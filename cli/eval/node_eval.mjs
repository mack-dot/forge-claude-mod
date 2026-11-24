// Forge diff evaluator for JavaScript / TypeScript: speaks the line protocol of cli/forge_diff.py.
// Usage: node node_eval.mjs <adapter.mjs>
// The adapter exports reference(input) and candidates { name: input => output }. Before it loads, the runner
// sets globalThis.forge = { loadDafny, require }: forge.loadDafny('<emitted .js, relative to the adapter>',
// ['ModuleName']) returns the compiled proved Dafny modules (Dafny's JavaScript output exports nothing itself).
// TypeScript files can be imported directly by their .ts path (Node 22.18 or newer). Test code: NOT proved.
//
// Big integers: JSON.parse rounds integers above 2^53. Here an integer that is not a safe JavaScript number
// arrives as a BigInt (exact), and a BigInt or a bignumber.js value in an output is written back with all its
// digits. Adapters therefore see small integers as numbers and big ones as BigInt.
//
// Messy inputs (the unproved wrapper that turns app/JSON values into Dafny values): the adapter may export
// wrapper(input) or wrappers { name: input => output }, which call the app's REAL wrapper with the raw values, and
// failClosed(input), the spec's fail-closed answer for a value the proved rule does not take (return undefined when
// the proved rule takes it). Messy inputs go only to the wrappers; {"__undefined__": true} in them becomes undefined.
//
// Steered runs (cli/forge_steer.py, node_steer.mjs): with FORGE_STEER_FILES set, a plain answer may carry
// "novel": [n, ...], the numbers of plain inputs (0, 1, ...) that reached a block of a watched file first.
import { createRequire, Module } from 'node:module'
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { pathToFileURL } from 'node:url'

// The protocol owns the real stdout. Code under test that logs (console.log, process.stdout.write) writes to stderr
// instead: before this, one console.log in the code under test broke the line protocol.
const proto = process.stdout.write.bind(process.stdout)
process.stdout.write = process.stderr.write.bind(process.stderr)

const adapterPath = path.resolve(process.argv[2])

function loadDafny(file, names) {
  const abs = path.resolve(path.dirname(adapterPath), file)
  const m = new Module(abs)
  m.filename = abs
  m.paths = [...(process.env.NODE_PATH || '').split(path.delimiter).filter(Boolean), ...Module._nodeModulePaths(path.dirname(abs))]
  m._compile(`${readFileSync(abs, 'utf8')}\nmodule.exports = { _dafny, ${names.join(', ')} };`, abs)
  return m.exports
}
globalThis.forge = { loadDafny, require: createRequire(import.meta.url) }

if (typeof JSON.rawJSON !== 'function') {
  process.stderr.write('forge: this Node cannot pass big integers exactly (needs Node 22 or newer)\n')
  process.exit(3)
}
const parse = text =>
  JSON.parse(text, (k, x, ctx) => (typeof x === 'number' && !Number.isSafeInteger(x) && ctx && /^-?\d+$/.test(ctx.source) ? BigInt(ctx.source) : x))
// bignumber.js values carry their own toJSON (a string), which runs before any replacer: turn them into exact
// BigInt (integers) or numbers first, then write BigInt with all its digits.
const plain = x => {
  if (x && x._isBigNumber === true) return x.isInteger() ? BigInt(x.toFixed()) : x.toNumber()
  if (Array.isArray(x)) return x.map(plain)
  if (x && typeof x === 'object' && Object.getPrototypeOf(x) === Object.prototype)
    return Object.fromEntries(Object.entries(x).map(([k, v]) => [k, plain(v)]))
  return x
}
const write = obj => JSON.stringify(plain(obj), (k, x) => (typeof x === 'bigint' ? JSON.rawJSON(x.toString()) : x))

// Before the adapter loads, so the watched files' blocks are counted from their first run.
const steer = process.env.FORGE_STEER_FILES
  ? (await import('./node_steer.mjs')).start(JSON.parse(process.env.FORGE_STEER_FILES), process.env.FORGE_STEER_OUT,
    Number(process.env.FORGE_STEER_BATCH || 16))
  : null
let seq = 0
const adapter = await import(pathToFileURL(adapterPath).href)

