#!/usr/bin/env python3
"""C# differential fuzz test. Needs the .NET SDK (dotnet), so it runs in CI; locally it reports SKIPPED when dotnet
is missing. Copies examples/wallet-cap, adds C# to the build and a C# diff entry, approves through the checker,
builds, and runs the fuzz: the shipped C# function's planted bugs must be found. Then the messy-input fuzz of the C#
wrappers that take a raw JsonElement (examples/wallet-cap/messycs), with a negative control, and the warm speed of a
1,500-input run (median of 3, printed for the M1.1 exit gate)."""
import json
import os
import statistics
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CLI = os.path.join(HERE, '..', 'cli', 'forge_check.py')
EXAMPLE = os.path.join(HERE, '..', 'examples', 'wallet-cap')


def main():
    if not shutil.which('dotnet'):
        print('SKIPPED: dotnet is not on PATH')
        return 0
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, 'p')
        shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns('cache', 'regressions', '__pycache__'))
        cfg = json.load(open(os.path.join(root, '.forge/forge.json')))
        check = cfg['checks'][0]
        check['build']['targets'] = ['py', 'cs']
        check['diff'] = [{'lang': 'cs', 'inputs': 'diff/wallet_inputs.py', 'adapter': 'diff/wallet_diff.cs',
                          'cs_sources': ['legacycs'], 'watch': ['legacycs/LegacyWallet.cs']}]
        json.dump(cfg, open(os.path.join(root, '.forge/forge.json'), 'w'), indent=2)
        hashes = json.loads(subprocess.run([sys.executable, CLI, 'spec', '--root', root, '--json'],
                                           stdout=subprocess.PIPE, universal_newlines=True, check=True).stdout)['hashes']
        subprocess.run([sys.executable, CLI, 'approve', '--root', root, '--hashes', json.dumps(hashes)], check=True)
        b = subprocess.run([sys.executable, CLI, 'build', '--root', root, '--no-write'], stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, universal_newlines=True)
        print(b.stdout[-1500:])
        if b.returncode != 0:
            print('FAIL  build with the cs target')
            return 1
        r = subprocess.run([sys.executable, CLI, 'diff', '--root', root, '--json', '--seed', '7'],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        try:
            res = json.loads(r.stdout)['checks']['wallet-cap']
        except (ValueError, KeyError):
            print('FAIL  no diff result: %s %s' % (r.stdout[-800:], r.stderr[-800:]))
            return 1
        failing = sorted(f['id'][:2] for f in (res.get('decisions') or {}).get('failing', []))
        wrapped = any(d['got'].get('ok') is True and isinstance(d['got'].get('spent'), int) and d['got']['spent'] < 0
                      for d in res.get('disagreements', []) if isinstance(d.get('got'), dict))
        ok = res.get('verdict') == 'DISAGREE' and failing == ['D1', 'D3'] and wrapped and res.get('ms', 99999) < 5000
        print('%s  diff: C# evaluator finds exact cap (D1), zero accepted (D3) and the long wraparound; %d ms; verdict %s; '
              'failing %s; wrap found %s' % ('ok  ' if ok else 'FAIL', res.get('ms', -1), res.get('verdict'), failing, wrapped))
        if not ok:
            print(json.dumps(res)[:1500])
        # Negative control: with the shipped C# code fixed, the fuzz must find nothing.
        with open(os.path.join(root, 'legacycs', 'LegacyWallet.cs'), 'w') as f:
            f.write('namespace LegacyCs { public static class LegacyWallet { public static (bool, long) Spend(long cap, long spent, long amount)'
                    ' { if (amount > 0 && amount <= cap - spent) return (true, spent + amount); return (false, spent); } } }\n')
        shutil.rmtree(os.path.join(root, '.forge', 'regressions'), ignore_errors=True)
        r2 = subprocess.run([sys.executable, CLI, 'diff', '--root', root, '--json', '--seed', '8'],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        res2 = json.loads(r2.stdout)['checks']['wallet-cap']
        # The fixed code still takes `long`: inputs above 2^63 cannot be passed to it at all (OverflowException).
        # Those are the only disagreements allowed; every logic disagreement must be gone.
        rest = [d for d in res2.get('disagreements', []) if d.get('got') != {'__throws__': 'OverflowException'}]
        ok2 = res2['decisions']['agree'] == 4 and not rest
        print('%s  diff: negative control, fixed C# code: decisions %s of %s agree; %d logic disagreements; %d inputs '
              'beyond long' % ('ok  ' if ok2 else 'FAIL', res2['decisions']['agree'], res2['decisions']['total'], len(rest),
                               len(res2.get('disagreements', [])) - len(rest)))
        if rest:
            print(json.dumps(rest)[:800])
        failed = (0 if ok else 1) + (0 if ok2 else 1)

        def run(*extra):
            t0 = time.time()
            r = subprocess.run([sys.executable, CLI, 'diff', '--root', root, '--json', '--seed', '7'] + list(extra),
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
            wall = time.time() - t0
            try:
                return r.returncode, json.loads(r.stdout)['checks'], wall
            except (ValueError, KeyError):
                return r.returncode, {'error': (r.stdout[-800:], r.stderr[-800:])}, wall

        # Warm speed (the evaluator is built above): 1,500 inputs, median of 3.
        runs = [run('--inputs', '1500', '--budget-ms', '60000') for _ in range(3)]
        ms = [x[1].get('wallet-cap', {}).get('ms', -1) for x in runs]
        n = [x[1].get('wallet-cap', {}).get('inputs', -1) for x in runs]
        print('info  warm C# fuzz, 1500 inputs: entry ms %s (median %d), wall s %s (median %.2f), inputs %s'
              % (ms, statistics.median(ms), ['%.2f' % x[2] for x in runs], statistics.median(x[2] for x in runs), n))
        # Messy inputs: the C# wrappers that take a raw JsonElement.
        def messy_cfg(flag):
            check['diff'] = [dict({'lang': 'cs', 'inputs': 'diff/wallet_inputs.py', 'adapter': 'diff/wallet_messy.cs',
                                   'cs_sources': ['messycs']}, **flag)]
            json.dump(cfg, open(os.path.join(root, '.forge/forge.json'), 'w'), indent=2)

        messy_cfg({'messy': False})  # negative control first
        code_off, off, _ = run()
        messy_cfg({})
        code_on, on, _ = run()
        code_again, again, _ = run()
        r = on.get('wallet-cap', {})
        m = r.get('messy') or {}

        def got(cand, kind):
            return [d for d in r.get('disagreements', []) if d['candidate'] == cand and d.get('kind') == kind]
        t, c = got('wrapper-throws', 'THROWS_ON_MESSY_INPUT'), got('wrapper-coerces', 'MESSY_ANSWER')
        kinds = [k for k in ('undefined', 'null', 'number', 'boolean', 'empty-text', 'lone-surrogate', 'long-text', 'array',
                             'object', 'number-as-text', 'removed') if k != 'undefined']
        cases = [
            ('messy C#: negative control, messy off: the planted wrappers agree on well-typed inputs',
             code_off == 0 and off.get('wallet-cap', {}).get('verdict') == 'DIFF_OK' and 'messy' not in off.get('wallet-cap', {})),
            ('messy C#: the wrapper that throws on a null, missing or wrong-typed amount is THROWS_ON_MESSY_INPUT, only on '
             'amount, shrunk to cap 0, spent 0',
             code_on == 1 and len(t) > 0 and bool({d['messy']['as'] for d in t} & {'null', 'removed'})
             and all(d['messy']['field'] == 'amount' for d in r.get('disagreements', []) if d['candidate'] == 'wrapper-throws')
             and any({k: v for k, v in d['input'].items() if k != 'amount'} == {'cap': 0, 'spent': 0} for d in t)),
            ('messy C#: the wrapper that coerces "5" to 5 is a MESSY_ANSWER disagreement',
             any(d['messy']['as'] == 'number-as-text' and isinstance(d['input']['amount'], str) for d in c)),
            ('messy C#: negative control, the fail-closed wrapper has 0 messy disagreements (%d messy inputs)' % m.get('inputs', 0),
             r.get('per_candidate', {}).get('wrapper-safe') == 0 and m.get('inputs', 0) > 100),
            ('messy C#: every kind but undefined is tried and judged (kinds %s, not judged %s)' % (m.get('kinds'), m.get('not_judged')),
             m.get('kinds') == kinds and m.get('not_judged') == 0 and m.get('pairs_tried', 0) >= 3 * len(kinds)),
            ('messy C#: saved messy cases replay first next run',
             again.get('wallet-cap', {}).get('replayed', 0) >= len(t) > 0 and code_again == 1),
        ]
        for name, ok in cases:
            failed += not ok
            print('%s  %s' % ('ok  ' if ok else 'FAIL', name))
        if failed:
            print(json.dumps({k: r.get(k) for k in ('verdict', 'per_candidate', 'messy', 'error')}, ensure_ascii=True)[:1500])
            for d in r.get('disagreements', [])[:12]:
                print('      %s %s %s' % (d['candidate'], d.get('group'), json.dumps(d['input'], ensure_ascii=True)[:120]))
        return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
