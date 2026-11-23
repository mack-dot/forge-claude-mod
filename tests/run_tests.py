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
import time

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


def approve_as_shown(root):  # the mod's approval path: spec --json, then approve --hashes
    shown = json.loads(subprocess.run([sys.executable, CLI, 'spec', '--root', root, '--json'],
                                      stdout=subprocess.PIPE, universal_newlines=True).stdout)['hashes']
    subprocess.run([sys.executable, CLI, 'approve', '--root', root, '--hashes', json.dumps(shown)],
                   stdout=subprocess.DEVNULL, check=True)


def legacy_note_edited(root):  # an approval from before 0.13 holds the full-file hash: a note edit still counts
    edit(root, DECISIONS, '- Who may spend (authorization).', '- Who may spend (authorization; see the auth rule).')


def out_of_scope_note_edited(root):  # prose only: no new approval needed (0.13)
    if not open(os.path.join(root, APPROVED)).read().count('"n:'):
        approve_as_shown(root)
        if os.path.isdir(os.path.join(root, '.git')):
            git(root, 'add', '.')
            git(root, 'commit', '-q', '-m', 'approved in the 0.13 form')
            git(root, 'branch', '-f', 'base')
    edit(root, DECISIONS, '- Who may spend (authorization).', '- Who may spend (authorization; see the auth rule).')


def broken_ts_app(root):  # a missing brace in a file the check touches
    edit(root, 'legacy_wallet.ts', '  return { ok: false, spent }\n}', '  return { ok: false, spent }\n')


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
    ('out-of-scope note edited after approval', out_of_scope_note_edited, 'VERIFIED', 0, False),
    ('note edited under an approval from before 0.13', legacy_note_edited, 'SPEC_CHANGED', 1, False),
    ('out-of-scope note edited, with --base', out_of_scope_note_edited, 'VERIFIED', 0, True),
    ('decision record edited, with --base', decision_record_edited, 'APPROVAL_CHANGED', 1, True),
    ('answers file changed, with --base', answer_edited, 'APPROVAL_CHANGED', 1, True),
]