// Trace mode (cli/forge_trace.py): the adapter exports trace = { reference: machine, candidates: { name: machine } },
// machine = { init(), step(state, event, now) -> { ok: state } | { reject: reason }, view(state) -> JSON,
// save(state)?, load(saved)? }. A rejected step keeps the state object it got (so a step that changes it before it
// rejects shows up). "restart" runs load(save(state)) through JSON text; without save/load it changes nothing.
if (adapter.trace) {
  const machines = { reference: adapter.trace.reference, ...(adapter.trace.candidates || {}) }
  const tnames = Object.keys(adapter.trace.candidates || {})
  const errName = e => (e && e.name ? e.name : String(e))
  const run = (m, steps) => {
    const out = []
    let s
    try {
      s = m.init()
    } catch (e) {
      return [{ __throws__: errName(e) }]
    }
    for (const st of steps) {
      try {
        let v
        if (st.restart) {
          if (m.save && m.load) s = m.load(parse(write(m.save(s))))
          v = 'restart'
        } else {
          const r = m.step(s, parse(write(st.event)), st.now)
          if (r && typeof r === 'object' && 'ok' in r) {
            s = r.ok
            v = 'ok'
          } else if (r && typeof r === 'object' && 'reject' in r) {
            v = 'reject:' + String(r.reject)
          } else {
            throw new TypeError('step returned neither { ok } nor { reject }')
          }
        }
        out.push({ v, s: parse(write(m.view(s))) })
      } catch (e) {
        out.push({ __throws__: errName(e) })
        break
      }
    }
    return out
  }
  proto(JSON.stringify({ candidates: tnames, trace: true }) + '\n')
  process.stdin.setEncoding('utf8')
  let buf = ''
  for await (const chunk of process.stdin) {
    buf += chunk
    let i
    while ((i = buf.indexOf('\n')) >= 0) {
      const line = buf.slice(0, i)
      buf = buf.slice(i + 1)
      if (!line.trim()) continue
      const { traces } = parse(line)
      const results = traces.map(steps => {
        const candidates = {}
        for (const n of tnames) candidates[n] = run(machines[n], steps)
        return { reference: run(machines.reference, steps), candidates }
      })
      proto(write({ results }) + '\n')
    }
  }
  process.exit(0)
}
const wrappers = adapter.wrappers ? { ...adapter.wrappers } : adapter.wrapper ? { wrapper: adapter.wrapper } : {}
const all = { ...(adapter.candidates || {}), ...wrappers }
const names = Object.keys(all)
const messyNames = Object.keys(wrappers)
const isUndef = (x) => x && typeof x === 'object' && !Array.isArray(x) && Object.keys(x).length === 1 && x.__undefined__ === true
const revive = (x) => {
  if (Array.isArray(x)) return x.map((v) => (isUndef(v) ? undefined : revive(v)))
  if (x && typeof x === 'object') {
    for (const k of Object.keys(x)) x[k] = isUndef(x[k]) ? undefined : revive(x[k])
  }
  return x
}
const call = (fn, input, messy) => {
  try {
    const copy = parse(write(input)) // a fresh copy per call
    const out = fn(messy ? revive(copy) : copy)
    return out === undefined ? { __undefined__: true } : out
  } catch (e) {
    return { __throws__: e && e.name ? e.name : String(e) }
  }
}
proto(JSON.stringify({ candidates: names, messy: messyNames, undefined: true }) + '\n')
// Split on "\n" only: readline also splits at U+2028 and U+2029, which may appear inside JSON strings.
async function* lines(stream) {
  let buf = ''
  for await (const chunk of stream) {
    buf += chunk
    let i
    while ((i = buf.indexOf('\n')) >= 0) {
      yield buf.slice(0, i)
      buf = buf.slice(i + 1)
    }
  }
  if (buf) yield buf
}
process.stdin.setEncoding('utf8')
for await (const line of lines(process.stdin)) {
  if (!line.trim()) continue
  const { input, messy } = parse(line)
  if (messy) {
    let reference = adapter.failClosed ? call(adapter.failClosed, input, true) : { __undefined__: true }
    if (isUndef(reference)) reference = call(adapter.reference, input, true)
    const out = { reference, candidates: {} }
    for (const n of messyNames) out.candidates[n] = call(wrappers[n], input, true)
    proto(write(out) + '\n')
    continue
  }
  const out = { reference: call(adapter.reference, input), candidates: {} }
  for (const n of names) out.candidates[n] = call(all[n], input)
  if (steer) {
    const novel = steer.plain(seq++, () => { for (const n of names) call(all[n], input) })
    if (novel.length) out.novel = novel
  }
  proto(write(out) + '\n')
}
if (steer) steer.finish()
