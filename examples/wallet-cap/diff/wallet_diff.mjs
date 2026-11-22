// forge diff adapter (JavaScript): the shipped TypeScript wallet code against the proved rule compiled to
// JavaScript. Test code, not proved. Node 22.18+ / 23.6+ runs the .ts import directly (type stripping).
import { legacySpend } from '../legacy_wallet.ts'

const { Wallet } = globalThis.forge.loadDafny('../verified/wallet-cap/js/wallet_cap.js', ['Wallet'])
const BigNumber = globalThis.forge.require(process.env.NODE_PATH.split(':')[0] + '/bignumber.js')

export function generate(rng) {
  const cap = rng.num(0, 10000)
  const spent = rng.num(0, cap)
  const room = cap - spent
  const amount = rng.pick([rng.num(-200, 12000), room, room + 1, room - 1, 0, -1, 1])
  return { cap, spent, amount }
}

export const inDomain = i => 0 <= i.spent && i.spent <= i.cap

export function reference(i) {
  const [next, outcome] = Wallet.__default.Spend(Wallet.State.create_Wallet(new BigNumber(i.cap), new BigNumber(i.spent)), new BigNumber(i.amount))
  return { ok: outcome.is_Ok, spent: next.dtor_spent.toNumber() }
}

export const candidates = {
  'legacy-ts': i => legacySpend(i.cap, i.spent, i.amount),
}

// The person's recorded decisions (.forge/decisions/wallet-cap.md), run as fixed inputs on every run.
export const decisions = [
  { id: 'D1 exact cap allowed', input: { cap: 5000, spent: 2000, amount: 3000 } },
  { id: 'D2 no partial spend', input: { cap: 5000, spent: 4000, amount: 2000 } },
  { id: 'D3 zero rejected', input: { cap: 5000, spent: 0, amount: 0 } },
  { id: 'D4 negative rejected', input: { cap: 5000, spent: 3000, amount: -1000 } },
]
