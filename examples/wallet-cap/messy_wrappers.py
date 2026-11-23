"""Three wrappers around the proved wallet rule that take a RAW dict {cap, spent, amount}, as an app gets it from
JSON. THIS FILE IS NOT PROVED. Two are planted on purpose so that the messy fuzz of `forge_check.py diff` has
something real to find. Do not fix them: the tests depend on these bugs.
  spend_safe     never raises: a value that is not a whole number, or 0 <= spent <= cap that does not hold, fails closed.
  spend_raises   bug: compares amount with 0 before it checks the type, so amount None (or text, a list, ...) raises.
  spend_coerces  bug: turns the text "5" into the number 5 (the proved rule never sees text)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'verified', 'wallet-cap', 'py', 'wallet_cap-py'))
import Wallet  # noqa: E402  the compiled proved rule

REFUSED = {'ok': False, 'spent': None}  # fail closed: nothing spent, no new state


def _whole(x):
    return type(x) is int  # bool is an int subclass in Python; it is not a whole number of cents


def _proved(cap, spent, amount):
    if not 0 <= spent <= cap:
        return dict(REFUSED)
    nxt, outcome = Wallet.default__.Spend(Wallet.State_Wallet(cap, spent), amount)
    return {'ok': bool(outcome.is_Ok), 'spent': int(nxt.spent)}


def spend_safe(raw):
    if not isinstance(raw, dict):
        return dict(REFUSED)
    cap, spent, amount = raw.get('cap'), raw.get('spent'), raw.get('amount')
    if not (_whole(cap) and _whole(spent) and _whole(amount)):
        return dict(REFUSED)
    return _proved(cap, spent, amount)


def spend_raises(raw):
    if not (_whole(raw.get('cap')) and _whole(raw.get('spent'))):
        return dict(REFUSED)
    if raw['amount'] < 0:  # bug: TypeError for None, text, lists; KeyError when amount is missing
        return {'ok': False, 'spent': raw['spent']}  # (what the proved rule says for a negative amount)
    if not _whole(raw['amount']):
        return dict(REFUSED)
    return _proved(raw['cap'], raw['spent'], raw['amount'])


def spend_coerces(raw):
    amount = raw.get('amount')
    if isinstance(amount, str) and amount.lstrip('-').isdigit() and amount.isascii():
        amount = int(amount)  # bug
    if not (_whole(raw.get('cap')) and _whole(raw.get('spent')) and _whole(amount)):
        return dict(REFUSED)
    return _proved(raw['cap'], raw['spent'], amount)
