"""forge diff inputs for thread-visibility: threads with reply chains, cycles, takedowns and flags. Test code."""
IDS = ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h']


def generate(rng):
    ids = IDS[:rng.int(0, 8)]
    items = [{'id': i, 'channel': rng.pick(['dev', 'dev', 'dev', 'ops', '']), 'author': rng.pick(['ana', 'bo', 'mod', '', 'Mod']),
              'kind': rng.pick(['post', 'post', 'post', 'takedown']),
              # a parent anywhere in the thread (also later items and itself: cycles), a missing id, or none
              'parent': rng.pick(['', '', 'zz'] + ids + ids), 'flagged': rng.float() < 0.15} for i in ids]
    return {'viewer': {'user': rng.pick(['ana', 'bo', 'mod']), 'channel': rng.pick(['dev', 'dev', 'ops', ''])}, 'items': items}


def in_domain(i):  # ids are unique in a thread
    ids = [it['id'] for it in i['items']]
    return len(set(ids)) == len(ids)


def _p(i, author='bo', parent='', channel='dev', kind='post', flagged=False):
    return {'id': i, 'channel': channel, 'author': author, 'kind': kind, 'parent': parent, 'flagged': flagged}


ANA = {'user': 'ana', 'channel': 'dev'}
decisions = [
    {'id': 'D1 cycle shares takedown', 'input': {'viewer': ANA, 'items': [_p('p1', parent='p2'), _p('p2', parent='p1'), _p('t1', parent='p1', kind='takedown')]}},
    {'id': 'D2 self reply', 'input': {'viewer': ANA, 'items': [_p('p1', parent='p1')]}},
    {'id': 'D3 deep takedown', 'input': {'viewer': ANA, 'items': [_p('p1'), _p('p2', 'cy', 'p1'), _p('p3', 'ana', 'p2'), _p('t1', parent='p1', kind='takedown')]}},
    {'id': 'D4 flagged root', 'input': {'viewer': ANA, 'items': [_p('p1', flagged=True), _p('p2', 'cy', 'p1')]}},
    {'id': 'D5 parent in another channel', 'input': {'viewer': ANA, 'items': [_p('p1', channel='ops'), _p('p2', 'cy', 'p1')]}},
    {'id': 'D6 missing parent', 'input': {'viewer': ANA, 'items': [_p('p2', 'cy', 'gone')]}},
    {'id': 'D7 takedown by another', 'input': {'viewer': ANA, 'items': [_p('p1'), _p('t1', 'cy', 'p1', kind='takedown')]}},
    {'id': 'D8 takedown by moderator', 'input': {'viewer': ANA, 'items': [_p('p1'), _p('t1', 'mod', 'p1', kind='takedown')]}},
    {'id': 'D9 empty channel', 'input': {'viewer': {'user': 'ana', 'channel': ''}, 'items': [_p('p1', channel='')]}},
]
