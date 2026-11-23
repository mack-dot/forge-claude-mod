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


def bypass_cases():
    """`bypass` and the UNAPPROVED_BYPASS gate of `check`: code outside the rule that reads the rule's watched fields.
    Each case plants gift_card.py, whose can_send_gift decides "does the spend fit under the cap?" itself."""
    cases = []

    def check(root):
        r = subprocess.run([sys.executable, CLI, 'check', '--root', root, '--json', '--no-write'],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        try:
            out = json.loads(r.stdout)
        except ValueError:
            out = {'verdicts': {'?': '<no result: %s>' % (r.stderr or r.stdout)[-200:]}, 'detail': {}}
        return r.returncode, list(out['verdicts'].values()), out

    def run(root, *args):
        r = subprocess.run([sys.executable, CLI, 'bypass', '--root', root] + list(args),
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        try:
            return r.returncode, json.loads(r.stdout) if '--json' in args else r.stdout
        except ValueError:
            return r.returncode, {'error': (r.stderr or r.stdout)[-300:]}

    def hits(out):
        return [h for r in out.get('checks', {}).values() for h in r['hits']]

    def copy(tmp, name='p', plant=True):
        root = os.path.join(tmp, name)
        shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns('cache', 'regressions', '__pycache__'))
        if plant:
            open(os.path.join(root, 'gift_card.py'), 'w').write(
                '"""Gift sends, added later by another team, outside the proved rule."""\n\n\n'
                'def can_send_gift(cap, spent, amount):\n'
                '    if spent + amount <= cap:  # decides the cap itself: a negative gift passes\n'
                '        return True\n    return False\n')
        return root

    with tempfile.TemporaryDirectory() as tmp:
        root = copy(tmp)
        code, out = run(root, '--json')
        found = hits(out)
        cases.append(('bypass: a second function outside the rule that checks the cap itself is BYPASS_FOUND',
                      code == 1 and out.get('verdict') == 'BYPASS_FOUND'
                      and [(h['file'], h['function']) for h in found] == [('gift_card.py', 'can_send_gift')]))
        # The term really occurs in the rule's own files (so their exclusion is tested), and none is reported.
        term = re.compile(json.load(open(os.path.join(root, CONFIG)))['checks'][0]['watchedFields'][0][1:-1])
        own = [IMPL, 'fuzz/WalletFuzz.dfy', 'INVARIANTS.md', 'verified/wallet-cap/py/wallet_cap-py/Wallet.py']
        occurs = [f for f in own if term.search(open(os.path.join(root, f), encoding='utf-8').read())]
        reported = {h['file'] for h in found} | {e['file'] for e in json.load(open(os.path.join(root, CONFIG)))['checks'][0]['allowedBypasses']}
        cases.append(('bypass: the rule\'s own files (impl, fuzz harness, emitted code, INVARIANTS.md) never appear',
                      len(occurs) >= 3 and not set(own) & reported
                      and not any(f.startswith(('spec/', 'impl/', 'fuzz/', 'diff/', 'verified/', '.forge/')) for f in reported)))
        # the hard gate: the proof passes, but an unapproved reader exists
        cexit, verdicts, chk = check(root)
        outside = (chk.get('outside') or {}).get('wallet-cap', [])
        cases.append(('gate: a new unapproved reader gives UNAPPROVED_BYPASS (exit 1), listed under "outside"',
                      cexit == 1 and verdicts == ['UNAPPROVED_BYPASS'] and 'gift_card.py:5' in chk['detail'].get('wallet-cap', '')
                      and [(h['file'], h['function'], h['line']) for h in outside] == [('gift_card.py', 'can_send_gift', found[0]['line'] if found else -1)]
                      and (set(outside[0]) == {'file', 'line', 'function', 'term', 'text'} if outside else False)))
        # the person says "it is fine": recorded with the line's hash; forge.json changed, so approval is needed again
        line = found[0]['line'] if found else 0
        r = subprocess.run([sys.executable, CLI, 'bypass', '--root', root, '--rule', 'wallet-cap', '--record',
                            'gift_card.py:%d' % line, '--note', 'test: reviewed'], stdout=subprocess.PIPE, universal_newlines=True)
        code2, out2 = run(root, '--json')
        c2 = subprocess.run([sys.executable, CLI, 'check', '--root', root, '--json', '--no-write'],
                            stdout=subprocess.PIPE, universal_newlines=True)
        cases.append(('bypass: after the person allows it (recorded by line hash), BYPASS_CLEAR; forge.json needs approval again',
                      r.returncode == 0 and code2 == 0 and out2.get('verdict') == 'BYPASS_CLEAR'
                      and list(json.loads(c2.stdout)['verdicts'].values()) == ['CONFIG_CHANGED']))
        reapprove(root)
        cexit, verdicts, _ = check(root)
        cases.append(('gate: after it is in allowedBypasses and re-approved, VERIFIED (exit 0)', cexit == 0 and verdicts == ['VERIFIED']))
        edit(root, 'gift_card.py', 'if spent + amount <= cap:', 'if spent + amount <= cap and amount != 0:')
        code3, out3 = run(root, '--json')
        cases.append(('bypass: editing the allowed line makes it BYPASS_FOUND again (outdated entry shown)',
                      code3 == 1 and [h['file'] for h in hits(out3)] == ['gift_card.py']
                      and len(out3['checks']['wallet-cap']['outdated_reviews']) == 1))
        cexit, verdicts, _ = check(root)
        cases.append(('gate: editing the allowed line gives UNAPPROVED_BYPASS again', cexit == 1 and verdicts == ['UNAPPROVED_BYPASS']))
        rbad = subprocess.run([sys.executable, CLI, 'bypass', '--root', root, '--rule', 'wallet-cap', '--record',
                               'app.py:1', '--note', 'x'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        cases.append(('bypass: a review of a line that is not a hit is refused', rbad.returncode == 2))
        # no git on PATH (minimal containers; found in CI): the walk is used instead, nothing crashes
        empty = os.path.join(tmp, 'empty-path')
        os.makedirs(empty)
        rng = subprocess.run([sys.executable, CLI, 'bypass', '--root', root, '--json', '--no-write'], env=dict(os.environ, PATH=empty),
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        cases.append(('bypass: without git on PATH it walks the folder (still BYPASS_FOUND)',
                      rng.returncode == 1 and '"BYPASS_FOUND"' in rng.stdout))

    with tempfile.TemporaryDirectory() as tmp:  # a proof failure still wins over the gate
        root = copy(tmp)
        bug(root)
        cexit, verdicts, _ = check(root)
        cases.append(('gate: a failing proof with an unapproved reader is still FAILED', cexit == 1 and verdicts == ['FAILED']))
    with tempfile.TemporaryDirectory() as tmp:  # negative control: no watchedFields, the same reader: behaviour unchanged
        root = copy(tmp)
        cfg = json.load(open(os.path.join(root, CONFIG)))
        cfg['checks'][0].pop('watchedFields')
        json.dump(cfg, open(os.path.join(root, CONFIG), 'w'))
        reapprove(root)
        cexit, verdicts, chk = check(root)
        cases.append(('gate: negative control, no watchedFields: VERIFIED (exit 0), nothing outside',
                      cexit == 0 and verdicts == ['VERIFIED'] and chk.get('outside') == {}))
    with tempfile.TemporaryDirectory() as tmp:  # negative control: nothing outside the rule uses the field
        root = copy(tmp, plant=False)
        code, out = run(root, '--json')
        cases.append(('bypass: negative control, no other use: BYPASS_CLEAR', code == 0 and out.get('verdict') == 'BYPASS_CLEAR'
                      and not hits(out) and out['checks']['wallet-cap']['reviewed'] == 6))
        cfg = json.load(open(os.path.join(root, CONFIG)))
        cfg['checks'][0].pop('watchedFields')
        json.dump(cfg, open(os.path.join(root, CONFIG), 'w'))
        code, out = run(root, '--json')
        cases.append(('bypass: no watchedFields: NO_SCOPE_TERMS (nothing searched, not a pass)',
                      code == 0 and out.get('verdict') == 'NO_SCOPE_TERMS'))
        cfg['checks'][0]['watchedFields'] = ['/spent(/']
        json.dump(cfg, open(os.path.join(root, CONFIG), 'w'))
        code, _ = run(root, '--json')
        cases.append(('bypass: an invalid regular expression is a configuration error (exit 2)', code == 2))

    # git file list: untracked files count, ignored files and vendored folders do not
    with tempfile.TemporaryDirectory() as tmp:
        root = copy(tmp, plant=False)
        open(os.path.join(root, '.gitignore'), 'w').write('ignored/\n')
        git(root, 'init', '-q')
        git(root, 'add', '.')
        git(root, 'commit', '-q', '-m', 'base')
        for rel in ('new_spender.py', 'ignored/x.py', 'node_modules/lib/y.js'):
            os.makedirs(os.path.dirname(os.path.join(root, rel)) or root, exist_ok=True)
            open(os.path.join(root, rel), 'w').write('def ok(cap, spent, amount):\n    return spent + amount <= cap\n')
        code, out = run(root, '--json')
        cases.append(('bypass: git list: an untracked new file is found; ignored and node_modules files are not',
                      code == 1 and [h['file'] for h in hits(out)] == ['new_spender.py']))

    # chronicle-shaped: a decision says body.for counts only on Companion's rows; a second gate function trusts it
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, 'c')
        for rel, text in {
            'spec/GateSpec.dfy': 'abstract module GateSpec { }\n', 'impl/Gate.dfy': 'module Gate { }\n',
            'src/gate.ts': ('import { mayReadProved } from "./gate-mayread.ts"\n\n'
                            'export function mayRead(actor: string, row: Row): boolean {\n'
                            '  return mayReadProved(actor, row)\n}\n\n'
                            'export const mayRetract = (actorId: string, target: Row): boolean =>\n'
                            '  target.authorId === actorId || target.body.for === actorId\n\n'
                            'class Store {\n  forget(input: Input) {\n    for (const e of this.rows) {\n'
                            '      if (e.authorId !== input.parentId && e.body.for !== input.parentId) continue\n'
                            '    }\n  }\n}\n'),
            'src/Svc.java': ('class Svc {\n    public static boolean retract(Row target, String by)\n    {\n'
                             '        return target.body.for.equals(by);\n    }\n}\n'),
            'src/svc.go': 'package svc\n\nfunc (s *S) Retract(t Row, by string) bool {\n\treturn t.body.for == by\n}\n',
            'src/svc.rs': 'pub fn retract(t: &Row, by: &str) -> bool {\n    t.body.for == by\n}\n',
            'docs/decisions.md': 'D9: body.for counts only on Companion rows.\n',
        }.items():
            os.makedirs(os.path.dirname(os.path.join(root, rel)), exist_ok=True)
            open(os.path.join(root, rel), 'w').write(text)
        os.makedirs(os.path.join(root, '.forge'))
        json.dump({'version': 1, 'checks': [{'name': 'gate-mayread', 'spec': 'spec/GateSpec.dfy', 'files': ['impl/Gate.dfy'],
                                             'watchedFields': ['body.for']}]}, open(os.path.join(root, CONFIG), 'w'))
        code, out = run(root, '--json')
        got = sorted((h['file'], h['function']) for h in hits(out))
        cases.append(('bypass: chronicle-shaped: mayRetract and forget (TS), retract in Java/Go/Rust named; docs skipped',
                      code == 1 and got == [('src/Svc.java', 'retract'), ('src/gate.ts', 'forget'), ('src/gate.ts', 'mayRetract'),
                                            ('src/svc.go', 'Retract'), ('src/svc.rs', 'retract')]))
        before = open(os.path.join(root, CONFIG)).read()
        rc = subprocess.run([sys.executable, CLI, 'bypass', '--root', root, '--count', '--json', '--rule', 'gate-mayread',
                             '--set-terms', '.body.for', 'not_in_this_project'], stdout=subprocess.PIPE, universal_newlines=True)
        try:
            pv = json.loads(rc.stdout).get('gate-mayread', {})
        except ValueError:
            pv = {}
        cases.append(('bypass: --count previews other terms (lines per term) and writes nothing',
                      rc.returncode == 0 and pv.get('per_term') == {'.body.for': 5, 'not_in_this_project': 0}
                      and open(os.path.join(root, CONFIG)).read() == before))
        txt = subprocess.run([sys.executable, CLI, 'check', '--root', root, '--no-write'], stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, universal_newlines=True).stdout
        cases.append(('bypass: check text names the places ("N places decide with body.for outside gate-mayread")',
                      '5 places decide with body.for outside gate-mayread: ' in txt and 'src/gate.ts:8 mayRetract' in txt))
    return cases


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

    # Messy inputs: the unproved wrapper that turns raw app/JSON values into Dafny values must fail closed, never throw
    # (owner feedback: a real app's wrapper threw on `householdId: undefined`, and the plain fuzz never sent one).
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, 'p')
        shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns('cache', 'regressions', '__pycache__'))
        cfg = json.load(open(os.path.join(root, CONFIG)))

        def messy_cfg(flag):
            cfg['checks'][0]['diff'] = [
                dict({'name': 'messy-js', 'lang': 'js', 'inputs': 'diff/wallet_inputs.py', 'adapter': 'diff/wallet_messy.mjs'}, **flag),
                dict({'name': 'messy-py', 'lang': 'py', 'inputs': 'diff/wallet_inputs.py', 'adapter': 'diff/wallet_messy.py'}, **flag)]
            json.dump(cfg, open(os.path.join(root, CONFIG), 'w'))

        messy_cfg({'messy': False})  # negative control first: without the messy step, nothing is found
        code_off, off = diff(root)
        messy_cfg({})  # the default: on, because the adapters name raw-value wrappers
        code_on, on = diff(root)
        code_again, again = diff(root)
        mjs, mpy = on.get('wallet-cap:messy-js', {}), on.get('wallet-cap:messy-py', {})

        def got(r, cand, kind=None):
            return [d for d in r.get('disagreements', []) if d['candidate'] == cand and (kind is None or d.get('kind') == kind)]
        t_js, t_py = got(mjs, 'wrapper-throws', 'THROWS_ON_MESSY_INPUT'), got(mpy, 'wrapper-raises', 'THROWS_ON_MESSY_INPUT')
        c_js, c_py = got(mjs, 'wrapper-coerces', 'MESSY_ANSWER'), got(mpy, 'wrapper-coerces', 'MESSY_ANSWER')
        undef = {'__undefined__': True}
        cases_messy = [
            ('messy: negative control, messy off: the planted wrappers agree on well-typed inputs',
             all(r.get('verdict') == 'DIFF_OK' and 'messy' not in r for r in off.values()) and code_off == 0),
            ('messy: JS wrapper that throws on amount undefined/null is found (THROWS_ON_MESSY_INPUT)',
             code_on == 1 and {d['messy']['as'] for d in t_js} >= {'undefined', 'null'}
             and all(d['messy']['field'] == 'amount' for d in got(mjs, 'wrapper-throws'))),
            ('messy: the throw is shrunk to that one field (cap 0, spent 0, amount undefined)',
             {'cap': 0, 'spent': 0, 'amount': undef} in [d['input'] for d in t_js]
             and {'cap': 0, 'spent': 0, 'amount': None} in [d['input'] for d in t_js + t_py]),
            ('messy: Python wrapper that raises on amount None is found, only on that field',
             'null' in {d['messy']['as'] for d in t_py} and all(d['messy']['field'] == 'amount' for d in got(mpy, 'wrapper-raises'))),
            ('messy: a wrapper that coerces "5" to 5 is found as a disagreement (JS and Python)',
             any(d['messy']['as'] == 'number-as-text' and isinstance(d['input']['amount'], str) for d in c_js)
             and any(d['messy']['as'] == 'number-as-text' for d in c_py)),
            ('messy: negative control, the fail-closed wrapper has no messy disagreement',
             mjs.get('per_candidate', {}).get('wrapper-safe') == 0 and mpy.get('per_candidate', {}).get('wrapper-safe') == 0
             and mjs.get('messy', {}).get('inputs', 0) > 100 and mpy.get('messy', {}).get('inputs', 0) > 100),
            ('messy: every kind of odd value is tried (undefined only in JS) and judged',
             mjs.get('messy', {}).get('kinds') == list(forge_diff.MESSY_KINDS)
             and mpy.get('messy', {}).get('kinds') == [k for k in forge_diff.MESSY_KINDS if k != 'undefined']
             and mjs['messy']['pairs_tried'] >= 30 and mpy['messy']['pairs_tried'] >= 27
             and mjs['messy']['not_judged'] == 0 and mpy['messy']['not_judged'] == 0),
            ('messy: saved messy cases replay first next run (a lone surrogate too)',
             again.get('wallet-cap:messy-py', {}).get('replayed', 0) >= len(got(mpy, 'wrapper-raises')) > 0
             and again.get('wallet-cap:messy-js', {}).get('replayed', 0) >= len(got(mjs, 'wrapper-throws')) > 0
             and code_again == 1),
        ]
        for name, ok in cases_messy:
            failed += not ok
            extra += 1
            print('%s  %s' % ('ok  ' if ok else 'FAIL', name))
            if not ok:
                print('      ' + json.dumps({k: {kk: v.get(kk) for kk in ('verdict', 'per_candidate', 'messy', 'error')}
                                             for k, v in on.items()}, ensure_ascii=True)[:700])

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
    for name, ok in bypass_cases():
        failed += not ok
        extra += 1
        print('%s  %s' % ('ok  ' if ok else 'FAIL', name))
    print('%d cases, %d failed' % (len(CASES) + len(BUILD_CASES) + extra, failed))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
