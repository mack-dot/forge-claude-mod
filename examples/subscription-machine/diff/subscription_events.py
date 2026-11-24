"""Trace fuzz events for the subscription-machine check: shared by every language's evaluator. Test code, not
proved. The core (cli/forge_trace.py) adds the clock, retries, replays, reordering and restarts."""
KINDS = ('Start', 'Renew', 'Pause', 'Resume', 'Cancel')


def event(rng, ctx):
    last = ctx.get('id', 0)
    r = rng.float()
    if r < 0.8:
        eid = last + 1  # the next id
    elif r < 0.9:
        eid = last  # the same id again
    else:
        eid = rng.int(0, last + 3)  # an old id, or a gap
    ctx['id'] = max(last, eid)
    kind = 'Start' if last == 0 and rng.float() < 0.6 else rng.pick(KINDS)
    return {'kind': kind, 'id': eid, 'len': rng.pick([0, 1, 2, 3, 5, rng.num(0, 20)])}


def advance(rng):
    return rng.pick([0, 0, 1, 1, 2, 3, 4, 5, rng.num(0, 25)])


def event_in_domain(e):
    return isinstance(e, dict) and e.get('kind') in KINDS and isinstance(e.get('id'), int) \
        and isinstance(e.get('len'), int) and not isinstance(e.get('id'), bool)
