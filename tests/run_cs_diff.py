#!/usr/bin/env python3
"""C# differential fuzz test. Needs the .NET SDK (dotnet), so it runs in CI; locally it reports SKIPPED when dotnet
is missing. Copies examples/wallet-cap, adds C# to the build and a C# diff entry, approves through the checker,
builds, and runs the fuzz: the shipped C# function's planted bugs must be found."""
import json
import os
import shutil
import subprocess
import sys
import tempfile

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
        return 0 if ok and ok2 else 1


if __name__ == '__main__':
    sys.exit(main())
