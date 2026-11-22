"""The wallet check as it was SHIPPED before Forge. Kept on purpose, with three bugs, so that
`forge_check.py diff` has something real to find. Do not fix it: the tests depend on these bugs."""


def legacy_spend(cap, spent, amount):
    if spent + amount < cap:  # bug 1: a spend that reaches the cap exactly is rejected
        return True, spent + amount  # bug 2: a zero amount is accepted; bug 3: so is a negative one
    return False, spent
