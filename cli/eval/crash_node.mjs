// Runner for `forge_crash.py` (CRASH-CHECK) on JavaScript / TypeScript boundary functions. Test tooling: NOT proved.
// Usage: node crash_node.mjs <project root> <targets.json>
//   targets.json: [{ "id": "...", "file": "rel/path.ts", "name": "exportName", "style": "express|fetch|lambda|plain",
//                    "params": ["a", "b"] }]
// First line out: {"ready": [ids], "missing": {id: reason}}. Then per line in: {"id": ..., "input": <json>} and per
// line out: {"ok": true} or {"throws": {"name", "message", "at": "file:line"}}.
// {"__undefined__": true} in an input becomes undefined, {"__nan__": true} becomes NaN.
// Calling styles: express (req, res, next) with the input as req.body; fetch (Request) with the input as the JSON
// body text; lambda (event, context) with the input as the event; plain: one argument per parameter, taken from the
// input object by parameter name.
import path from 'node:path'
import { readFileSync, realpathSync } from 'node:fs'
import { pathToFileURL, fileURLToPath } from 'node:url'

const root = realpathSync(path.resolve(process.argv[2]))
const targets = JSON.parse(readFileSync(process.argv[3], 'utf8'))
const CALL_MS = 1000
const fns = {}
const missing = {}
const mods = {}
for (const t of targets) {
  try {
    const abs = path.resolve(root, t.file)
    mods[abs] ??= await import(pathToFileURL(abs).href)
    const m = mods[abs]
    const f = t.name === 'default' ? m.default : (m[t.name] ?? m.default?.[t.name])
    if (typeof f !== 'function') missing[t.id] = `${t.name} is not an exported function`
    else fns[t.id] = { t, f }
  } catch (e) {
    missing[t.id] = `the module did not load: ${e && e.name}: ${String(e && e.message).slice(0, 200)}`
  }
}

const isUndef = x => x && typeof x === 'object' && !Array.isArray(x) && Object.keys(x).length === 1 && x.__undefined__ === true
const isNaNm = x => x && typeof x === 'object' && !Array.isArray(x) && Object.keys(x).length === 1 && x.__nan__ === true
const revive = x => {
  if (isUndef(x)) return undefined
  if (isNaNm(x)) return NaN
  if (Array.isArray(x)) return x.map(revive)
  if (x && typeof x === 'object') {
    const o = {}
    for (const k of Object.keys(x)) o[k] = revive(x[k])
    return o
  }
  return x
}

function fakeRes() {
  const res = { statusCode: 200, headers: {}, body: undefined, headersSent: false, locals: {} }
  const self = () => res
  Object.assign(res, {
    status(c) { res.statusCode = c; return res },
    sendStatus(c) { res.statusCode = c; res.headersSent = true; return res },
    json(b) { res.body = b; res.headersSent = true; return res },
    send(b) { res.body = b; res.headersSent = true; return res },
    end(b) { res.body = b; res.headersSent = true; return res },
    set: self, header: self, setHeader: self, type: self, append: self, cookie: self, clearCookie: self,
    redirect(...a) { res.headersSent = true; return res },
    code(c) { res.statusCode = c; return res }, // fastify reply
    writeHead(c) { res.statusCode = c; return res },
    write() { return true },
    getHeader() { return undefined },
  })
  return res
}

function argsFor(t, input) {
  const v = revive(input)
  if (t.style === 'express') {
    const req = { body: v, query: {}, params: {}, headers: { 'content-type': 'application/json' }, method: 'POST', url: '/', get: () => undefined, header: () => undefined }
    return [req, fakeRes(), () => undefined]
  }
  if (t.style === 'fetch') {
    // What arrives over HTTP is text: the input as JSON text (undefined: no body; a string: the text itself).
    const body = v === undefined ? undefined : typeof v === 'string' ? v : JSON.stringify(v)
    const init = { method: 'POST', headers: { 'content-type': 'application/json' } }
    if (body !== undefined) init.body = body
    return [new Request('http://localhost/', init), { params: {} }]
  }
  if (t.style === 'lambda') return [v, { functionName: 'crash-check', getRemainingTimeInMillis: () => 30000 }]
  const o = v && typeof v === 'object' && !Array.isArray(v) ? v : {}
  return (t.params.length ? t.params : ['input']).map(p => o[p])
}

function where(e) {
  const stack = String((e && e.stack) || '')
  for (const line of stack.split('\n').slice(1)) {
    const m = /\(?((?:file:\/\/)?\/[^():]+):(\d+):\d+\)?\s*$/.exec(line.trim())
    if (!m) continue
    let p = m[1]
    if (p.startsWith('file://')) { try { p = fileURLToPath(p) } catch { continue } }
    if (!p.startsWith(root + path.sep) || p.includes(`${path.sep}node_modules${path.sep}`)) continue
    return `${path.relative(root, p)}:${m[2]}`
  }
  return null
}

const verdict = e => ({ throws: { name: (e && e.name) || typeof e, message: String((e && e.message) ?? e).slice(0, 200), at: where(e) } })

async function run(id, input) {
  const { t, f } = fns[id]
  let out
  try {
    out = f(...argsFor(t, input))
  } catch (e) {
    return verdict(e)
  }
  if (out && typeof out.then === 'function') {
    let timer
    try {
      await Promise.race([out, new Promise((_, rej) => { timer = setTimeout(() => rej(Object.assign(new Error('no answer'), { name: '__hang__' })), CALL_MS) })])
    } catch (e) {
      if (e && e.name === '__hang__') return { hang: true }
      return verdict(e)
    } finally {
      clearTimeout(timer)
    }
  }
  return { ok: true }
}

// An unhandled rejection from work the handler started but did not await is a crash of the process in Node 15+.
let late = null
process.on('unhandledRejection', e => { late = e })
process.on('uncaughtException', e => { late = e })

process.stdout.write(JSON.stringify({ ready: Object.keys(fns), missing }) + '\n')
let buf = ''
process.stdin.setEncoding('utf8')
for await (const chunk of process.stdin) {
  buf += chunk
  let i
  while ((i = buf.indexOf('\n')) >= 0) {
    const line = buf.slice(0, i)
    buf = buf.slice(i + 1)
    if (!line.trim()) continue
    const msg = JSON.parse(line)
    late = null
    let out = await run(msg.id, msg.input)
    await new Promise(r => setImmediate(r))
    if (out.ok && late) out = verdict(late)
    process.stdout.write(JSON.stringify(out) + '\n')
  }
}
process.exit(0)
