// forge diff adapter (JavaScript) for the messy fuzz: the wrappers that take raw {cap, spent, amount} objects, and
// the proved rule. Inputs come from wallet_inputs.py. Test code, not proved. Node 22.18+ imports the .ts directly.
import { REFUSED, spendCoerces, spendSafe, spendThrows } from '../messy_wrappers.ts'

const { Wallet } = globalThis.forge.loadDafny('../verified/wallet-cap/js/wallet_cap.js', ['Wallet'])
const BigNumber = globalThis.forge.require(process.env.NODE_PATH.split(':')[0] + '/bignumber.js')
const big = (x) => new BigNumber(String(x))
const whole = (x) => typeof x === 'bigint' || Number.isSafeInteger(x)

export function reference(i) {
  const [next, outcome] = Wallet.__default.Spend(Wallet.State.create_Wallet(big(i.cap), big(i.spent)), big(i.amount))
  return { ok: outcome.is_Ok, spent: next.dtor_spent }
}

// The spec's answer for a value the proved rule does not take: refused, nothing spent. undefined: the rule takes it.
export function failClosed(i) {
  const ok = i && whole(i.cap) && whole(i.spent) && whole(i.amount) && big(0).lte(big(i.spent)) && big(i.spent).lte(big(i.cap))
  return ok ? undefined : REFUSED
}

// Each calls the wrapper with the raw value: no conversion here.
export const wrappers = { 'wrapper-safe': spendSafe, 'wrapper-throws': spendThrows, 'wrapper-coerces': spendCoerces }
