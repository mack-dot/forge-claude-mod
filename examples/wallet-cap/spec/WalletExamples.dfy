// The person's answers from the /forge add conversation, as checks against the abstract spec.
// Each method verifies only if the spec forces exactly the answer given.
// See .forge/decisions/wallet-cap.md for the questions in plain words.
include "WalletSpec.dfy"

abstract module WalletExamples {
  import S : WalletSpec

  // D1: spending exactly up to the cap is allowed. Cap $50.00, spent $20.00, spend $30.00.
  method D1_ExactCapAllowed() {
    var next, outcome := S.Spend(S.Wallet(5000, 2000), 3000);
    assert outcome == S.Ok && next.spent == 5000;
  }

  // D2: no partial spend. Cap $50.00, spent $40.00, spend $20.00: rejected, not cut to $10.00.
  method D2_NoPartialSpend() {
    var next, outcome := S.Spend(S.Wallet(5000, 4000), 2000);
    assert outcome == S.Rejected && next.spent == 4000;
  }

  // D3: a zero amount is rejected, not treated as a no-op success.
  method D3_ZeroRejected() {
    var next, outcome := S.Spend(S.Wallet(5000, 0), 0);
    assert outcome == S.Rejected && next.spent == 0;
  }

  // D4: a negative amount (a refund in disguise) is rejected.
  method D4_NegativeRejected() {
    var next, outcome := S.Spend(S.Wallet(5000, 3000), -1000);
    assert outcome == S.Rejected && next.spent == 3000;
  }
}