# `build --check` cases: name, change, expected build verdict, expected exit code
BUILD_CASES = [
    ('emitted code equals a fresh build', no_change, 'BUILT', 0),
    ('app ran the built code (__pycache__ appears)', run_built_python, 'BUILT', 0),
    ('emitted code edited by hand', edit_emitted, 'STALE_BUILD', 1),
    ('fuzz harness expects a wrong result', wrong_expect, 'FUZZ_FAILED', 1),
    ('app no longer imports the built code', unwire, 'NOT_WIRED', 1),
    ('a touched app file does not parse (missing brace)', broken_ts_app, 'APP_SYNTAX_ERROR', 1),
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
    # one shared Rust build folder for every test project (compiled dependencies are reused)
    os.environ.setdefault('FORGE_CARGO_TARGET_DIR', os.path.join(tempfile.gettempdir(), 'forge-test-cargo-target'))
    for problem in static_checks():
        failed += 1
        print('FAIL  static: ' + problem)
    for name, change, want_verdict, want_exit, use_base in CASES:
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, 'p')
            shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns('cache', 'regressions', '__pycache__'))
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
            shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns('cache', 'regressions', '__pycache__'))
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
            shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns('cache', 'regressions', '__pycache__'))
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
    # the approval digest: shorter, but every clause line of the spec and every decision row is in it
    full = subprocess.run([sys.executable, CLI, 'spec', '--root', EXAMPLE, '--full'], stdout=subprocess.PIPE,
                          universal_newlines=True).stdout
    with tempfile.TemporaryDirectory() as tmp:  # nothing approved yet: the digest shows every rule
        shutil.copytree(EXAMPLE, os.path.join(tmp, 'p'), ignore=shutil.ignore_patterns('cache', 'regressions', '__pycache__'))
        open(os.path.join(tmp, 'p', APPROVED), 'w').write('{}\n')
        dig = subprocess.run([sys.executable, CLI, 'spec', '--root', os.path.join(tmp, 'p')], stdout=subprocess.PIPE,
                             universal_newlines=True).stdout
    again = subprocess.run([sys.executable, CLI, 'spec', '--root', EXAMPLE], stdout=subprocess.PIPE,
                           universal_newlines=True).stdout
    ok_again = 'approved, unchanged' in again and 'ensures' not in again
    failed += not ok_again
    extra += 1
    print('%s  approval digest: approved, unchanged files shown as one line (%d characters)' % (
        'ok  ' if ok_again else 'FAIL', len(again)))
    spec_lines = [l.rstrip() for l in open(os.path.join(EXAMPLE, SPEC)).read().split('\n')
                  if re.match(r'\s*(requires|ensures|predicate|function|datatype|//)', l) or l.startswith('      ')]
    rows = [l for l in open(os.path.join(EXAMPLE, DECISIONS)).read().split('\n') if l.startswith('| D')]
    ok = len(dig) < len(full) and all(l in dig for l in spec_lines + rows) and '/forge approve --full' in dig
    failed += not ok
    extra += 1
    print('%s  approval digest: %d of %d characters, all %d spec clause lines and %d decisions kept' % (
        'ok  ' if ok else 'FAIL', len(dig), len(full), len(spec_lines), len(rows)))
    # the syntax step on a machine with no git and a Node without a TypeScript parser (found in CI): no false alarm
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, 'p')
        shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns('cache', 'regressions', '__pycache__'))
        bin_dir = os.path.join(tmp, 'bin')
        os.makedirs(bin_dir)
        os.symlink(sys.executable, os.path.join(bin_dir, 'python3'))
        with open(os.path.join(bin_dir, 'node'), 'w') as f:
            f.write('#!/bin/sh\nexit 3\n')  # an old Node: syntax_check.mjs exits 3 when it cannot parse TypeScript
        os.chmod(os.path.join(bin_dir, 'node'), 0o755)
        env = dict(os.environ, PATH=bin_dir)
        c = subprocess.run([sys.executable, CLI, 'check', '--root', root, '--json', '--no-write'], env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        try:
            res = json.loads(c.stdout)
        except ValueError:
            res = {'verdicts': {}, 'warnings': ['<no result> ' + c.stderr[-300:]]}
        ok = (c.returncode == 0 and list(res['verdicts'].values()) == ['VERIFIED']
              and not any(w.startswith('APP_SYNTAX_ERROR') for w in res.get('warnings', [])))
        failed += not ok
        extra += 1
        print('%s  syntax step: no git and a Node without TypeScript parsing: not checked, no false alarm' % (
            'ok  ' if ok else 'FAIL'))
        if not ok:
            print('      ' + json.dumps(res.get('warnings'))[:400] + c.stderr[-300:])
    # a decision record that names code: approval remembers that code; a later change to it is flagged (never a
    # failed verdict), and an out-of-scope item that names code is offered as the next rule
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, 'p')
        shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns('cache', 'regressions', '__pycache__'))
        edit(root, DECISIONS, '- Several wallets sharing one cap; concurrency; persistence.',
             '- Several wallets sharing one cap; concurrency; persistence.\n'
             '- Input checks before `legacySpend` is called (legacy_wallet.ts): assumed done by the caller.')
        shown = json.loads(subprocess.run([sys.executable, CLI, 'spec', '--root', root, '--json'],
                                          stdout=subprocess.PIPE, universal_newlines=True).stdout)['hashes']
        subprocess.run([sys.executable, CLI, 'approve', '--root', root, '--hashes', json.dumps(shown)],
                       stdout=subprocess.DEVNULL, check=True)
        def chk():
            c = subprocess.run([sys.executable, CLI, 'check', '--root', root, '--json'],
                               stdout=subprocess.PIPE, universal_newlines=True)
            return c.returncode, json.loads(c.stdout)
        code0, before = chk()
        edit(root, 'legacy_wallet.ts', 'export interface Result', 'export interface Result /* changed */')
        code1, after = chk()
        nxt = (before.get('next_rule') or {}).get('wallet-cap', {})
        cases = [
            ('notes: nothing flagged while the described code is unchanged (negative control)',
             code0 == 0 and before['warnings'] == []),
            ('notes: change to described code flagged DECISION_NOTE_STALE, verdict stays VERIFIED',
             code1 == 0 and list(after['verdicts'].values()) == ['VERIFIED']
             and len(after['warnings']) == 1 and after['warnings'][0].startswith('DECISION_NOTE_STALE')
             and 'legacy_wallet.ts' in after['warnings'][0]),
            ('next rule: the out-of-scope item that names code is suggested',
             nxt.get('code') == ['legacy_wallet.ts'] and 'legacySpend' in nxt.get('item', '')),
        ]
        for name, ok in cases:
            failed += not ok
            extra += 1
            print('%s  %s' % ('ok  ' if ok else 'FAIL', name))
            if not ok:
                print('      ' + json.dumps({'before': before.get('warnings'), 'after': after.get('warnings'),
                                             'next': before.get('next_rule')})[:600])
    # mutation testing: a strong spec catches every planted bug; a weakened one lets some survive
    for name, weaken, want_survivors in [('strong spec: every planted bug caught', False, False),
                                         ('weakened spec (W4 removed): survivors reported', True, True)]:
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, 'p')
            shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns('cache', 'regressions', '__pycache__'))
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
        open(os.path.join(root, 'legacygo', 'legacy.go'), 'w').write(
            'package legacygo\n\nfunc Spend(cap, spent, amount int64) (bool, int64) {\n'
            '\tif 0 < amount && amount <= cap-spent {\n\t\treturn true, spent + amount\n\t}\n\treturn false, spent\n}\n')
        open(os.path.join(root, 'legacyjava', 'LegacyWallet.java'), 'w').write(
            'package legacyjava;\npublic final class LegacyWallet {\n  public static long[] spend(long cap, long spent, long amount) {\n'
            '    if (amount > 0 && amount <= cap - spent) return new long[] {1, spent + amount};\n    return new long[] {0, spent};\n  }\n}\n')
        open(os.path.join(root, 'legacy_wallet.rs'), 'w').write(
            'pub fn spend(cap: u64, spent: u64, amount: i64) -> (bool, u64) {\n'
            '    if amount > 0 && (amount as u64) <= cap - spent { (true, spent + amount as u64) } else { (false, spent) }\n}\n')

    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, 'p')
        shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns('cache', 'regressions', '__pycache__'))
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
            ('diff: at most 3 differently shaped examples per kind of wrong answer, never a decision input again',
             all(n <= 3 for n in __import__('collections').Counter((d['candidate'], d.get('group')) for d in py.get('disagreements', [])).values())
             and not any(d['input'] in [{'cap': 5000, 'spent': 2000, 'amount': 3000}, {'cap': 5000, 'spent': 0, 'amount': 0},
                                        {'cap': 5000, 'spent': 3000, 'amount': -1000}] for d in py.get('disagreements', []))),
            ('diff: recorded decisions run as fixed inputs (legacy fails D1, D3, D4; the wrapper agrees)',
             (py.get('decisions') or {}).get('total') == 4
             and sorted(f['id'][:2] for f in (py.get('decisions') or {}).get('failing', [])) == ['D1', 'D3', 'D4']
             and all(f['candidates'] == ['legacy'] for f in (py.get('decisions') or {}).get('failing', []))),
        ]
        fix_legacy(root)
        code2, res2 = diff(root, '--inputs', '0')
        js2 = res2.get('wallet-cap:js', {})
        cases.append(('diff: after the shipped code is fixed, every decision agrees',
                      all(r['decisions']['agree'] == 4 for r in res2.values())
                      and res2.get('wallet-cap:py', {}).get('verdict') == 'DIFF_OK'))
        # The "fixed" TypeScript still uses plain numbers: above 2^53 it loses precision, and the fuzz keeps saying so.
        code_big, res_big = diff(root, '--inputs', '3000')
        jsb = res_big.get('wallet-cap:js', {})
        cases.append(('diff: finds the precision loss above 2^53 in TypeScript number code',
                      any(d['input']['cap'] > 2 ** 53 for d in jsb.get('disagreements', []))))
        edit(root, IMPL, 's.spent + amount <= s.cap {', 's.spent + amount <= s.cap && true {')
        code3, res3 = diff(root)
        cases.append(('diff: an outdated build is detected', code3 == 1 and all(r.get('verdict') == 'BUILD_STALE' for r in res3.values())))
        stats = json.load(open(os.path.join(root, '.forge/diff-stats.json')))
        # Go and Rust evaluators (built by forge in .forge/cache), from the first run above
        go_res, rs_res = res.get('wallet-cap:go', {}), res.get('wallet-cap:rs', {})
        cases.append(('diff: Go evaluator finds the shipped Go bugs (exact cap, zero, negative)',
                      sorted(f['id'][:2] for f in (go_res.get('decisions') or {}).get('failing', [])) == ['D1', 'D3', 'D4']))
        cases.append(('diff: Rust evaluator finds zero accepted and the overflow panic',
                      [f['id'][:2] for f in (rs_res.get('decisions') or {}).get('failing', [])] == ['D3']
                      and any('overflow' in json.dumps(d['got']) for d in rs_res.get('disagreements', []))))
        cases.append(('diff: Go and Rust runs under 2 seconds once built', go_res.get('ms', 9999) < 2000 and rs_res.get('ms', 9999) < 2000))
        java_res = res.get('wallet-cap:java', {})
        cases.append(('diff: Java evaluator finds the exact-cap bug and the long wraparound',
                      [f['id'][:2] for f in (java_res.get('decisions') or {}).get('failing', [])] == ['D1']
                      and any(isinstance(d.get('got'), dict) and d['got'].get('ok') is True
                              and isinstance(d['got'].get('spent'), int) and d['got']['spent'] < 0
                              for d in java_res.get('disagreements', []))))
        # big integers cross the line protocol exactly (JSON.parse alone would round 2^63 - 1)
        modules = os.path.join(os.environ.get('XDG_CACHE_HOME') or os.path.expanduser('~/.cache'), 'forge-check', 'js', 'node_modules')
        p = subprocess.Popen(['node', os.path.join(HERE, '..', 'cli', 'eval', 'node_eval.mjs'), os.path.join(root, 'diff/wallet_diff.mjs')],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                             env=dict(os.environ, NODE_PATH=modules), cwd=root)
        out, err = p.communicate(json.dumps({'input': {'cap': 2 ** 63 - 1, 'spent': 2 ** 63 - 2, 'amount': 1}}) + '\n', timeout=60)
        lines = [json.loads(l) for l in out.strip().split('\n')]
        exact = lines[1]['reference'] == {'ok': True, 'spent': 2 ** 63 - 1}
        rounded = lines[1]['candidates']['legacy-ts']['spent'] != 2 ** 63 - 1  # the shipped code uses plain numbers
        cases.append(('diff: integers above 2^53 cross the protocol exactly', exact and rounded))
        # any language through "cmd": here the Python evaluator, named as a plain program
        cfg = json.load(open(os.path.join(root, CONFIG)))
        cfg['checks'][0]['diff'] = [{'name': 'any', 'lang': 'cmd', 'inputs': 'diff/wallet_inputs.py',
                                     'run': [sys.executable, os.path.join(HERE, '..', 'cli', 'eval', 'py_eval.py'),
                                             'diff/wallet_diff.py']}]
        json.dump(cfg, open(os.path.join(root, CONFIG), 'w'))
        shutil.rmtree(os.path.join(root, 'verified'), ignore_errors=True)
        shutil.copytree(os.path.join(EXAMPLE, 'verified'), os.path.join(root, 'verified'))
        edit(root, IMPL, 's.spent + amount <= s.cap && true {', 's.spent + amount <= s.cap {')
        code4, res4 = diff(root)
        cmd_res = res4.get('wallet-cap', {})
        cases.append(('diff: any language through a "cmd" evaluator (protocol only)',
                      cmd_res.get('verdict') == 'DIFF_OK' and cmd_res.get('decisions', {}).get('agree') == 4))
        # a hanging function is reported, not waited on
        open(os.path.join(root, 'diff/wallet_diff.py'), 'a').write(
            "\ndef _hang(i):\n    while True:\n        pass\n\ncandidates['hangs'] = _hang\n")
        t0 = time.time()
        code5, res5 = diff(root)
        cases.append(('diff: a hanging function is reported within seconds',
                      res5.get('wallet-cap', {}).get('verdict') == 'DIFF_ERROR'
                      and 'no answer within' in res5['wallet-cap'].get('error', '') and time.time() - t0 < 30))
        cases.append(('diff: search totals kept across runs (saturation)',
                      stats.get('wallet-cap:py', {}).get('runs') == 3 and stats['wallet-cap:py']['runs_since_new'] == 2
                      and stats['wallet-cap:py']['failing_decisions'] == []
                      and stats['wallet-cap:py']['inputs'] >= 1500))
        for name, ok in cases:
            failed += not ok
            extra += 1
            print('%s  %s' % ('ok  ' if ok else 'FAIL', name))
            if not ok:
                print('      ' + json.dumps({k: {kk: v.get(kk) for kk in ('verdict', 'per_candidate', 'ms', 'error')} if isinstance(v, dict) else v
                                             for k, v in res.items()})[:500])
    # Two different bugs that give the SAME wrong answer must both be reported (a tester's run hid three bugs behind
    # one kind of wrong answer before 0.12.2).
    sys.path.insert(0, os.path.join(HERE, '..', 'cli'))
    import forge_diff
    with tempfile.TemporaryDirectory() as tmp:
        open(os.path.join(tmp, 'inputs.py'), 'w').write(
            'def generate(rng):\n    return {"x": rng.pick([0, 1, 5, -3, "", "a", None])}\n')
        open(os.path.join(tmp, 'adapter.py'), 'w').write(
            'def reference(i):\n    return {"allowed": isinstance(i["x"], int) and i["x"] > 0}\n'
            'def buggy(i):\n    x = i["x"]\n    if x == 0 or x == "":\n        return {"allowed": True}\n'
            '    return {"allowed": isinstance(x, int) and x > 0}\n'
            'candidates = {"buggy": buggy}\n')
        res = forge_diff.run_entry([sys.executable, os.path.join(HERE, '..', 'cli', 'eval', 'py_eval.py'),
                                    os.path.join(tmp, 'adapter.py')], tmp, dict(os.environ), os.path.join(tmp, 'inputs.py'),
                                   7, 500, 5000, os.path.join(tmp, 'reg'))
        found = sorted(json.dumps(d['input']['x']) for d in res.get('disagreements', []))
        ok = found == ['""', '0'] and len({d['group'] for d in res['disagreements']}) == 1
        failed += not ok
        extra += 1
        print('%s  diff: two bugs with the same wrong answer (zero and empty text) are both reported  %s' % ('ok  ' if ok else 'FAIL', found))

    # INVARIANTS.md: generated from the approved rules; CI catches a stale copy
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, 'p')
        shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns('cache', 'regressions', '__pycache__'))
        inv = lambda *a: subprocess.run([sys.executable, CLI, 'invariants', '--root', root] + list(a),
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        r1 = inv('--check')
        text = open(os.path.join(root, 'INVARIANTS.md')).read()
        edit(root, DECISIONS, '| Reject; never a partial spend |', '| Allow a partial spend |')
        r2 = inv('--check')
        inv()
        new = open(os.path.join(root, 'INVARIANTS.md')).read()
        cases_inv = [
            ('invariants: the committed file equals a fresh generation', r1.returncode == 0),
            ('invariants: plain-words guarantees, decisions and owner\'s words included',
             '**W1** the total spent never goes over the cap.' in text and '| D2 |' in text
             and 'A wallet must never spend more than its cap.' in text),
            ('invariants: a changed decision makes the committed file stale (CI fails)',
             r2.returncode == 1 and 'INVARIANTS_STALE' in r2.stdout),
            ('invariants: after regeneration the change is a readable diff and approval shows as not approved',
             'Allow a partial spend' in new and 'NOT approved as written' in new),
        ]
        for name, ok in cases_inv:
            failed += not ok
            extra += 1
            print('%s  %s' % ('ok  ' if ok else 'FAIL', name))
    print('%d cases, %d failed' % (len(CASES) + len(BUILD_CASES) + extra, failed))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
