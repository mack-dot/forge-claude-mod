// Wallet with a spending cap, in whole cents.
abstract module WalletSpec {
  datatype Outcome = Ok | Rejected
  datatype State = Wallet(cap: int, spent: int)

  predicate Valid(s: State) { 0 <= s.spent <= s.cap }

  method Spend(s: State, amount: int) returns (next: State, outcome: Outcome)
    requires Valid(s)
    // W1: the total spent never goes over the cap.
    ensures Valid(next)
    // W2: the cap never changes.
    ensures next.cap == s.cap
    // W3: a positive amount that fits is accepted and added exactly.
    ensures (0 < amount && s.spent + amount <= s.cap) ==>
      outcome == Ok && next.spent == s.spent + amount
    // W4: every other amount is rejected with no change.
    ensures !(0 < amount && s.spent + amount <= s.cap) ==>
      outcome == Rejected && next == s
}
