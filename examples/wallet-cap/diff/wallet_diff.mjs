// forge diff evaluator adapter (JavaScript): the shipped TypeScript wallet code, and the proved rule compiled to
// JavaScript. Inputs come from wallet_inputs.py. Test code, not proved. Node 22.18+ imports the .ts directly.
import { legacySpend } from '../legacy_wallet.ts'

const { Wallet } = globalThis.forge.loadDafny('../verified/wallet-cap/js/wallet_cap.js', ['Wallet'])
const BigNumber = globalThis.forge.require(process.env.NODE_PATH.split(':')[0] + '/bignumber.js')

export function reference(i) {
  const [next, outcome] = Wallet.__default.Spend(Wallet.State.create_Wallet(new BigNumber(i.cap), new BigNumber(i.spent)), new BigNumber(i.amount))
  return { ok: outcome.is_Ok, spent: next.dtor_spent.toNumber() }
}

export const candidates = {
  'legacy-ts': i => legacySpend(i.cap, i.spent, i.amount),
}
