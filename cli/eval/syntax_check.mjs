import { stripTypeScriptTypes } from 'node:module'
import { readFileSync } from 'node:fs'
let bad = 0
for (const f of process.argv.slice(2)) {
  try { stripTypeScriptTypes(readFileSync(f, 'utf8'), { mode: 'transform' }) }
  catch (e) { bad = 1; console.log(`${f}: ${String(e.message).split('\n').slice(0, 3).join(' ')}`) }
}
process.exit(bad)
