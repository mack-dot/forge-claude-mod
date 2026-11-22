#!/usr/bin/env python3
"""Tests of cli/forge_check.py: each case copies examples/wallet-cap, changes it,
and requires one verdict and one exit code. Needs Dafny (PATH or FORGE_DAFNY)."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
CLI = os.path.join(HERE, '..', 'cli', 'forge_check.py')
EXAMPLE = os.path.join(HERE, '..', 'examples', 'wallet-cap')
IMPL, SPEC = 'impl/Wallet.dfy', 'spec/WalletSpec.dfy'
CONFIG, APPROVED = '.forge/forge.json', '.forge/approved.json'
EXAMPLES, DECISIONS = 'spec/WalletExamples.dfy', '.forge/decisions/wallet-cap.md'


def edit(root, rel, old, new):
    path = os.path.join(root, rel)
    text = open(path, encoding='utf-8').read()
    assert old in text, (rel, old)
    open(path, 'w', encoding='utf-8').write(text.replace(old, new, 1))


def git(root, *args):
    subprocess.run(['git', '-C', root, '-c', 'user.name=t', '-c', 'user.email=t@example.invalid',
                    '-c', 'commit.gpgsign=false'] + list(args), check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def no_change(root):
    pass


def bug(root):  # off by one: lets the total pass the cap
    edit(root, IMPL, 's.spent + amount <= s.cap {', 's.spent + amount <= s.cap + 1 {')


def weaken_spec(root):
    edit(root, SPEC, '    ensures Valid(next)\n', '')


def swap_spec(root):  # the approved spec stays; the code refines a new weak spec
    open(os.path.join(root, 'spec/Weak.dfy'), 'w').write(
        'abstract module Weak {\n  datatype Outcome = Ok | Rejected\n'
        '  datatype State = Wallet(cap: int, spent: int)\n'
        '  method Spend(s: State, amount: int) returns (next: State, outcome: Outcome)\n}\n')
    open(os.path.join(root, IMPL), 'w').write(
        'include "../spec/WalletSpec.dfy"\ninclude "../spec/Weak.dfy"\n\n'
        'module Wallet refines Weak {\n  method Spend ... { next := Wallet(0, 1000000); outcome := Ok; }\n}\n')


def assume_false(root):
    edit(root, IMPL, '  method Spend ... {\n', '  method Spend ... {\n    assume false;\n')


def assume_in_comment(root):  # a comment must not trip the scan
    edit(root, IMPL, '  method Spend ... {\n', '  method Spend ... {\n    // we never assume anything\n')


def drop_check(root):  # config changed after approval
    edit(root, CONFIG, '"wallet-cap"', '"wallet"')


def unapprove(root):
    open(os.path.join(root, APPROVED), 'w').write('{}\n')


def forge_approval(root):  # weaken the spec AND rewrite the approval to match
    import hashlib
    weaken_spec(root)
    h = lambda p: hashlib.sha256(open(os.path.join(root, p), 'rb').read()).hexdigest()
    json.dump({f: h(f) for f in [CONFIG, SPEC, EXAMPLES, DECISIONS]}, open(os.path.join(root, APPROVED), 'w'))


def add_helper(root, body):
    """Adds impl/Helper.dfy as a second include, in a project whose approved binding allows it."""
    import hashlib
    open(os.path.join(root, 'impl/Helper.dfy'), 'w').write(body)
    old = 'include "../spec/WalletSpec.dfy"\n\nmodule Wallet refines WalletSpec {'
    new = 'include "../spec/WalletSpec.dfy"\ninclude "Helper.dfy"\n\nmodule Wallet refines WalletSpec {'
    edit(root, IMPL, old, new)
    cfg = json.load(open(os.path.join(root, CONFIG)))
    cfg['checks'][0]['binding']['prefix'] = new
    json.dump(cfg, open(os.path.join(root, CONFIG), 'w'))
    h = lambda p: hashlib.sha256(open(os.path.join(root, p), 'rb').read()).hexdigest()
    json.dump({f: h(f) for f in [CONFIG, SPEC, EXAMPLES, DECISIONS]}, open(os.path.join(root, APPROVED), 'w'))


def helper_with_assume(root):  # an included file that no check lists
    add_helper(root, 'module Helper {\n  lemma Anything()\n    ensures 1 == 2\n  { assume false; }\n}\n')


def helper_with_bug(root):  # an included, unlisted file whose proof fails
    add_helper(root, 'module Helper {\n  lemma Wrong()\n    ensures 1 == 2\n  { }\n}\n')


def helper_ok(root):
    add_helper(root, 'module Helper {\n  lemma Fine()\n    ensures 1 + 1 == 2\n  { }\n}\n')


def reapprove(root):
    import hashlib
    h = lambda p: hashlib.sha256(open(os.path.join(root, p), 'rb').read()).hexdigest()
    json.dump({f: h(f) for f in [CONFIG, SPEC, EXAMPLES, DECISIONS]}, open(os.path.join(root, APPROVED), 'w'))


def spec_contradicts_answer(root):  # spec now allows a partial spend; the person said no (D2)
    edit(root, SPEC, '    ensures !(0 < amount && s.spent + amount <= s.cap) ==>\n      outcome == Rejected && next == s\n',
         '    ensures (amount <= 0) ==> outcome == Rejected && next == s\n')
    reapprove(root)


def spec_leaves_answer_open(root):  # spec no longer says what happens to a zero amount (D3)
    edit(root, SPEC, '    ensures !(0 < amount && s.spent + amount <= s.cap) ==>\n      outcome == Rejected && next == s\n',
         '    ensures !(0 <= amount && s.spent + amount <= s.cap) ==>\n      outcome == Rejected && next == s\n')
    reapprove(root)


def answer_edited(root):  # someone flips an answer after approval
    edit(root, EXAMPLES, 'assert outcome == S.Rejected && next.spent == 0;', 'assert next.spent == 0;')


def decision_record_edited(root):
    edit(root, DECISIONS, '| Reject; never a partial spend |', '| Allow a partial spend |')


def edit_emitted(root):  # hand edit of emitted code
    path = os.path.join(root, 'verified/wallet-cap/py/wallet_cap-py/Wallet.py')
    text = open(path).read()
    assert '<=' in text
    open(path, 'w').write(text.replace('<=', '<', 1))


def run_built_python(root):  # importing the built module creates __pycache__ inside verified/
    subprocess.run([sys.executable, os.path.join(root, 'app.py')], stdout=subprocess.DEVNULL, check=True)


def wrong_expect(root):  # the harness expects something the code does not do
    edit(root, 'fuzz/WalletFuzz.dfy', 'expect next.cap == cap, "W2";', 'expect next.cap == cap + 1, "W2";')


def unwire(root):  # the app stops importing the built code
    edit(root, 'app.py', "'verified', 'wallet-cap', 'py', 'wallet_cap-py'", "'old_wallet'")


# name, change, expected verdict, expected exit code, use --base
CASES = [
    ('unchanged example', no_change, 'VERIFIED', 0, False),
    ('off-by-one bug in the code', bug, 'FAILED', 1, False),
    ('spec weakened', weaken_spec, 'SPEC_CHANGED', 1, False),
    ('code refines a new weak spec', swap_spec, 'BINDING_BROKEN', 1, False),
    ('assume false', assume_false, 'UNSOUND_CONSTRUCT', 1, False),
    ('the word assume in a comment', assume_in_comment, 'VERIFIED', 0, False),
    ('forge.json changed', drop_check, 'CONFIG_CHANGED', 1, False),
    ('approval file emptied', unapprove, 'CONFIG_CHANGED', 1, False),
    ('spec weakened and approval rewritten, no --base', forge_approval, 'VERIFIED', 0, False),
    ('spec weakened and approval rewritten, with --base', forge_approval, 'APPROVAL_CHANGED', 1, True),
    ('code-only change, with --base', assume_in_comment, 'VERIFIED', 0, True),
    ('assume in an included file that no check lists', helper_with_assume, 'UNSOUND_CONSTRUCT', 1, False),
    ('failing proof in an included file that no check lists', helper_with_bug, 'FAILED', 1, False),
    ('a sound included file', helper_ok, 'VERIFIED', 0, False),
    ('spec contradicts an answer (D2), re-approved', spec_contradicts_answer, 'EXAMPLES_FAILED', 1, False),
    ('spec leaves an answer open (D3), re-approved', spec_leaves_answer_open, 'EXAMPLES_FAILED', 1, False),
    ('an answer edited after approval', answer_edited, 'SPEC_CHANGED', 1, False),
    ('decision record edited after approval', decision_record_edited, 'SPEC_CHANGED', 1, False),
    ('answers file changed, with --base', answer_edited, 'APPROVAL_CHANGED', 1, True),
]

# `build --check` cases: name, change, expected build verdict, expected exit code
BUILD_CASES = [
    ('emitted code equals a fresh build', no_change, 'BUILT', 0),
    ('app ran the built code (__pycache__ appears)', run_built_python, 'BUILT', 0),
    ('emitted code edited by hand', edit_emitted, 'STALE_BUILD', 1),
    ('fuzz harness expects a wrong result', wrong_expect, 'FUZZ_FAILED', 1),
    ('app no longer imports the built code', unwire, 'NOT_WIRED', 1),
]


def static_checks():
    """Mistakes that only show up inside Claude Code, where CI cannot run the mod."""
    problems = []
    src = open(os.path.join(HERE, '..', 'hooks', 'register.ts'), encoding='utf-8').read()
    for m in re.finditer(r'timeoutMs:\s*([0-9_]+)', src):
        if int(m.group(1).replace('_', '')) > 600000:
            problems.append('register.ts: timeoutMs %s is over the host limit of 600000' % m.group(1))
    code = re.sub(r'//[^\n]*', '', src)
    if '$.prompt.submit(' in code:
        problems.append('register.ts calls $.prompt.submit: the host refuses it inside command.run; '
                        'use a Markdown command in commands/ instead')
    for name in os.listdir(os.path.join(HERE, '..', 'commands')):
        text = open(os.path.join(HERE, '..', 'commands', name), encoding='utf-8').read()
        if not text.startswith('---\ndescription:'):
            problems.append('commands/%s has no description front matter' % name)
        # A Markdown command named like a /forge subcommand shadows it: "/forge approve" ran commands/approve.md
        # instead of the approval (found in v0.7.1).
        if name[:-3] in ('approve', 'check', 'build', 'status', 'spec', 'diff', 'mutate'):
            problems.append('commands/%s shadows the fixed step "/forge %s"' % (name, name[:-3]))
    return problems


def main():
    failed = 0
    for problem in static_checks():
        failed += 1
        print('FAIL  static: ' + problem)
    for name, change, want_verdict, want_exit, use_base in CASES:
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, 'p')
            shutil.copytree(EXAMPLE, root)
            cmd = [sys.executable, CLI, 'check', '--root', root, '--json']
            if use_base:
                git(root, 'init', '-q')
                git(root, 'add', '.')
                git(root, 'commit', '-q', '-m', 'base')
                git(root, 'branch', 'base')
                cmd += ['--base', 'base']
            change(root)
            r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
            try:
                verdicts = list(json.loads(r.stdout)['verdicts'].values())
            except (ValueError, KeyError):
                verdicts = ['<no result: %s %s>' % (r.stdout.strip()[:200], r.stderr.strip()[:200])]
            ok = verdicts == [want_verdict] and r.returncode == want_exit
            failed += not ok
            print('%s  %-52s want %s/%d  got %s/%d' % ('ok  ' if ok else 'FAIL', name, want_verdict,
                                                       want_exit, ','.join(verdicts), r.returncode))
    extra = 0
    for name, change, want_verdict, want_exit in BUILD_CASES:
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, 'p')
            shutil.copytree(EXAMPLE, root)
            change(root)
            r = subprocess.run([sys.executable, CLI, 'build', '--check', '--root', root, '--json'],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
            try:
                got = [b['verdict'] for b in json.loads(r.stdout)['build'].values()]
                notes = [n for b in json.loads(r.stdout)['build'].values() for n in b['notes']]
            except (ValueError, KeyError):
                got, notes = ['<no result: %s %s>' % (r.stdout.strip()[:200], r.stderr.strip()[:200])], []
            ok = got == [want_verdict] and r.returncode == want_exit
            failed += not ok
            print('%s  %-52s want %s/%d  got %s/%d' % ('ok  ' if ok else 'FAIL', 'build: ' + name, want_verdict,
                                                       want_exit, ','.join(got), r.returncode))
            if not ok:
                print('      ' + ' | '.join(notes)[:400])
    # approval through `spec --json` then `approve --hashes` (the path the mod uses)
    for name, tamper, want_exit, want_verdict in [
            ('approve exactly what was shown', False, 0, 'VERIFIED'),
            ('answers changed between showing and approving', True, 1, 'CONFIG_CHANGED')]:
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, 'p')
            shutil.copytree(EXAMPLE, root)
            open(os.path.join(root, APPROVED), 'w').write('{}\n')
            shown = json.loads(subprocess.run([sys.executable, CLI, 'spec', '--root', root, '--json'],
                                              stdout=subprocess.PIPE, universal_newlines=True).stdout)['hashes']
            if tamper:
                edit(root, EXAMPLES, '// D3:', '// D3 (edited):')
            r = subprocess.run([sys.executable, CLI, 'approve', '--root', root, '--hashes', json.dumps(shown)],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
            c = subprocess.run([sys.executable, CLI, 'check', '--root', root, '--json'],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
            got = list(json.loads(c.stdout)['verdicts'].values())
            covered = sorted(shown) == sorted([CONFIG, SPEC, EXAMPLES, DECISIONS])
            ok = r.returncode == want_exit and got == [want_verdict] and covered
            failed += not ok
            extra += 1
            print('%s  %-52s want approve %d, %s  got %d, %s%s' % ('ok  ' if ok else 'FAIL', 'approval: ' + name,
                  want_exit, want_verdict, r.returncode, ','.join(got), '' if covered else ' (hashes miss a file)'))
    # mutation testing: a strong spec catches every planted bug; a weakened one lets some survive
    for name, weaken, want_survivors in [('strong spec: every planted bug caught', False, False),
                                         ('weakened spec (W4 removed): survivors reported', True, True)]:
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, 'p')
            shutil.copytree(EXAMPLE, root)
            if weaken:
                import hashlib
                edit(root, SPEC, '    ensures !(0 < amount && s.spent + amount <= s.cap) ==>\n      outcome == Rejected && next == s\n', '')
                cfg = json.load(open(os.path.join(root, CONFIG)))
                for key in ('examples', 'decisions'):
                    cfg['checks'][0].pop(key)
                json.dump(cfg, open(os.path.join(root, CONFIG), 'w'))
                h = lambda q: hashlib.sha256(open(os.path.join(root, q), 'rb').read()).hexdigest()
                json.dump({CONFIG: h(CONFIG), SPEC: h(SPEC)}, open(os.path.join(root, APPROVED), 'w'))
            before = open(os.path.join(root, IMPL)).read()
            r = subprocess.run([sys.executable, CLI, 'mutate', '--root', root, '--json'],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
            try:
                out = json.loads(r.stdout)
                caught, survived = out['caught'], out['survived']
            except (ValueError, KeyError):
                caught, survived = -1, -1
            unchanged = open(os.path.join(root, IMPL)).read() == before
            ok = r.returncode == 0 and caught > 0 and (survived > 0) == want_survivors and unchanged
            failed += not ok
            extra += 1
            print('%s  %-52s caught %d, survived %d%s' % ('ok  ' if ok else 'FAIL', 'mutate: ' + name, caught, survived,
                  '' if unchanged else ' (PROJECT CHANGED)'))
            if not ok:
                print('      ' + (r.stderr or r.stdout)[-400:])
    # differential fuzz: shipped code and wrapper against the compiled proved rule
    def diff(root, *extra_args):
        r = subprocess.run([sys.executable, CLI, 'diff', '--root', root, '--json', '--seed', '7'] + list(extra_args),  # writes stats
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        try:
            return r.returncode, json.loads(r.stdout)['checks']
        except (ValueError, KeyError):
            return r.returncode, {'error': (r.stderr or r.stdout)[-300:]}

    def fix_legacy(root):
        open(os.path.join(root, 'legacy_wallet.py'), 'w').write(
            'def legacy_spend(cap, spent, amount):\n'
            '    if 0 < amount and spent + amount <= cap:\n        return True, spent + amount\n    return False, spent\n')
        open(os.path.join(root, 'legacy_wallet.ts'), 'w').write(
            'export function legacySpend(cap: number, spent: number, amount: number) {\n'
            '  if (0 < amount && spent + amount <= cap) return { ok: true, spent: spent + amount }\n'
            '  return { ok: false, spent }\n}\n')

    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, 'p')
        shutil.copytree(EXAMPLE, root)
        code, res = diff(root)
        py, js = res.get('wallet-cap:py', {}), res.get('wallet-cap:js', {})
        kinds_py = sorted({json.dumps(d['reference']['ok']) + '>' + json.dumps(d['got']['ok']) for d in py.get('disagreements', [])})
        failing_py = sorted(f['id'][:2] for f in (py.get('decisions') or {}).get('failing', []))
        failing_js = sorted(f['id'][:2] for f in (js.get('decisions') or {}).get('failing', []))
        cases = [
            ('diff: shipped Python bugs found (exact cap, zero, negative)', code == 1 and py.get('verdict') == 'DISAGREE'
             and failing_py == ['D1', 'D3', 'D4']),
            ('diff: the wrapper agrees with the proved rule', (py.get('per_candidate') or {}).get('app-wrapper') == 0),
            ('diff: shipped TypeScript bugs found (zero, negative)', js.get('verdict') == 'DISAGREE'
             and failing_js == ['D3', 'D4']),
            ('diff: each run under 2 seconds', py.get('ms', 9999) < 2000 and js.get('ms', 9999) < 2000),
            ('diff: random search adds no duplicate of a failing decision', len(py.get('disagreements', [])) == 0),
            ('diff: recorded decisions run as fixed inputs (legacy fails D1, D3, D4; the wrapper agrees)',
             (py.get('decisions') or {}).get('total') == 4
             and sorted(f['id'][:2] for f in (py.get('decisions') or {}).get('failing', [])) == ['D1', 'D3', 'D4']
             and all(f['candidates'] == ['legacy'] for f in (py.get('decisions') or {}).get('failing', []))),
        ]
        fix_legacy(root)
        code2, res2 = diff(root, '--inputs', '0')
        cases.append(('diff: after the shipped code is fixed, every decision agrees',
                      code2 == 0 and all(r.get('verdict') == 'DIFF_OK' and r['decisions']['agree'] == 4 for r in res2.values())))
        edit(root, IMPL, 's.spent + amount <= s.cap {', 's.spent + amount <= s.cap && true {')
        code3, res3 = diff(root)
        cases.append(('diff: an outdated build is detected', code3 == 1 and all(r.get('verdict') == 'BUILD_STALE' for r in res3.values())))
        stats = json.load(open(os.path.join(root, '.forge/diff-stats.json')))
        cases.append(('diff: search totals kept across runs (saturation)',
                      stats.get('wallet-cap:py', {}).get('runs') == 2 and stats['wallet-cap:py']['runs_since_new'] == 1
                      and stats['wallet-cap:py']['failing_decisions'] == []
                      and stats['wallet-cap:py']['inputs'] >= 1500))
        for name, ok in cases:
            failed += not ok
            extra += 1
            print('%s  %s' % ('ok  ' if ok else 'FAIL', name))
            if not ok:
                print('      ' + json.dumps({k: {kk: v.get(kk) for kk in ('verdict', 'per_candidate', 'ms', 'error')} if isinstance(v, dict) else v
                                             for k, v in res.items()})[:500])
    print('%d cases, %d failed' % (len(CASES) + len(BUILD_CASES) + extra, failed))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
