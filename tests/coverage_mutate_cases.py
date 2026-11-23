"""Tests of `diff --coverage` and `mutate --app` (cli/forge_coverage.py, cli/forge_mutate_app.py).

Strong = the example's own inputs module, with the shipped Python and TypeScript code fixed (no planted bug).
Weak = the same fixed code with a deliberately weak inputs module (narrow ranges, no boundaries, no decisions):
the negative control. Both measures must fall when the fuzz is weak. Needs node (and the example's build)."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
CLI = os.path.join(HERE, '..', 'cli', 'forge_check.py')
EXAMPLE = os.path.join(HERE, '..', 'examples', 'wallet-cap')

FIXED_PY = ('def legacy_spend(cap, spent, amount):\n'
            '    if 0 < amount and spent + amount <= cap:\n        return True, spent + amount\n    return False, spent\n')
FIXED_TS = ('export function legacySpend(cap: number, spent: number, amount: number) {\n'
            '  if (0 < amount && spent + amount <= cap) return { ok: true, spent: spent + amount }\n'
            '  return { ok: false, spent }\n}\n')
WEAK_INPUTS = ('"""A deliberately weak inputs module (negative control)."""\n\n\n'
               'def generate(rng):\n    return {"cap": 10000, "spent": rng.int(0, 100), "amount": rng.int(1, 100)}\n\n\n'
               'def in_domain(i):\n    return 0 <= i["spent"] <= i["cap"]\n')


def _project(tmp, name, fixed=True, weak=False):
    root = os.path.join(tmp, name)
    shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns('cache', 'regressions', '__pycache__'))
    cfg_path = os.path.join(root, '.forge', 'forge.json')
    cfg = json.load(open(cfg_path))
    cfg['checks'][0]['diff'] = [d for d in cfg['checks'][0]['diff'] if d['lang'] in ('py', 'js')]
    json.dump(cfg, open(cfg_path, 'w'), indent=2)  # diff does not read the approval; only py and js are measured here
    if fixed:
        open(os.path.join(root, 'legacy_wallet.py'), 'w').write(FIXED_PY)
        open(os.path.join(root, 'legacy_wallet.ts'), 'w').write(FIXED_TS)
    if weak:
        open(os.path.join(root, 'diff', 'wallet_inputs.py'), 'w').write(WEAK_INPUTS)
    return root


def _run(root, *args, env=None):
    e = dict(os.environ, FORGE_NO_COVERAGE_PY='1', **(env or {}))
    r = subprocess.run([sys.executable, CLI] + list(args) + ['--root', root, '--json'], stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, universal_newlines=True, env=e)
    try:
        return r.returncode, json.loads(r.stdout)
    except ValueError:
        return r.returncode, {'error': (r.stderr or r.stdout)[-400:]}


def _tree(root):
    h = hashlib.sha256()
    for d, dirs, files in sorted(os.walk(root)):
        dirs.sort()
        for f in sorted(files):
            p = os.path.join(d, f)
            h.update(os.path.relpath(p, root).encode() + b'\0' + open(p, 'rb').read())
    return h.hexdigest()


def _cov(res, key, f):
    return ((res.get('checks', {}).get(key) or {}).get('coverage') or {}).get('files', {}).get(f) or {}


def cases():
    out = []
    with tempfile.TemporaryDirectory() as tmp:
        strong, weak = _project(tmp, 'strong'), _project(tmp, 'weak', weak=True)
        buggy = _project(tmp, 'buggy', fixed=False)
        # ---- diff --coverage
        _, plain = _run(strong, 'diff', '--seed', '7', '--no-write')
        shutil.rmtree(os.path.join(strong, '.forge', 'regressions'), ignore_errors=True)  # same start for both runs
        _, s = _run(strong, 'diff', '--seed', '7', '--no-write', '--coverage')
        _, w = _run(weak, 'diff', '--seed', '7', '--no-write', '--coverage')
        sp, wp = _cov(s, 'wallet-cap:py', 'legacy_wallet.py'), _cov(w, 'wallet-cap:py', 'legacy_wallet.py')
        sj, wj = _cov(s, 'wallet-cap:js', 'legacy_wallet.ts'), _cov(w, 'wallet-cap:js', 'legacy_wallet.ts')
        strip = lambda r: {k: {kk: v.get(kk) for kk in ('verdict', 'disagreements', 'decisions', 'inputs')}
                           for k, v in r.get('checks', {}).items()}
        out.append(('coverage: off by default; on, the diff verdicts and findings are the same',
                    all('coverage' not in r for r in plain.get('checks', {}).values())
                    and strip(plain) == strip(s) and bool(strip(s))))
        out.append(('coverage: strong inputs reach every line and branch of the shipped Python and TypeScript',
                    sp.get('lines') == [4, 4] and sp.get('branches') == [2, 2] and sj.get('lines') == [3, 3]
                    and sj.get('blocks_never_run') == 0))
        out.append(('coverage: weak inputs (negative control) miss the reject path, named by file:line and source',
                    wp.get('branches') == [1, 2] and wp.get('lines') == [3, 4]
                    and any(u.startswith('legacy_wallet.py:2  condition never False') for u in wp.get('unreached', []))
                    and wj.get('lines') == [2, 3] and wj.get('blocks_never_run') == 1
                    and any(u.startswith('legacy_wallet.ts:3  block never run') and 'ok: false' in u
                            for u in wj.get('unreached', []))))
        app = _cov(s, 'wallet-cap:py', 'app.py')
        out.append(('coverage: the wrapper\'s unreached error paths are named (in-domain inputs only)',
                    any(u.startswith('app.py:18  condition never True') for u in app.get('unreached', []))))
        try:
            import coverage  # noqa: F401  only when this Python has coverage.py; never required
            have_cov = True
        except ImportError:
            have_cov = False
        if have_cov:
            e = dict(os.environ)
            e.pop('FORGE_NO_COVERAGE_PY', None)
            r = subprocess.run([sys.executable, CLI, 'diff', '--seed', '7', '--no-write', '--coverage', '--root', weak,
                                '--json'], stdout=subprocess.PIPE, universal_newlines=True, env=e)
            cp = _cov(json.loads(r.stdout), 'wallet-cap:py', 'legacy_wallet.py')
            out.append(('coverage: coverage.py and the stdlib tracer give the same counts',
                        cp.get('lines') == wp.get('lines') and cp.get('branches') == wp.get('branches')))
        # ---- mutate --app
        before = _tree(strong)
        _, ms = _run(strong, 'mutate', '--app')
        _, mw = _run(weak, 'mutate', '--app')
        _, mb = _run(buggy, 'mutate', '--app')
        cmp_line = lambda m: (m['file'], m['line']) in (('legacy_wallet.py', 2), ('legacy_wallet.ts', 2))
        s_cmp = [m for m in ms.get('mutants', []) if cmp_line(m)]
        w_cmp = [m for m in mw.get('mutants', []) if cmp_line(m)]
        score = lambda r: r.get('caught', 0) / float(max(1, r.get('caught', 0) + r.get('survived', 0)))
        out.append(('mutate --app: strong inputs catch every mutant of the cap comparison (Python and TypeScript)',
                    len(s_cmp) >= 8 and all(m['status'] == 'caught' for m in s_cmp)
                    and {m['file'] for m in s_cmp} == {'legacy_wallet.py', 'legacy_wallet.ts'}))
        out.append(('mutate --app: weak inputs (negative control) let cap-comparison mutants survive; score falls',
                    any(m['status'] == 'survived' and m['mutation'] == '<= to <' for m in w_cmp)
                    and score(mw) < score(ms) and mw.get('survived', 0) > ms.get('survived', 0)))
        out.append(('mutate --app: a survivor on a line the fuzz never reached says so',
                    any(m['status'] == 'survived' and m.get('line_reached') is False and m['file'] == 'legacy_wallet.py'
                        for m in mw.get('mutants', []))))
        out.append(('mutate --app: on the shipped buggy code, "fixing" bug 1 (< to <=) is caught as a change',
                    any(m['file'] == 'legacy_wallet.py' and m['line'] == 6 and m['mutation'] == '< to <='
                        and m['status'] == 'caught' for m in mb.get('mutants', []))))
        out.append(('mutate --app: the project is not changed (no mutant, no saved case left behind)',
                    _tree(strong) == before and ms.get('caught', 0) > 0))
        out.append(('mutate --app: --max caps the mutants; py and js entries by default',
                    _run(strong, 'mutate', '--app', '--max', '3')[1].get('sampled') == 3
                    and set(ms.get('entries', {})) == {'wallet-cap:py', 'wallet-cap:js'}))
    return out
