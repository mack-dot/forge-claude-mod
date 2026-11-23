// Three wrappers around the proved wallet rule that take a RAW object {cap, spent, amount}, as an app gets it from
// JSON. THIS FILE IS NOT PROVED. Two are planted on purpose so that the messy fuzz of `forge_check.py diff` has
// something real to find. Do not fix them: the tests depend on these bugs.
//   spendSafe     never throws: a value that is not a whole number, or 0 <= spent <= cap that does not hold, fails closed.
//   spendThrows   bug: reads amount.valueOf() before it checks the type, so amount undefined or null throws.
//   spendCoerces  bug: turns the text "5" into the number 5 (the proved rule never sees text).
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'

const require = createRequire(import.meta.url)
const EMITTED = new URL('./verified/wallet-cap/js/wallet_cap.js', import.meta.url)
// eslint-disable-next-line @typescript-eslint/no-explicit-any -- the emitted code is untyped
type Dafny = any
const { Wallet } = new Function('require', `${readFileSync(EMITTED, 'utf8')}\nreturn { _dafny, Wallet };`)(require) as { Wallet: Dafny }
const BigNumber = require('bignumber.js')

export interface Result { ok: boolean; spent: bigint | number | null }
export const REFUSED: Result = { ok: false, spent: null } // fail closed: nothing spent, no new state

const whole = (x: unknown): x is number | bigint => typeof x === 'bigint' || Number.isSafeInteger(x)

function proved(cap: number | bigint, spent: number | bigint, amount: number | bigint): Result {
  const big = (x: number | bigint) => new BigNumber(String(x))
  if (!(big(0).lte(big(spent)) && big(spent).lte(big(cap)))) return REFUSED
  const [next, outcome] = Wallet.__default.Spend(Wallet.State.create_Wallet(big(cap), big(spent)), big(amount))
  return { ok: outcome.is_Ok === true, spent: BigInt(next.dtor_spent.toFixed()) }
}

export function spendSafe(raw: unknown): Result {
  if (!raw || typeof raw !== 'object') return REFUSED
  const { cap, spent, amount } = raw as Record<string, unknown>
  if (!whole(cap) || !whole(spent) || !whole(amount)) return REFUSED
  return proved(cap, spent, amount)
}

export function spendThrows(raw: { cap: unknown; spent: unknown; amount: unknown }): Result {
  if (!whole(raw.cap) || !whole(raw.spent)) return REFUSED
  const amount = (raw.amount as number).valueOf() // bug: TypeError when amount is undefined or null
  if (!whole(amount)) return REFUSED
  return proved(raw.cap, raw.spent, amount)
}

export function spendCoerces(raw: { cap: unknown; spent: unknown; amount: unknown }): Result {
  const amount = typeof raw.amount === 'string' && /^-?\d+$/.test(raw.amount) ? Number(raw.amount) : raw.amount // bug
  if (!whole(raw.cap) || !whole(raw.spent) || !whole(amount)) return REFUSED
  return proved(raw.cap, raw.spent, amount)
}
