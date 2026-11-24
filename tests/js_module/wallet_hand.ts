// A hand-written TypeScript equivalent of the example rule (examples/wallet-cap/spec/WalletSpec.dfy), for the
// benchmark in bench_module_vs_hand.mjs only. NOT proved. Two versions: plain numbers (exact below 2^53 only) and
// bigint (exact everywhere, like the Dafny int).
export function spendNumber(cap: number, spent: number, amount: number): { ok: boolean; spent: number } {
  if (0 < amount && spent + amount <= cap) return { ok: true, spent: spent + amount }
  return { ok: false, spent }
}

export function spendBigint(cap: bigint, spent: bigint, amount: bigint): { ok: boolean; spent: bigint } {
  if (0n < amount && spent + amount <= cap) return { ok: true, spent: spent + amount }
  return { ok: false, spent }
}
