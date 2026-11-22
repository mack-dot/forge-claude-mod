// Fuzz harness for the wallet-cap check. Dafny verifies it, then it is compiled and run in
// every build target. Each target must print exactly the same lines.
// `expect` is a runtime check in the compiled code: a failure stops the run.
include "../impl/Wallet.dfy"

module FuzzMain {
  import W = Wallet

  // Deterministic pseudo-random numbers (a linear congruential generator), same in every target.
  function Next(seed: nat): nat { (seed * 6364136223846793005 + 1442695040888963407) % 18446744073709551616 }

  method Try(cap: int, spent: int, amount: int)
    requires 0 <= spent <= cap
  {
    var s := W.Wallet(cap, spent);
    var next, outcome := W.Spend(s, amount);
    // The spec's clauses again, as runtime checks of the compiled code.
    expect 0 <= next.spent <= next.cap, "W1";
    expect next.cap == cap, "W2";
    if 0 < amount && spent + amount <= cap {
      expect outcome == W.Ok && next.spent == spent + amount, "W3";
    } else {
      expect outcome == W.Rejected && next == s, "W4";
    }
    print cap, " ", spent, " ", amount, " -> ", (if outcome == W.Ok then "Ok " else "Rejected "), next.spent, "\n";
  }

  method Main() {
    // Boundary values: zero, one, the cap and its neighbours, and the integer cliffs of
    // JavaScript (2^31, 2^32, 2^53) and of 64-bit machines (2^63, 2^64).
    var edges := [0, 1, 2, 4999, 5000, 5001, 2147483647, 2147483648, 4294967295, 4294967296,
                  9007199254740991, 9007199254740992, 9223372036854775807, 9223372036854775808,
                  18446744073709551615, 18446744073709551616];
    var amounts := [-9223372036854775809, -1, 0] + edges;
    var count := 0;
    for i := 0 to |edges| {
      for j := 0 to |edges| {
        if edges[j] <= edges[i] {
          for k := 0 to |amounts| {
            Try(edges[i], edges[j], amounts[k]);
            count := count + 1;
          }
        }
      }
    }
    var seed: nat := 20261122;
    for n := 0 to 2000 {
      seed := Next(seed);
      var cap := seed % 100000;
      seed := Next(seed);
      var spent := seed % (cap + 1);
      seed := Next(seed);
      var amount := (seed % 200001) as int - 100000;
      Try(cap, spent, amount);
      count := count + 1;
    }
    print "cases ", count, "\n";
  }
}
