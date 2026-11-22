"""forge diff inputs for the wallet-cap rule: shared by every language's evaluator. Test code, not proved."""


def generate(rng):
    if rng.float() < 0.1:  # now and then near the integer cliffs of JavaScript (2^53) and of 64-bit code (2^63)
        cap = rng.pick([2 ** 53 + 1, 2 ** 63 - 1, 2 ** 63, 2 ** 64])
        spent = cap - rng.pick([0, 1, 2, 10])
        return {'cap': cap, 'spent': spent, 'amount': rng.pick([1, 2, cap - spent, cap - spent + 1])}
    cap = rng.num(0, 10000)
    spent = rng.num(0, cap)
    room = cap - spent
    amount = rng.pick([rng.num(-200, 12000), room, room + 1, room - 1, 0, -1, 1])
    return {'cap': cap, 'spent': spent, 'amount': amount}


def in_domain(i):  # the proved rule's precondition: 0 <= spent <= cap
    return 0 <= i['spent'] <= i['cap']


# The person's recorded decisions (.forge/decisions/wallet-cap.md), run as fixed inputs on every run.
decisions = [
    {'id': 'D1 exact cap allowed', 'input': {'cap': 5000, 'spent': 2000, 'amount': 3000}},
    {'id': 'D2 no partial spend', 'input': {'cap': 5000, 'spent': 4000, 'amount': 2000}},
    {'id': 'D3 zero rejected', 'input': {'cap': 5000, 'spent': 0, 'amount': 0}},
    {'id': 'D4 negative rejected', 'input': {'cap': 5000, 'spent': 3000, 'amount': -1000}},
]
