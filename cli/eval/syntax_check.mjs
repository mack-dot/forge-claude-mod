// Parses each file given (TypeScript or JavaScript) without running it. Exit 0: all parse; 1: syntax errors (one
// "path: message" line each); 3: this Node cannot parse TypeScript (stripTypeScriptTypes needs Node 22.13 or later),
// so nothing was checked. A sanity step, not a proof.
import { readFileSync } from 'node:fs'
const mod = await import('node:module')
if (typeof mod.stripTypeScriptTypes !== 'function') process.exit(3)
let bad = 0
for (const f of process.argv.slice(2)) {
  try { mod.stripTypeScriptTypes(readFileSync(f, 'utf8'), { mode: 'transform' }) }
  catch (e) { bad = 1; console.log(`${f}: ${String(e.message).split('\n').slice(0, 3).join(' ')}`) }
}
process.exit(bad)
