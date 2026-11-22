"""forge diff evaluator adapter (Python): the shipped wallet code and the app's wrapper, and the proved rule.
Inputs come from wallet_inputs.py. Test code, not proved."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'verified', 'wallet-cap', 'py', 'wallet_cap-py'))
import Wallet  # noqa: E402  the compiled proved rule
import app  # noqa: E402  the wrapper the app calls
import legacy_wallet  # noqa: E402  the code shipped before Forge


def reference(i):
    nxt, outcome = Wallet.default__.Spend(Wallet.State_Wallet(i['cap'], i['spent']), i['amount'])
    return {'ok': bool(outcome.is_Ok), 'spent': int(nxt.spent)}


def _pair(t): return {'ok': bool(t[0]), 'spent': int(t[1])}


candidates = {
    'app-wrapper': lambda i: _pair(app.spend(i['cap'], i['spent'], i['amount'])),
    'legacy': lambda i: _pair(legacy_wallet.legacy_spend(i['cap'], i['spent'], i['amount'])),
}
