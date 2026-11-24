// The wallet check as a TypeScript service shipped it before Forge. Kept on purpose, with three bugs, so that
// `forge_check.py diff` has something real to find. Do not fix it: the tests depend on this bug.
export interface Result {
  ok: boolean
  spent: number
}

export function legacySpend(cap: number, spent: number, amount: number): Result {
  // planted bugs: (1) reaching the cap exactly is rejected (< instead of <=); (2) a zero amount and (3) a negative
  // amount are accepted. Plain numbers also lose precision above 2^53 (not planted; the fuzz finds it).
  if (spent + amount < cap) return { ok: true, spent: spent + amount }
  return { ok: false, spent }
}
