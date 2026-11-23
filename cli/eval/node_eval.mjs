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
import { createRequire, Module } from 'node:module'
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { pathToFileURL } from 'node:url'

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

const adapter = await import(pathToFileURL(adapterPath).href)
const names = Object.keys(adapter.candidates)
const call = (fn, input) => {
  try {
    const out = fn(parse(write(input))) // a fresh copy per call
    return out === undefined ? { __undefined__: true } : out
  } catch (e) {
    return { __throws__: e && e.name ? e.name : String(e) }
  }
}
process.stdout.write(JSON.stringify({ candidates: names }) + '\n')
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
  const { input } = parse(line)
  const out = { reference: call(adapter.reference, input), candidates: {} }
  for (const n of names) out.candidates[n] = call(adapter.candidates[n], input)
  process.stdout.write(write(out) + '\n')
}
