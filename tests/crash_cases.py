"""Tests of `forge_crash.py` (CRASH-CHECK) and of the fuzz tiers and steering (cli/forge_steer.py).

Crash fixture (tests/crash_fixture/): per language three planted crashes (a missing field, a null deref, JSON.parse /
json.loads of a value that is not text), one fail-closed handler (negative control: 0 crashes) and one private
helper that throws on undefined / None (negative control: NOT reported by default; reported with
--include-private in Python, so the skip is a real skip, not a blind spot of the runner).

Steering: a copy of the example with a bug planted behind `amount == 7777` (tests/bench_steer.py). Blind random at
1,500 inputs misses it for these seeds; the steered run finds it. Needs node."""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
CLI = os.path.join(HERE, '..', 'cli')
FIXTURE = os.path.join(HERE, 'crash_fixture')


def _crash(root, *args):
    r = subprocess.run([sys.executable, os.path.join(CLI, 'forge_crash.py'), '--root', root, '--seed', '1', '--json']
                       + list(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
    try:
        return r.returncode, json.loads(r.stdout)
    except ValueError:
        return r.returncode, {'functions': [], 'skipped': [], 'error': (r.stderr or r.stdout)[-400:]}


def _fn(res, name):
    return next((f for f in res['functions'] if f['name'] == name), None)


def _has(f, error, text, kinds):
    return bool(f) and any(c['error'] == error and text in c['message'] and (c['kind'] in kinds or any(
        a.split(' = ')[-1] in kinds for a in c.get('also', []))) for c in f['crashes'])


def crash_cases():
    out = []
    code, res = _crash(FIXTURE)
    names = {f['name'] for f in res['functions']}
    text = subprocess.run([sys.executable, os.path.join(CLI, 'forge_crash.py'), '--root', FIXTURE, '--seed', '1'],
                          stdout=subprocess.PIPE, universal_newlines=True).stdout
    out += [
        ('crash-fuzz js: missing field (body.item) is a TypeError, found and shrunk to {"item": ...}',
         _has(_fn(res, 'createOrder'), 'TypeError', "reading 'quantity'", ('undefined', 'removed'))
         and any(set(c['input']) == {'item'} for c in _fn(res, 'createOrder')['crashes'])),
        ('crash-fuzz js: null deref (user: null) in a fetch-style POST handler is a TypeError, repro {"user": null}',
         any(c['error'] == 'TypeError' and c['input'] == {'user': None} for c in (_fn(res, 'POST') or {}).get('crashes', []))),
        ('crash-fuzz js: JSON.parse of an object (not text) is a SyntaxError "[object Object]"',
         _has(_fn(res, 'parseWebhook'), 'SyntaxError', '', ('object',))),
        ('crash-fuzz py: missing field (item) is a KeyError, shrunk to {}',
         any(c['error'] == 'KeyError' and "'item'" in c['message'] or c['error'] == 'KeyError' and 'item = removed' in c.get('also', [])
             for c in (_fn(res, 'create_order') or {}).get('crashes', []))),
        ('crash-fuzz py: null deref (payload.user = None) is a TypeError, repro {"payload": {"user": null}}',
         any(c['error'] == 'TypeError' and c['input'] == {'payload': {'user': None}} for c in (_fn(res, 'handle_profile') or {}).get('crashes', []))),
        ('crash-fuzz py: json.loads of a dict (not text) in a Lambda handler is a TypeError',
         _has(_fn(res, 'lambda_handler'), 'TypeError', 'must be str', ('object', 'array', 'number', 'null'))),
        ('crash-fuzz: negative control, the fail-closed handlers have 0 crashes (js %d calls, py %d calls)' % (
            (_fn(res, 'updateLimit') or {}).get('calls', 0), (_fn(res, 'update_limit') or {}).get('calls', 0)),
         all(_fn(res, n) and not _fn(res, n)['crashes'] and _fn(res, n)['calls'] >= 50 for n in ('updateLimit', 'update_limit'))),
        ('crash-fuzz: negative control, the private helpers that throw on undefined/None are NOT reported, and the '
         'reason is said', not ({'normalizeName', '_normalize'} & names)
         and sum('private helper' in s['reason'] for s in res['skipped']) == 2),
        ('crash-fuzz: exactly the 6 planted handlers crash, exit 1, the headline counts them, labelled CRASH-CHECK '
         'and never PROVED', code == 1 and res.get('crashing') == 6 and res.get('label') == 'CRASH-CHECK'
         and text.startswith('CRASH-CHECK: 6 handlers crash with unhandled errors') and 'PROVED' not in text.replace('NOT PROVED', '')),
    ]
    code_p, res_p = _crash(FIXTURE, '--include-private', '--langs', 'py')
    out.append(('crash-fuzz py: with --include-private the private helper is called and its crash on None is found',
                any(c['error'] == 'AttributeError' for c in (_fn(res_p, '_normalize') or {}).get('crashes', []))))
    code_t, res_t = _crash(FIXTURE, '--target', 'js/handlers.ts:updateLimit')
    out.append(('crash-fuzz: --target checks only the named function (fail-closed: exit 0)',
                code_t == 0 and [f['name'] for f in res_t['functions']] == ['updateLimit']))
    return out


def steer_cases():
    sys.path.insert(0, HERE)
    import bench_steer
    out = []
    with tempfile.TemporaryDirectory() as tmp:
        root = bench_steer.project(tmp)
        rows = {}
        for mode in ('blind', 'full'):
            for lang in ('py', 'js'):
                rows[mode, lang] = [bench_steer.run(root, lang, s, 1500, mode) for s in (1, 2, 3)]
        for lang in ('py', 'js'):
            b, f = rows['blind', lang], rows['full', lang]
            out.append(('steer %s: negative control, blind random at 1,500 inputs misses the bug behind amount == 7777 '
                        '(found in %d of 3 seeds)' % (lang, sum(r[0] for r in b)), sum(r[0] for r in b) == 0))
            out.append(('steer %s: the steered run finds it in 3 of 3 seeds and reaches more of the shipped code '
                        '(%s vs %s)' % (lang, f[0][1], b[0][1]), all(r[0] for r in f) and all(r[1][0] > b[0][1][0] for r in f)))
        # the plain line for an unreached branch, and the tiers
        reg = os.path.join(root, '.forge', 'regressions')
        shutil.rmtree(reg, ignore_errors=True)
        r = subprocess.run([sys.executable, os.path.join(CLI, 'forge_check.py'), 'diff', '--root', root, '--no-write',
                            '--seed', '1', '--inputs', '300', '--no-steer', '--coverage'],
                           stdout=subprocess.PIPE, universal_newlines=True)
        r2 = subprocess.run([sys.executable, os.path.join(CLI, 'forge_check.py'), 'diff', '--root', root, '--no-write',
                             '--seed', '1', '--inputs', '300', '--steer'], stdout=subprocess.PIPE, universal_newlines=True,
                            env=dict(os.environ, FORGE_STEER_MODE='blind'))
        out.append(('steer: an unreached line is said plainly ("legacy_wallet.py:3 was never reached")',
                    'legacy_wallet.py:3 was never reached' in r2.stdout))
        cfg_path = os.path.join(root, '.forge', 'forge.json')
        cfg = json.load(open(cfg_path))
        cfg['fuzz'] = {'tiers': {'ci': {'inputs': 400, 'steer': False}}}
        json.dump(cfg, open(cfg_path, 'w'))
        shutil.rmtree(reg, ignore_errors=True)
        r3 = subprocess.run([sys.executable, os.path.join(CLI, 'forge_check.py'), 'diff', '--root', root, '--no-write',
                             '--seed', '1', '--tier', 'ci', '--json'], stdout=subprocess.PIPE, universal_newlines=True)
        d3 = json.loads(r3.stdout)
        out.append(('tiers: forge.json "fuzz.tiers.ci" sets the CI tier (400 inputs, not steered)',
                    d3.get('tier') == 'ci' and all(c.get('inputs') == 400 and 'steer' not in c for c in d3['checks'].values())))
        cfg['fuzz'] = {'tiers': {'ci': {'inputs': -1}}}
        json.dump(cfg, open(cfg_path, 'w'))
        r4 = subprocess.run([sys.executable, os.path.join(CLI, 'forge_check.py'), 'diff', '--root', root, '--no-write',
                             '--tier', 'ci'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        out.append(('tiers: a bad tier value is a configuration error (exit 2)', r4.returncode == 2))
        out.append(('tiers: --coverage without steering still works and names the planted branch',
                    'legacy_wallet.py:2  condition never True' in r.stdout))
    return out


def cases():
    return crash_cases() + steer_cases()
