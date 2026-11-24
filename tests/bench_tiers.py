"""Measure the fuzz tiers' wall time per language (not a gate; prints JSON lines).

On a copy of the example (py and js diff entries; the shipped code fixed so every run does the full input count):
  local  `diff` with the local tier (1,500 inputs), blind and steered: median of --runs warm runs, wall time of the
         whole command and the core's own "ms" (evaluator start excluded)
  ci     `diff --tier ci` (100,000 inputs by default; --ci-inputs to change), blind and steered: one run each
Usage: python3 tests/bench_tiers.py [--runs 5] [--ci-inputs 100000] [--langs py,js]
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bench_steer  # noqa: E402

CLI = os.path.join(HERE, '..', 'cli', 'forge_check.py')
FIXED_PY = 'def legacy_spend(cap, spent, amount):\n    if 0 < amount and spent + amount <= cap:\n        return True, spent + amount\n    return False, spent\n'
FIXED_TS = ('export function legacySpend(cap: number, spent: number, amount: number) {\n'
            '  if (0 < amount && spent + amount <= cap) return { ok: true, spent: spent + amount }\n'
            '  return { ok: false, spent }\n}\n')


def one(root, lang, args):
    shutil.rmtree(os.path.join(root, '.forge', 'regressions'), ignore_errors=True)
    t = time.time()
    r = subprocess.run([sys.executable, CLI, 'diff', '--root', root, '--no-write', '--json', '--seed', '1'] + args,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
    wall = int((time.time() - t) * 1000)
    res = list(json.loads(r.stdout)['checks'].values())[0]  # one entry: the key has no language suffix
    return wall, res.get('ms'), res.get('inputs')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--runs', type=int, default=5)
    ap.add_argument('--ci-inputs', type=int, default=100000)
    ap.add_argument('--langs', default='py,js')
    a = ap.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        root = bench_steer.project(tmp, planted=False)
        open(os.path.join(root, 'legacy_wallet.py'), 'w').write(FIXED_PY)
        open(os.path.join(root, 'legacy_wallet.ts'), 'w').write(FIXED_TS)
        cfg_path = os.path.join(root, '.forge', 'forge.json')
        for lang in a.langs.split(','):
            cfg = json.load(open(os.path.join(bench_steer.EXAMPLE, '.forge', 'forge.json')))
            cfg['checks'][0]['diff'] = [d for d in cfg['checks'][0]['diff'] if d['lang'] == lang]
            cfg['fuzz'] = {'tiers': {'ci': {'inputs': a.ci_inputs}}}
            json.dump(cfg, open(cfg_path, 'w'))
            for steer in ('--no-steer', '--steer'):
                one(root, lang, [steer])  # warm: caches, bytecode, npm folder
                rows = [one(root, lang, [steer]) for _ in range(a.runs)]
                med = lambda i: sorted(r[i] for r in rows)[len(rows) // 2]  # noqa: E731
                print(json.dumps({'lang': lang, 'tier': 'local', 'steer': steer == '--steer', 'runs': a.runs,
                                  'inputs': rows[0][2], 'median_wall_ms': med(0), 'median_core_ms': med(1)}), flush=True)
            for steer in ('--no-steer', '--steer'):
                wall, ms, n = one(root, lang, ['--tier', 'ci', steer])
                print(json.dumps({'lang': lang, 'tier': 'ci', 'steer': steer == '--steer', 'runs': 1, 'inputs': n,
                                  'wall_ms': wall, 'core_ms': ms}), flush=True)


if __name__ == '__main__':
    main()
