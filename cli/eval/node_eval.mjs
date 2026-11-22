// Forge diff evaluator for JavaScript / TypeScript: speaks the line protocol of cli/forge_diff.py.
// Usage: node node_eval.mjs <adapter.mjs>
// The adapter exports reference(input) and candidates { name: input => output }. Before it loads, the runner
// sets globalThis.forge = { loadDafny, require }: forge.loadDafny('<emitted .js, relative to the adapter>',
// ['ModuleName']) returns the compiled proved Dafny modules (Dafny's JavaScript output exports nothing itself).
// TypeScript files can be imported directly by their .ts path (Node 22.18 or newer). Test code: NOT proved.
import { createRequire, Module } from 'node:module'
import { readFileSync } from 'node:fs'
import { createInterface } from 'node:readline'
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

const adapter = await import(pathToFileURL(adapterPath).href)
const names = Object.keys(adapter.candidates)
const call = (fn, input) => {
  try {
    const out = fn(structuredClone(input))
    return out === undefined ? { __undefined__: true } : out
  } catch (e) {
    return { __throws__: e && e.name ? e.name : String(e) }
  }
}
process.stdout.write(JSON.stringify({ candidates: names }) + '\n')
for await (const line of createInterface({ input: process.stdin, crlfDelay: Infinity })) {
  if (!line.trim()) continue
  const { input } = JSON.parse(line)
  const out = { reference: call(adapter.reference, input), candidates: {} }
  for (const n of names) out.candidates[n] = call(adapter.candidates[n], input)
  process.stdout.write(JSON.stringify(out) + '\n')
}
