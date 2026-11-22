"""forge diff adapter (Python): the shipped wallet code and the app's wrapper against the proved rule.
Test code, not proved."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'verified', 'wallet-cap', 'py', 'wallet_cap-py'))
import Wallet  # noqa: E402  the compiled proved rule
import app  # noqa: E402  the wrapper the app calls
import legacy_wallet  # noqa: E402  the code shipped before Forge


def generate(rng):
    cap = rng.num(0, 10000)
    spent = rng.num(0, cap)
    room = cap - spent
    amount = rng.pick([rng.num(-200, 12000), room, room + 1, room - 1, 0, -1, 1])
    return {'cap': cap, 'spent': spent, 'amount': amount}


def in_domain(i):  # the proved rule's precondition: 0 <= spent <= cap
    return 0 <= i['spent'] <= i['cap']


def reference(i):
    nxt, outcome = Wallet.default__.Spend(Wallet.State_Wallet(i['cap'], i['spent']), i['amount'])
    return {'ok': bool(outcome.is_Ok), 'spent': int(nxt.spent)}


def _pair(t): return {'ok': bool(t[0]), 'spent': int(t[1])}


candidates = {
    'app-wrapper': lambda i: _pair(app.spend(i['cap'], i['spent'], i['amount'])),
    'legacy': lambda i: _pair(legacy_wallet.legacy_spend(i['cap'], i['spent'], i['amount'])),
}


# The person's recorded decisions (.forge/decisions/wallet-cap.md), run as fixed inputs on every run.
decisions = [
    {'id': 'D1 exact cap allowed', 'input': {'cap': 5000, 'spent': 2000, 'amount': 3000}},
    {'id': 'D2 no partial spend', 'input': {'cap': 5000, 'spent': 4000, 'amount': 2000}},
    {'id': 'D3 zero rejected', 'input': {'cap': 5000, 'spent': 0, 'amount': 0}},
    {'id': 'D4 negative rejected', 'input': {'cap': 5000, 'spent': 3000, 'amount': -1000}},
]
