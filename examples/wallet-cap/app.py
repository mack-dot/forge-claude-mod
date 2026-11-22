"""A small app that calls the built wallet code. The file verified/ is emitted by
`forge_check.py build`; never edit it by hand.

This wrapper is ordinary, unproved code: keep it thin. It turns plain Python values into
the generated types and back."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'verified', 'wallet-cap', 'py', 'wallet_cap-py'))
import Wallet  # noqa: E402  (the emitted, proved-in-the-model module)


def spend(cap_cents: int, spent_cents: int, amount_cents: int):
    """Returns (accepted, new_spent_cents). The proved rule needs 0 <= spent <= cap."""
    for value in (cap_cents, spent_cents, amount_cents):
        if type(value) is not int:
            raise TypeError('whole cents only')
    if not 0 <= spent_cents <= cap_cents:
        raise ValueError('need 0 <= spent <= cap')
    state, outcome = Wallet.default__.Spend(Wallet.State_Wallet(cap_cents, spent_cents), amount_cents)
    return outcome.is_Ok, state.spent


if __name__ == '__main__':
    print(spend(5000, 0, 2000))     # (True, 2000)
    print(spend(5000, 2000, 3000))  # (True, 5000): exactly the cap is allowed
    print(spend(5000, 5000, 1))     # (False, 5000): over the cap is rejected
