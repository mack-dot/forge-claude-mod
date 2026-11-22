include "../spec/WalletSpec.dfy"

module Wallet refines WalletSpec {
  method Spend ... {
    if 0 < amount && s.spent + amount <= s.cap {
      next := Wallet(s.cap, s.spent + amount);
      outcome := Ok;
    } else {
      next := s;
      outcome := Rejected;
    }
  }
}
