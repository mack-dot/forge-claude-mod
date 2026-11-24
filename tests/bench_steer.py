"""Measure steered against blind random inputs at the SAME input budget (not a gate; prints a table).

A copy of the example with the shipped Python and TypeScript fixed, then ONE bug planted behind a narrow branch
(`amount == 7777`: the code accepts the spend and keeps `spent` unchanged). For each seed and mode it runs
`diff --inputs N` with a large time budget (so every run gets exactly N inputs) and records whether the planted bug
was found and how many branches (Python) / blocks (V8) of the shipped file the run reached.

Modes (FORGE_STEER_MODE): blind (random from generate(), coverage measured the same way), corpus (kept seeds and
changes, no constants), dict (constants from the code, no seeds), full (both; what --steer does).

Usage: python3 tests/bench_steer.py [--seeds 20] [--inputs 1500] [--langs py,js] [--modes blind,corpus,dict,full]
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
CLI = os.path.join(HERE, '..', 'cli', 'forge_check.py')
EXAMPLE = os.path.join(HERE, '..', 'examples', 'wallet-cap')

PLANTED_PY = ('def legacy_spend(cap, spent, amount):\n'
              '    if amount == 7777:  # planted: a narrow branch\n'
              '        return True, spent\n'
              '    if 0 < amount and spent + amount <= cap:\n'
              '        return True, spent + amount\n'
              '    return False, spent\n')
PLANTED_TS = ('export function legacySpend(cap: number, spent: number, amount: number) {\n'
              '  if (amount === 7777) {\n    return { ok: true, spent } // planted: a narrow branch\n  }\n'
              '  if (0 < amount && spent + amount <= cap) return { ok: true, spent: spent + amount }\n'
              '  return { ok: false, spent }\n}\n')
CANDIDATE = {'py': 'legacy', 'js': 'legacy-ts'}
FILE = {'py': 'legacy_wallet.py', 'js': 'legacy_wallet.ts'}


def project(tmp, planted=True):
    root = os.path.join(tmp, 'p')
    shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns('cache', 'regressions', '__pycache__'))
    cfg_path = os.path.join(root, '.forge', 'forge.json')
    cfg = json.load(open(cfg_path))
    cfg['checks'][0]['diff'] = [d for d in cfg['checks'][0]['diff'] if d['lang'] in ('py', 'js')]
    json.dump(cfg, open(cfg_path, 'w'), indent=2)
    if planted:
        open(os.path.join(root, 'legacy_wallet.py'), 'w').write(PLANTED_PY)
        open(os.path.join(root, 'legacy_wallet.ts'), 'w').write(PLANTED_TS)
    return root


def run(root, lang, seed, inputs, mode):
    reg = os.path.join(root, '.forge', 'regressions')
    shutil.rmtree(reg, ignore_errors=True)  # saved cases would replay the bug: every run starts from nothing
    env = dict(os.environ, FORGE_STEER_MODE=mode)
    t = time.time()
    r = subprocess.run([sys.executable, CLI, 'diff', '--root', root, '--no-write', '--json', '--seed', str(seed),
                        '--inputs', str(inputs), '--budget-ms', '600000', '--steer'],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, env=env)
    wall = time.time() - t
    res = json.loads(r.stdout)['checks']['wallet-cap:' + lang]
    f = (res.get('coverage') or {}).get('files', {}).get(FILE[lang], {})
    # Python: branches (each if/while/for two ways); V8 gives no branch total, so JS/TS: code lines reached.
    reached = tuple(f['branches']) if f.get('branches') else tuple(f.get('lines') or (0, 0))
    # The planted bug only: the TypeScript copy also loses precision above 2^53 (a real, unplanted difference).
    found = any(d['candidate'] == CANDIDATE[lang] and isinstance(d['input'], dict) and d['input'].get('amount') == 7777
                for d in res.get('disagreements') or [])
    return found, reached, res.get('inputs'), wall, res.get('ms')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--seeds', type=int, default=20)
    ap.add_argument('--inputs', type=int, default=1500)
    ap.add_argument('--langs', default='py,js')
    ap.add_argument('--modes', default='blind,corpus,dict,full')
    a = ap.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        root = project(tmp)
        for lang in a.langs.split(','):
            for mode in a.modes.split(','):
                rows = [run(root, lang, s, a.inputs, mode) for s in range(1, a.seeds + 1)]
                found = sum(r[0] for r in rows)
                reach = sorted(r[1][0] for r in rows)
                total = rows[0][1][1]
                print(json.dumps({'lang': lang, 'mode': mode, 'inputs': a.inputs, 'seeds': a.seeds,
                                  'found': found, 'found_rate': '%d/%d' % (found, a.seeds),
                                  'reached_median': reach[len(reach) // 2], 'reached_min': reach[0], 'of': total,
                                  'unit': 'branches' if lang == 'py' else 'code lines',
                                  'median_ms': sorted(r[4] for r in rows)[len(rows) // 2],
                                  'every_run_inputs': sorted({r[2] for r in rows})}), flush=True)


if __name__ == '__main__':
    main()
