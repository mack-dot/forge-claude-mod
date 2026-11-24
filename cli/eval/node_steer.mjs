// Per-input coverage feedback for the Node evaluator (node_eval.mjs) in a steered diff run (cli/forge_steer.py).
// Turned on by FORGE_STEER_FILES (JSON list of watched files) and FORGE_STEER_OUT (a folder; at exit the blocks of
// the whole run are written there in NODE_V8_COVERAGE's format, so cli/forge_coverage.py can read them).
//
// V8 block coverage through node:inspector, in this thread (the session answers synchronously). A take costs
// about 1 ms, so it is not done after every input: every FORGE_STEER_BATCH plain inputs (default 16) one take
// shows whether the batch reached a block no earlier plain input reached; only then is each input of the batch
// run again on its own to find which one did ("novel"). Messy inputs count for the end-of-run report only.
// A take resets V8's counters, so this cannot run together with NODE_V8_COVERAGE. Test tooling: NOT proved.
import { Session } from 'node:inspector'
import { realpathSync, writeFileSync, mkdirSync } from 'node:fs'
import { fileURLToPath, pathToFileURL } from 'node:url'
import path from 'node:path'

export function start(files, outDir, batchSize) {
  const watched = new Set(files.map(f => { try { return realpathSync(f) } catch { return path.resolve(f) } }))
  const s = new Session()
  s.connect()
  const post = (m, p) => {
    let r, e
    s.post(m, p, (err, res) => { e = err; r = res })
    if (e) throw e
    return r
  }
  post('Profiler.enable')
  post('Profiler.startPreciseCoverage', { callCount: true, detailed: true })
  const urlOk = new Map() // url -> real path or null
  const scripts = new Map() // real path -> { url, fns: Map(fnKey -> { name, keys: [rangeKey...] }), known: Map(rangeKey -> [a, b]) }
  const ever = new Set() // "path|a-b" blocks reached in the whole run
  let seen = new Set() // reached by some earlier plain input
  const real = url => {
    if (!urlOk.has(url)) {
      let rp = null
      if (url.startsWith('file:')) { try { rp = realpathSync(fileURLToPath(url)) } catch { rp = null } }
      urlOk.set(url, rp && watched.has(rp) ? rp : null)
    }
    return urlOk.get(url)
  }
  // One take: the set of blocks (of watched files) that ran since the last take.
  const take = () => {
    const got = new Set()
    for (const sc of post('Profiler.takePreciseCoverage').result) {
      const rp = real(sc.url)
      if (!rp) continue
      let entry = scripts.get(rp)
      if (!entry) scripts.set(rp, (entry = { url: sc.url, fns: new Map(), known: new Map() }))
      const ranges = []
      for (const f of sc.functions) {
        const fk = `${f.ranges[0].startOffset}-${f.ranges[0].endOffset}`
        let fe = entry.fns.get(fk)
        if (!fe) entry.fns.set(fk, (fe = { name: f.functionName, keys: [] }))
        for (const r of f.ranges) {
          const k = `${r.startOffset}-${r.endOffset}`
          if (!entry.known.has(k)) { entry.known.set(k, [r.startOffset, r.endOffset]); fe.keys.push(k) }
          ranges.push(r)
        }
      }
      // V8 lists a block only where its count differs from the code around it: a known block ran in this take if
      // the smallest range of this take that holds its start has a count above 0.
      for (const [k, [a]] of entry.known) {
        let best = null
        for (const r of ranges) if (r.startOffset <= a && a < r.endOffset && (!best || r.endOffset - r.startOffset < best.endOffset - best.startOffset)) best = r
        if (best && best.count > 0) got.add(`${rp}|${k}`)
      }
    }
    for (const k of got) ever.add(k)
    return got
  }
  let batch = []
  const flush = () => {
    if (!batch.length) return []
    const all = take()
    const novel = []
    if ([...all].some(k => !seen.has(k))) {
      for (const b of batch) {
        b.rerun()
        const one = take()
        if ([...one].some(k => !seen.has(k))) novel.push(b.seq)
        for (const k of one) seen.add(k)
      }
    }
    for (const k of all) seen.add(k)
    batch = []
    return novel
  }
  return {
    // After a plain input ran: returns the numbers of the plain inputs found novel (possibly earlier ones).
    plain(seq, rerun) {
      batch.push({ seq, rerun })
      return batch.length >= batchSize ? flush() : []
    },
    finish() {
      flush()
      take()
      const result = []
      for (const [rp, e] of scripts) {
        const functions = []
        for (const [, fe] of e.fns) {
          functions.push({ functionName: fe.name, isBlockCoverage: true, ranges: fe.keys.map(k => {
            const [a, b] = e.known.get(k)
            return { startOffset: a, endOffset: b, count: ever.has(`${rp}|${k}`) ? 1 : 0 }
          }) })
        }
        result.push({ scriptId: '0', url: e.url || pathToFileURL(rp).href, functions })
      }
      mkdirSync(outDir, { recursive: true })
      writeFileSync(path.join(outDir, 'forge-steer.json'), JSON.stringify({ result }))
    },
  }
}
