"""forge diff adapter (Python) for the messy fuzz: the wrappers that take raw {cap, spent, amount} dicts, and the
proved rule. Inputs come from wallet_inputs.py. Test code, not proved."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'verified', 'wallet-cap', 'py', 'wallet_cap-py'))
import Wallet  # noqa: E402  the compiled proved rule
import messy_wrappers  # noqa: E402  the wrappers under test


def reference(i):
    nxt, outcome = Wallet.default__.Spend(Wallet.State_Wallet(i['cap'], i['spent']), i['amount'])
    return {'ok': bool(outcome.is_Ok), 'spent': int(nxt.spent)}


def fail_closed(i):
    """The spec's answer for a value the proved rule does not take: refused, nothing spent. None: the rule takes it."""
    whole = isinstance(i, dict) and all(type(i.get(k)) is int for k in ('cap', 'spent', 'amount'))
    return None if whole and 0 <= i['spent'] <= i['cap'] else dict(messy_wrappers.REFUSED)


# Each calls the wrapper with the raw value: no conversion here.
wrappers = {'wrapper-safe': messy_wrappers.spend_safe, 'wrapper-raises': messy_wrappers.spend_raises,
            'wrapper-coerces': messy_wrappers.spend_coerces}
