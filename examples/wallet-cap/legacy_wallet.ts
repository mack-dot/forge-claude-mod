// The wallet check as a TypeScript service shipped it before Forge. Kept on purpose, with one bug, so that
// `forge_check.py diff` has something real to find. Do not fix it: the tests depend on this bug.
export interface Result {
  ok: boolean
  spent: number
}

export function legacySpend(cap: number, spent: number, amount: number): Result {
  if (spent + amount <= cap) return { ok: true, spent: spent + amount } // bug: a negative amount is accepted
  return { ok: false, spent }
}
