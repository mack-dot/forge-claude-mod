#!/usr/bin/env python3
"""Tests of cli/forge_check.py: each case copies examples/wallet-cap, changes it,
and requires one verdict and one exit code. Needs Dafny (PATH or FORGE_DAFNY)."""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
CLI = os.path.join(HERE, '..', 'cli', 'forge_check.py')
EXAMPLE = os.path.join(HERE, '..', 'examples', 'wallet-cap')
IMPL, SPEC = 'impl/Wallet.dfy', 'spec/WalletSpec.dfy'
CONFIG, APPROVED = '.forge/forge.json', '.forge/approved.json'


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
    json.dump({CONFIG: h(CONFIG), SPEC: h(SPEC)}, open(os.path.join(root, APPROVED), 'w'))


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
    json.dump({CONFIG: h(CONFIG), SPEC: h(SPEC)}, open(os.path.join(root, APPROVED), 'w'))


def helper_with_assume(root):  # an included file that no check lists
    add_helper(root, 'module Helper {\n  lemma Anything()\n    ensures 1 == 2\n  { assume false; }\n}\n')


def helper_with_bug(root):  # an included, unlisted file whose proof fails
    add_helper(root, 'module Helper {\n  lemma Wrong()\n    ensures 1 == 2\n  { }\n}\n')


def helper_ok(root):
    add_helper(root, 'module Helper {\n  lemma Fine()\n    ensures 1 + 1 == 2\n  { }\n}\n')


def edit_emitted(root):  # hand edit of emitted code
    path = os.path.join(root, 'verified/wallet-cap/py/wallet_cap-py/Wallet.py')
    text = open(path).read()
    assert '<=' in text
    open(path, 'w').write(text.replace('<=', '<', 1))


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
]

# `build --check` cases: name, change, expected build verdict, expected exit code
BUILD_CASES = [
    ('emitted code equals a fresh build', no_change, 'BUILT', 0),
    ('emitted code edited by hand', edit_emitted, 'STALE_BUILD', 1),
    ('fuzz harness expects a wrong result', wrong_expect, 'FUZZ_FAILED', 1),
    ('app no longer imports the built code', unwire, 'NOT_WIRED', 1),
]


def main():
    failed = 0
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
    print('%d cases, %d failed' % (len(CASES) + len(BUILD_CASES), failed))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
