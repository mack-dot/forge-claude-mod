// forge diff adapter used by tests/run_tests.py: the old loader (the emitted script, evaluated with a require for
// bignumber.js) is the reference; the generated ES module is the candidate. FORGE_TEST_MODULE names the module
// (the built one, or an edited copy for the negative control); FORGE_TEST_EMITTED names the emitted script.
// Test code, not proved.
import { pathToFileURL } from 'node:url'

const { Wallet } = globalThis.forge.loadDafny(process.env.FORGE_TEST_EMITTED, ['Wallet'])
const M = await import(pathToFileURL(process.env.FORGE_TEST_MODULE).href)
const BigNumber = globalThis.forge.require(process.env.NODE_PATH.split(':')[0] + '/bignumber.js')
const big = x => new BigNumber(String(x))

export function reference(i) {
  const [next, outcome] = Wallet.__default.Spend(Wallet.State.create_Wallet(big(i.cap), big(i.spent)), big(i.amount))
  return { ok: outcome.is_Ok, spent: next.dtor_spent }
}

export const candidates = {
  'generated-module': i => {
    const [next, outcome] = M.Spend(M.State.create_Wallet(M.toDafnyInt(i.cap), M.toDafnyInt(i.spent)), M.toDafnyInt(i.amount))
    return { ok: outcome.is_Ok, spent: M.fromDafnyInt(next.dtor_spent) }
  },
}
