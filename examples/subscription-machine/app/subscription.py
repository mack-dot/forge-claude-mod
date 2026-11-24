"""The shipped subscription code (Python): the app's own step function over plain dict state. It must behave exactly
like the proved model (Subscription.Step in impl/Subscription.dfy) for EVERY sequence of events, with retries,
replays and restarts. Not proved: the trace fuzz (forge_check.py diff) compares it with the model."""
import json


def initial():
    return {'status': 'New', 'expiresAt': 0, 'left': 0, 'lastId': 0, 'renewals': 0}


def step(s, e, now):
    """{'ok': next state} or {'reject': 'Duplicate' | 'BadInput' | 'BadState' | 'Expired'}; never changes `s`."""
    if e['id'] < 1:
        return {'reject': 'BadInput'}
    if e['id'] <= s['lastId']:
        return {'reject': 'Duplicate'}
    kind = e['kind']
    if kind in ('Start', 'Renew') and e['len'] < 1:
        return {'reject': 'BadInput'}
    if kind == 'Start':
        if s['status'] != 'New':
            return {'reject': 'BadState'}
        return {'ok': {'status': 'Active', 'expiresAt': now + e['len'], 'left': 0, 'lastId': e['id'], 'renewals': 0}}
    if kind == 'Renew':
        if s['status'] != 'Active':
            return {'reject': 'BadState'}
        if now >= s['expiresAt']:
            return {'reject': 'Expired'}
        return {'ok': dict(s, expiresAt=s['expiresAt'] + e['len'], lastId=e['id'], renewals=s['renewals'] + 1)}
    if kind == 'Pause':
        if s['status'] != 'Active':
            return {'reject': 'BadState'}
        if now >= s['expiresAt']:
            return {'reject': 'Expired'}
        return {'ok': dict(s, status='Paused', left=s['expiresAt'] - now, lastId=e['id'])}
    if kind == 'Resume':
        if s['status'] != 'Paused':
            return {'reject': 'BadState'}
        return {'ok': dict(s, status='Active', expiresAt=now + s['left'], left=0, lastId=e['id'])}
    if kind == 'Cancel':
        if s['status'] not in ('Active', 'Paused'):
            return {'reject': 'BadState'}
        return {'ok': dict(s, status='Canceled', left=0, lastId=e['id'])}
    raise ValueError('unknown event kind')


def save(s):
    """What the app writes to its database."""
    return json.dumps(s)


def load(text):
    """What the app reads back after a restart."""
    d = json.loads(text)
    return {k: d[k] for k in ('status', 'expiresAt', 'left', 'lastId', 'renewals')}
