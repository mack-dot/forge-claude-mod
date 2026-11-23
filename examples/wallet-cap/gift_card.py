"""Gift sends, added later by another team. Kept on purpose: it decides "does this spend fit under the cap?"
itself instead of calling the proved rule, so that `forge_check.py bypass` has something real to find. Do not fix
it: the tests depend on it."""


def can_send_gift(cap, spent, amount):
    """True if a gift of `amount` cents still fits in the sender's wallet."""
    if spent + amount <= cap:  # goes around the proved rule: a negative gift passes, and nothing proves this line
        return True
    return False
