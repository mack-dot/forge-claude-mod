// Micro-benchmark: wallet Spend through the old loader (the emitted script evaluated with a require for bignumber.js,
// as cli/eval/node_eval.mjs does) and through the generated ES module. 2,000 entries x 10 calls = one pass.
// Warm (3 passes first), then the median of 5 passes, in ms. Also checks that both give the same answers.
// Usage: NODE_PATH=<folder with bignumber.js> node bench_wallet.mjs <verified/wallet-cap/js folder>. Test code.
import { createRequire, Module } from 'node:module'
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { pathToFileURL } from 'node:url'

const dir = path.resolve(process.argv[2])
const abs = path.join(dir, 'wallet_cap.js')
const m = new Module(abs)
m.filename = abs
m.paths = [...(process.env.NODE_PATH || '').split(path.delimiter).filter(Boolean)]
m._compile(`${readFileSync(abs, 'utf8')}\nmodule.exports = { _dafny, Wallet };`, abs)
const { Wallet } = m.exports
const BigNumber = createRequire(abs)(path.join(process.env.NODE_PATH.split(path.delimiter)[0], 'bignumber.js'))
const M = await import(pathToFileURL(path.join(dir, 'wallet_cap.mjs')).href)

let seed = 7
const rand = n => { seed = (Math.imul(seed, 1103515245) + 12345) >>> 0; return (seed >>> 8) % n }
const entries = Array.from({ length: 2000 }, () => { const cap = rand(10000); const spent = rand(cap + 1); return { cap, spent, amounts: Array.from({ length: 10 }, () => rand(12000) - 200) } })

const viaOld = e => { const out = []; for (const a of e.amounts) { const [n, o] = Wallet.__default.Spend(Wallet.State.create_Wallet(new BigNumber(e.cap), new BigNumber(e.spent)), new BigNumber(a)); out.push(o.is_Ok, n.dtor_spent.toNumber()) } return out }
const viaNew = e => { const out = []; for (const a of e.amounts) { const [n, o] = M.Spend(M.State.create_Wallet(M.toDafnyInt(e.cap), M.toDafnyInt(e.spent)), M.toDafnyInt(a)); out.push(o.is_Ok, M.fromDafnyInt(n.dtor_spent)) } return out }
const pass = f => { let last; for (const e of entries) last = f(e); return last }
let differ = 0
for (const e of entries) if (JSON.stringify(viaOld(e)) !== JSON.stringify(viaNew(e))) differ++
const median = xs => [...xs].sort((a, b) => a - b)[Math.floor(xs.length / 2)]
const time = f => { for (let i = 0; i < 3; i++) pass(f); const runs = []; for (let r = 0; r < 5; r++) { const t = performance.now(); pass(f); runs.push(+(performance.now() - t).toFixed(2)) } return { median: median(runs), runs } }
const order = process.argv[3] === 'new-first' ? ['new', 'old'] : ['old', 'new']
const res = {}
for (const k of order) res[k] = time(k === 'old' ? viaOld : viaNew)
console.log(JSON.stringify({ calls_per_pass: 20000, entries_differ: differ, ...res }))
