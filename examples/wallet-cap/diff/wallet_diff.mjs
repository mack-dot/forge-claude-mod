// forge diff evaluator adapter (JavaScript): the shipped TypeScript wallet code, and the proved rule compiled to
// JavaScript. Inputs come from wallet_inputs.py. Test code, not proved. Node 22.18+ imports the .ts directly.
import { legacySpend } from '../legacy_wallet.ts'

const { Wallet } = globalThis.forge.loadDafny('../verified/wallet-cap/js/wallet_cap.js', ['Wallet'])
const BigNumber = globalThis.forge.require(process.env.NODE_PATH.split(':')[0] + '/bignumber.js')

// Inputs may hold BigInt values (integers above 2^53 arrive exactly); BigNumber takes them as strings, and the
// evaluator writes a BigNumber result back with all its digits.
const big = x => new BigNumber(String(x))

export function reference(i) {
  const [next, outcome] = Wallet.__default.Spend(Wallet.State.create_Wallet(big(i.cap), big(i.spent)), big(i.amount))
  return { ok: outcome.is_Ok, spent: next.dtor_spent }
}

export const candidates = {
  // The shipped TypeScript code takes plain numbers, as the app passes them.
  'legacy-ts': i => legacySpend(Number(i.cap), Number(i.spent), Number(i.amount)),
}
