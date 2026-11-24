"""PLANTED BUGS (test material, never shipped): four copies of app/subscription.py, each with one bug that shows only
after two or more events. The trace fuzz must find each one and shrink it to its minimal trace.
  replay-off-by-one   the duplicate check uses < instead of <=, so an immediate retry is applied again
  pause-expiry        after a Resume the expiry check is one tick late (now > expiresAt instead of >=)
  restart-drops-id    save() leaves out lastId, so after a restart old events apply again
  reject-leaks        Renew changes the state dict before it checks expiry, and a rejected Renew keeps it"""
import json

import subscription as good


def replay_off_by_one(s, e, now):
    if e['id'] < 1:
        return {'reject': 'BadInput'}
    if e['id'] < s['lastId']:  # BUG: an equal id is a retry, too
        return {'reject': 'Duplicate'}
    return good.step(dict(s, lastId=min(s['lastId'], e['id'] - 1)), e, now)


def pause_expiry(s, e, now):
    # BUG: after a Resume this code gives one grace tick: the expiry check becomes now > expiresAt
    expired = now > s['expiresAt'] if s.get('resumed') else now >= s['expiresAt']
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
        if expired:
            return {'reject': 'Expired'}
        return {'ok': dict(s, expiresAt=s['expiresAt'] + e['len'], lastId=e['id'], renewals=s['renewals'] + 1)}
    if kind == 'Pause':
        if s['status'] != 'Active':
            return {'reject': 'BadState'}
        if expired:
            return {'reject': 'Expired'}
        return {'ok': dict(s, status='Paused', left=s['expiresAt'] - now, lastId=e['id'])}
    if kind == 'Resume':
        if s['status'] != 'Paused':
            return {'reject': 'BadState'}
        return {'ok': dict(s, status='Active', expiresAt=now + s['left'], left=0, lastId=e['id'], resumed=True)}
    if kind == 'Cancel':
        if s['status'] not in ('Active', 'Paused'):
            return {'reject': 'BadState'}
        return {'ok': dict(s, status='Canceled', left=0, lastId=e['id'])}
    raise ValueError('unknown event kind')


def reject_leaks(s, e, now):
    if e['kind'] == 'Renew' and e['id'] > s['lastId'] and e['len'] >= 1 and s['status'] == 'Active':
        s['renewals'] += 1  # BUG: changes the caller's state before the expiry check
        if now >= s['expiresAt']:
            return {'reject': 'Expired'}
        return {'ok': dict(s, expiresAt=s['expiresAt'] + e['len'], lastId=e['id'])}
    return good.step(s, e, now)


def view(s):
    return {k: s[k] for k in ('status', 'expiresAt', 'left', 'lastId', 'renewals')}


def machine(step, save=good.save, load=good.load):
    return {'init': good.initial, 'step': step, 'view': view, 'save': save, 'load': load}


planted = {
    'replay-off-by-one': machine(replay_off_by_one),
    'pause-expiry': machine(pause_expiry, json.dumps, json.loads),
    'restart-drops-id': machine(good.step, lambda s: json.dumps({k: v for k, v in s.items() if k != 'lastId'}),  # BUG
                                lambda t: dict({'lastId': 0}, **json.loads(t))),
    'reject-leaks': machine(reject_leaks),
}
