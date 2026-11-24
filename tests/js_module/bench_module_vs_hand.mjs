// Micro-benchmark: wallet Spend through the generated ES module (forge build's js target: wallet_cap.mjs, no
// `new Function`, no eval) and through a hand-written TypeScript equivalent (wallet_hand.ts, number and bigint).
// 2,000 entries x 10 calls = one pass; warm (3 passes), then the median of 5 passes, in ms. Checks that all agree.
// Run it with --disallow-code-generation-from-strings to show the module needs no code generation from strings.
// Usage: node --disallow-code-generation-from-strings bench_module_vs_hand.mjs <verified/wallet-cap/js folder>
import path from 'node:path'
import { pathToFileURL } from 'node:url'

const dir = path.resolve(process.argv[2])
const M = await import(pathToFileURL(path.join(dir, 'wallet_cap.mjs')).href)
const H = await import(new URL('./wallet_hand.ts', import.meta.url).href)
let codegen = 'allowed'
try { new Function('return 1')() } catch (e) { codegen = `refused (${e.name})` }

let seed = 7
const rand = n => { seed = (Math.imul(seed, 1103515245) + 12345) >>> 0; return (seed >>> 8) % n }
const entries = Array.from({ length: 2000 }, () => { const cap = rand(10000); const spent = rand(cap + 1); return { cap, spent, amounts: Array.from({ length: 10 }, () => rand(12000) - 200) } })

const viaModule = e => { const out = []; for (const a of e.amounts) { const [n, o] = M.Spend(M.State.create_Wallet(M.toDafnyInt(e.cap), M.toDafnyInt(e.spent)), M.toDafnyInt(a)); out.push(o.is_Ok, M.fromDafnyInt(n.dtor_spent)) } return out }
const viaNumber = e => { const out = []; for (const a of e.amounts) { const r = H.spendNumber(e.cap, e.spent, a); out.push(r.ok, r.spent) } return out }
const viaBigint = e => { const out = []; for (const a of e.amounts) { const r = H.spendBigint(BigInt(e.cap), BigInt(e.spent), BigInt(a)); out.push(r.ok, Number(r.spent)) } return out }
const pass = f => { let last; for (const e of entries) last = f(e); return last }
let differ = 0
for (const e of entries) {
  const a = JSON.stringify(viaModule(e))
  if (a !== JSON.stringify(viaNumber(e)) || a !== JSON.stringify(viaBigint(e))) differ++
}
const median = xs => [...xs].sort((a, b) => a - b)[Math.floor(xs.length / 2)]
const time = f => { for (let i = 0; i < 3; i++) pass(f); const runs = []; for (let r = 0; r < 5; r++) { const t = performance.now(); pass(f); runs.push(+(performance.now() - t).toFixed(2)) } return { median_ms: median(runs), runs } }
const res = { module: time(viaModule), hand_number: time(viaNumber), hand_bigint: time(viaBigint) }
console.log(JSON.stringify({ node: process.version, calls_per_pass: 20000, entries_differ: differ, code_generation_from_strings: codegen, ...res }))
