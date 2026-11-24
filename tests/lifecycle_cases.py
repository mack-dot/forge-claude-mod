"""Tests of the checker's fail-open paths and of the evaluator process lifecycle (review of 0.x, after two bugs in
the airlock's MCP client that one-call-at-a-time tests against well-behaved mocks did not show).

Each case changes a copy of examples/thread-visibility (Python only, so it is fast) or speaks to an evaluator through
forge_diff.run_entry, and requires a failure where a failure is due, or no false failure where code under test only
logs. Run alone: python3 tests/lifecycle_cases.py (needs Dafny for the check case: PATH or FORGE_DAFNY; node for the
JavaScript cases)."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CLI_DIR = os.path.join(HERE, '..', 'cli')
CLI = os.path.join(CLI_DIR, 'forge_check.py')
EXAMPLE = os.path.join(HERE, '..', 'examples', 'thread-visibility')
WALLET = os.path.join(HERE, '..', 'examples', 'wallet-cap')
# (diff key, toolchain, file, old text, new text): the shipped function prints a log line on every call
COMPILED_PRINTS = [
    ('wallet-cap:go', 'go', 'legacygo/legacy.go', 'package legacygo\n\n// Spend returns whether the spend is accepted and the new total spent.\nfunc Spend(cap, spent, amount int64) (bool, int64) {\n',
     'package legacygo\n\nimport "fmt"\n\nfunc Spend(cap, spent, amount int64) (bool, int64) {\n\tfmt.Println("spend called")\n'),
    ('wallet-cap:java', 'javac', 'legacyjava/LegacyWallet.java', '    public static long[] spend(long cap, long spent, long amount) {\n',
     '    public static long[] spend(long cap, long spent, long amount) {\n        System.out.println("spend called");\n'),
    ('wallet-cap:rs', 'cargo', 'legacy_wallet.rs', 'pub fn spend(cap: u64, spent: u64, amount: i64) -> (bool, u64) {\n',
     'pub fn spend(cap: u64, spent: u64, amount: i64) -> (bool, u64) {\n    println!("spend called");\n'),
]


def _copy(tmp):
    root = os.path.join(tmp, 'p')
    shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns('cache', 'regressions', '__pycache__'))
    return root


def _edit(root, rel, old, new):
    path = os.path.join(root, rel)
    text = open(path, encoding='utf-8').read()
    assert old in text, (rel, old)
    open(path, 'w', encoding='utf-8').write(text.replace(old, new, 1))


def _run(root, *args):
    r = subprocess.run([sys.executable, CLI] + list(args) + ['--root', root, '--json'], stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, universal_newlines=True)
    try:
        return r.returncode, json.loads(r.stdout)
    except ValueError:
        return r.returncode, {'error': (r.stderr or r.stdout)[-400:]}


def _diff(root, *extra):
    code, out = _run(root, 'diff', '--seed', '7', '--no-write', *extra)
    rows = list((out.get('checks') or {}).values())
    return code, rows[0] if rows else out


def _check_axiom(attr):
    with tempfile.TemporaryDirectory() as tmp:
        root = _copy(tmp)
        # every item visible (takedowns too), "proved" from an axiom that says false
        _edit(root, 'impl/Thread.dfy', '  method ShownTo(v: Viewer, items: seq<Item>) returns (out: seq<bool>) {\n',
              '  %s\n  lemma Anything()\n    ensures false\n\n'
              '  method ShownTo(v: Viewer, items: seq<Item>) returns (out: seq<bool>) {\n'
              '    Anything();\n    out := seq(|items|, _ => true);\n    return;\n' % attr)
        code, out = _run(root, 'check', '--no-write')
        return code == 1 and list((out.get('verdicts') or {}).values()) == ['UNSOUND_CONSTRUCT'], out


def _entry(tmp, lang, adapter_text, inputs_text, n=300):
    """forge_diff.run_entry on a self-contained adapter (no Dafny output needed)."""
    sys.path.insert(0, CLI_DIR)
    import forge_diff
    ext = {'py': '.py', 'js': '.mjs'}[lang]
    adapter = os.path.join(tmp, 'adapter' + ext)
    inputs = os.path.join(tmp, 'inputs_%s.py' % lang)
    open(adapter, 'w').write(adapter_text)
    open(inputs, 'w').write(inputs_text)
    cmd = ([sys.executable, os.path.join(CLI_DIR, 'eval', 'py_eval.py'), adapter] if lang == 'py'
           else ['node', os.path.join(CLI_DIR, 'eval', 'node_eval.mjs'), adapter])
    return forge_diff.run_entry(cmd, tmp, dict(os.environ), inputs, 7, n, 60000, os.path.join(tmp, 'reg-' + lang))


INPUTS = 'def generate(rng):\n    return {"x": rng.int(-10 ** 9, 10 ** 9)}\n'
PY_LOGS = ('import sys\n'
           'def reference(i):\n    return i["x"] > 0\n'
           'def noisy(i):\n    print("noisy called with", i)\n    sys.stderr.write("warning: " + "w" * 400 + "\\n")\n'
           '    return i["x"] > 0\n'
           'print("adapter loaded")\n'
           'candidates = {"noisy": noisy}\n')
PY_STDERR = ('import sys\n'
             'def reference(i):\n    return i["x"] > 0\n'
             'def noisy(i):\n    sys.stderr.write("warning: " + "w" * 400 + "\\n")\n    return i["x"] > 0\n'
             'candidates = {"noisy": noisy}\n')
JS_LOGS = ('export const reference = i => i.x > 0\n'
           'console.log("adapter loaded")\n'
           'export const candidates = { noisy: i => { console.log("noisy called", i); '
           'console.error("warning: " + "w".repeat(400)); return i.x > 0 } }\n')


def _compiled_print(key, rel, old, new):
    """A Go/Java/Rust function under test that prints: the diff still finds the planted bugs (DISAGREE)."""
    sys.path.insert(0, CLI_DIR)
    import forge_check
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, 'p')
        shutil.copytree(WALLET, root, ignore=shutil.ignore_patterns('cache', 'regressions', '__pycache__'))
        _edit(root, rel, old, new)
        r = forge_check.run_diff(root, inputs=300, budget_ms=60000, seed=7, only={key})['checks'][key]
        return r.get('verdict') == 'DISAGREE' and not r.get('error'), [r.get('verdict'), (r.get('error') or '')[-300:]]


def _crash_logging(lang):
    with tempfile.TemporaryDirectory() as tmp:
        if lang == 'py':
            open(os.path.join(tmp, 'h.py'), 'w').write(
                'def handle_request(body):\n    print("handling", body)\n    return {"ok": True}\n')
            target = 'h.py:handle_request'
        else:
            open(os.path.join(tmp, 'h.mjs'), 'w').write(
                'export function handleRequest(body) {\n  console.log("handling", body)\n  return { ok: true }\n}\n')
            target = 'h.mjs:handleRequest'
        r = subprocess.run([sys.executable, os.path.join(CLI_DIR, 'forge_crash.py'), '--root', tmp, '--target', target,
                            '--inputs', '20', '--seed', '1', '--json'], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           universal_newlines=True)
        try:
            out = json.loads(r.stdout)
        except ValueError:
            return False, r.stdout[-300:] + r.stderr[-300:]
        text = json.dumps(out)
        return r.returncode == 0 and 'ProcessExit' not in text and 'NOT judged' not in text \
            and 'Expecting value' not in text, text[:400]


HUGE_PY = ('def reference(i):\n    return 0\n'
           'candidates = {"big": lambda i: "z" * 20000000 if i["x"] == 1 else 0}\n')


def _regression_kill(tries=8):
    """A run killed while it saves a new case must not leave a half-written case file (every later run replays the
    folder first, and a file that is not JSON stops it)."""
    import signal
    with tempfile.TemporaryDirectory() as tmp:
        adapter, inputs = os.path.join(tmp, 'big.py'), os.path.join(tmp, 'inputs_big.py')
        open(adapter, 'w').write(HUGE_PY)
        open(inputs, 'w').write('def generate(rng):\n    return {"x": 1}\n')
        bad = []
        for k in range(tries):
            reg = os.path.join(tmp, 'reg%d' % k)
            code = ('import sys; sys.path.insert(0, %r); import forge_diff\n'
                    'forge_diff.run_entry([sys.executable, %r, %r], %r, None, %r, 1, 5, 60000, %r)\n'
                    % (CLI_DIR, os.path.join(CLI_DIR, 'eval', 'py_eval.py'), adapter, tmp, inputs, reg))
            p = subprocess.Popen([sys.executable, '-c', code], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 start_new_session=True)
            end = time.time() + 60
            while time.time() < end and p.poll() is None:
                if os.path.isdir(reg) and any(f.endswith('.json') for f in os.listdir(reg)):
                    break
                time.sleep(0.0005)
            os.killpg(p.pid, signal.SIGKILL)
            p.wait()
            for f in (os.listdir(reg) if os.path.isdir(reg) else []):
                if f.endswith('.json'):
                    try:
                        json.load(open(os.path.join(reg, f), encoding='utf-8'))
                    except ValueError:
                        bad.append('%s: %d bytes, not JSON' % (f, os.path.getsize(os.path.join(reg, f))))
        return not bad, bad


def _stats_two_processes(n=150):
    """Two runs at once (the end-of-turn fuzz and a manual diff) both add their run to .forge/diff-stats.json: no
    update may be lost."""
    with tempfile.TemporaryDirectory() as tmp:
        code = ('import sys; sys.path.insert(0, %r); import forge_check\n'
                'for i in range(%d):\n'
                '    forge_check.update_diff_stats(%r, {"at": "t", "checks": {"k": {"verdict": "DIFF_OK", "inputs": 1}}})\n'
                % (CLI_DIR, n, tmp))
        ps = [subprocess.Popen([sys.executable, '-c', code], stderr=subprocess.PIPE, universal_newlines=True)
              for _ in range(2)]
        errs = [p.communicate()[1][-300:] for p in ps]
        try:
            runs = json.load(open(os.path.join(tmp, '.forge', 'diff-stats.json')))['k']['runs']
        except (OSError, ValueError, KeyError) as e:
            runs = repr(e)
        return runs == 2 * n, {'runs': runs, 'want': 2 * n, 'stderr': [e for e in errs if e]}


def _compiled_two_runs(key, rounds=4):
    """Two diff runs at once on a fresh project share .forge/cache/diff-<key>: both must give the verdict."""
    code = ('import sys, json; sys.path.insert(0, %r); import forge_check\n'
            'r = forge_check.run_diff(sys.argv[1], inputs=200, budget_ms=60000, seed=7, only={%r})["checks"][%r]\n'
            'print(json.dumps(r.get("verdict")))\n' % (CLI_DIR, key, key))
    got = []
    for _ in range(rounds):
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, 'p')
            shutil.copytree(WALLET, root, ignore=shutil.ignore_patterns('cache', 'regressions', '__pycache__'))
            ps = [subprocess.Popen([sys.executable, '-c', code, root], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   universal_newlines=True) for _ in range(2)]
            for p in ps:
                raw = p.communicate()[0].strip().split('\n')
                got.append(raw[-1] if raw[-1] == '"DISAGREE"' else ' | '.join(raw)[-300:])
    return all(g == '"DISAGREE"' for g in got), got


def _mutate_error():
    """mutate (Dafny): a mutant that does not even resolve (seq `-`) is an ERROR, not a caught bug."""
    sys.path.insert(0, CLI_DIR)
    import forge_check
    with tempfile.TemporaryDirectory() as tmp:
        root = _copy(tmp)
        old = forge_check.MUTATIONS
        forge_check.MUTATIONS = [('+ to -', r'(?<=out )\+(?= \[ok\])', '-')]  # `out + [ok]`: seq minus seq
        try:
            out = forge_check.run_mutate(root, 5)
        finally:
            forge_check.MUTATIONS = old
        m = out['mutants']
        ok = len(m) == 1 and m[0]['verdict'] == 'ERROR' and m[0]['status'] == 'error' and out['caught'] == 0 \
            and out.get('errors') == 1 and 'not scored' in forge_check.mutate_report(out)
        return ok, dict({k: out.get(k) for k in ('caught', 'survived', 'errors')}, mutants=[(x['verdict'], x['status']) for x in m])


def _fuzz_build_failed(stale):
    """build --check: a target compiler that fails on the fuzz harness is FUZZ_BUILD_FAILED (not FUZZ_FAILED, a wrong
    result), and it does not hide STALE_BUILD."""
    sys.path.insert(0, CLI_DIR)
    import forge_check
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, 'p')
        shutil.copytree(WALLET, root, ignore=shutil.ignore_patterns('cache', 'regressions', '__pycache__'))
        fake = os.path.join(tmp, 'bin')
        os.makedirs(fake)
        open(os.path.join(fake, 'cargo'), 'w').write('#!/bin/sh\necho "cargo: build failed (planted)" >&2\nexit 101\n')
        os.chmod(os.path.join(fake, 'cargo'), 0o755)
        if stale:
            path = os.path.join(root, 'verified', 'wallet-cap', 'py', 'wallet_cap-py', 'Wallet.py')
            open(path, 'a').write('\n# edited by hand\n')
        cfg = forge_check.load_config(root)
        c = cfg['checks'][0]
        c['build']['targets'] = ['py', 'rs']
        c['build'].pop('usedBy', None)
        old = os.environ['PATH']
        os.environ['PATH'] = fake + os.pathsep + old
        try:
            res = forge_check.build_one(root, cfg, c, forge_check.find_dafny(root, cfg, download=False), True)
        finally:
            os.environ['PATH'] = old
        want = 'STALE_BUILD' if stale else 'FUZZ_BUILD_FAILED'
        rs = res['targets'].get('rs', {}).get('fuzz')
        ok = res['verdict'] == want and rs == 'BUILD_FAILED' and any(n.startswith('rs:') for n in res['notes'])
        return ok, [res['verdict'], rs, [n[:80] for n in res['notes']]]


def _write_json_atomic():
    """A reader (the status line) never sees a half-written .forge file while a run writes it."""
    sys.path.insert(0, CLI_DIR)
    import forge_check
    with tempfile.TemporaryDirectory() as tmp:
        value = {'checks': {'k%d' % i: {'verdict': 'DIFF_OK', 'pad': 'x' * 200} for i in range(4000)}}
        forge_check.write_json(tmp, forge_check.LAST_DIFF, value)
        stop, bad = [False], []

        def writer():
            while not stop[0]:
                forge_check.write_json(tmp, forge_check.LAST_DIFF, value)

        t = threading.Thread(target=writer)
        t.start()
        try:
            end = time.time() + 3
            while time.time() < end and not bad:
                try:
                    forge_check.read_json(os.path.join(tmp, forge_check.LAST_DIFF))
                except forge_check.ConfigError as e:
                    bad.append(str(e)[:120])
        finally:
            stop[0] = True
            t.join()
        return not bad, bad


def _fuzz_verify_timeout():
    """build: a `dafny verify` of the fuzz harness that hangs ends with a verdict, not a hang of the build."""
    sys.path.insert(0, CLI_DIR)
    import forge_check
    with tempfile.TemporaryDirectory() as tmp:
        root = _copy(tmp)
        fake = os.path.join(tmp, 'dafny')
        open(fake, 'w').write('#!/bin/sh\nsleep 20\n')
        os.chmod(fake, 0o755)
        cfg = forge_check.load_config(root)
        old = forge_check.TIMEOUT_S
        forge_check.TIMEOUT_S = 2
        res = {}
        try:
            t = threading.Thread(target=lambda: res.update(forge_check.build_one(root, cfg, cfg['checks'][0], fake, True)))
            t.daemon = True
            t.start()
            t.join(12)
        finally:
            forge_check.TIMEOUT_S = old
        return res.get('verdict') == 'FUZZ_NOT_VERIFIED', res.get('verdict') or 'still running after 12 s'


def cases():
    out = []
    # ---- fail-open: VERIFIED / DIFF_OK where a failure is due
    for attr in ('@Axiom', '@VerifyOnly'):
        ok, info = _check_axiom(attr)
        out.append((('fail-open: check: @Axiom (Dafny 4 attribute syntax of {:axiom}) is UNSOUND_CONSTRUCT, not VERIFIED'
                     if attr == '@Axiom' else 'check: @VerifyOnly (the Dafny 4 form of {:only}) is UNSOUND_CONSTRUCT'),
                    ok, info))

    with tempfile.TemporaryDirectory() as tmp:
        root = _copy(tmp)
        _edit(root, 'diff/thread_diff.py', '\ncandidates = {', '\ncandidatez = {')
        _edit(root, 'diff/thread_diff.py', '\nwrappers = {', '\nwrapperz = {')
        code, r = _diff(root)
        out.append(('fail-open: diff: an adapter that names no candidates is DIFF_ERROR, not DIFF_OK',
                    code == 1 and r.get('verdict') == 'DIFF_ERROR', r.get('verdict') or r))

    with tempfile.TemporaryDirectory() as tmp:
        root = _copy(tmp)
        inputs = os.path.join(root, 'diff/thread_inputs.py')
        text = open(inputs).read()
        open(inputs, 'a').write('\n\ndef in_domain(i):\n    return False\n\n\ndecisions = []\n')
        code, r = _diff(root)
        out.append(('fail-open: diff: every input outside in_domain (nothing compared) is DIFF_ERROR, not DIFF_OK',
                    code == 1 and r.get('verdict') == 'DIFF_ERROR' and 'no input was compared' in (r.get('error') or ''),
                    [r.get('verdict'), r.get('error'), r.get('inputs'), r.get('out_of_domain')]))
        open(inputs, 'w').write(text + '\n\ndef in_domain(i):\n    return False\n')
        code, r = _diff(root, '--inputs', '0')
        out.append(('fail-open: diff: a recorded decision outside in_domain is not counted as agreeing',
                    code == 1 and r.get('verdict') == 'DIFF_ERROR' and (r.get('decisions') or {}).get('agree') == 0,
                    [r.get('verdict'), r.get('decisions')]))

    with tempfile.TemporaryDirectory() as tmp:
        root = _copy(tmp)
        # the shipped code shows everything, and the emitted "proved" code is edited by hand to agree
        _edit(root, 'thread_app.py', "    by_id = {it['id']: it for it in items}\n",
              "    return [it['id'] for it in items]\n")
        open(os.path.join(root, 'verified/thread-visibility/py/thread_visibility-py/Thread.py'), 'a').write(
            '\n\ndef _everything(v, items):\n    return _dafny.Seq([True] * len(items))\n\n\n'
            'default__.ShownTo = staticmethod(_everything)\n')
        code, r = _diff(root)
        out.append(('fail-open: diff: emitted code edited by hand is BUILD_STALE (the reference is not the proved code)',
                    code == 1 and r.get('verdict') == 'BUILD_STALE', r.get('verdict')))
    with tempfile.TemporaryDirectory() as tmp:  # negative control: running the built code (__pycache__) is not stale
        root = _copy(tmp)
        code, r = _diff(root)
        code2, r2 = _diff(root)
        out.append(('diff: after a run (Python caches in verified/) the build is still fresh',
                    code == 0 and code2 == 0 and r2.get('verdict') == 'DIFF_OK', [r.get('verdict'), r2.get('verdict')]))

    # ---- evaluator lifecycle: code under test that logs is fuzzed, not reported as broken or hung
    with tempfile.TemporaryDirectory() as tmp:
        res = _entry(tmp, 'py', PY_LOGS, INPUTS, n=1500)
        out.append(('evaluator py: code that prints to stdout and stderr is fuzzed (no protocol error, no false hang)',
                    not res.get('error') and res['inputs'] == 1500 and not res['disagreements'],
                    (res.get('error') or '')[-200:]))
    with tempfile.TemporaryDirectory() as tmp:
        res = _entry(tmp, 'py', PY_STDERR, INPUTS, n=1500)
        out.append(('evaluator py: 600 KB on stderr is drained (no false "hang" after the 64 KB pipe buffer)',
                    not res.get('error') and res['inputs'] == 1500, (res.get('error') or '')[-200:]))
    if shutil.which('node'):
        with tempfile.TemporaryDirectory() as tmp:
            res = _entry(tmp, 'js', JS_LOGS, INPUTS, n=1500)
            out.append(('evaluator js: code that console.logs and console.errors is fuzzed (no protocol error, no '
                        'false hang)', not res.get('error') and res['inputs'] == 1500 and not res['disagreements'],
                        (res.get('error') or '')[-200:]))
    for lang in ('py', 'js'):
        if lang == 'js' and not shutil.which('node'):
            continue
        ok, info = _crash_logging(lang)
        out.append(('crash %s: a function that logs to stdout is judged (not a false ProcessExit / NOT judged)' % lang,
                    ok, info))

    for key, tool, rel, old, new in COMPILED_PRINTS:
        if not shutil.which(tool):
            continue
        ok, info = _compiled_print(key, rel, old, new)
        out.append(('evaluator %s: shipped code that prints to stdout is fuzzed (DISAGREE, not a protocol error)'
                    % key.split(':')[1], ok, info))

    # ---- state files and timeouts
    ok, info = _write_json_atomic()
    out.append(('state: a reader never sees a half-written .forge/last-diff.json (atomic write)', ok, info))
    ok, info = _stats_two_processes()
    out.append(('state: two diff runs at once lose no diff-stats.json update', ok, info))
    if shutil.which('go'):
        ok, info = _compiled_two_runs('wallet-cap:go')
        out.append(('state: two diff runs at once share the Go evaluator build folder (both DISAGREE, no crash)', ok, info))
    ok, info = _regression_kill()
    out.append(('state: a run killed while it saves a case leaves no half-written regression file', ok, info))
    ok, info = _mutate_error()
    out.append(('mutate: a mutant that does not resolve (ERROR) is reported separately, not counted as caught', ok, info))
    for stale in (False, True):
        ok, info = _fuzz_build_failed(stale)
        out.append(('build: a target compiler that fails on the fuzz harness is %s' % (
            'FUZZ_BUILD_FAILED (not FUZZ_FAILED)' if not stale else 'noted, and does not hide STALE_BUILD'), ok, info))
    ok, info = _fuzz_verify_timeout()
    out.append(('build: a hanging dafny verify of the fuzz harness times out (FUZZ_NOT_VERIFIED)', ok, info))
    return out


if __name__ == '__main__':
    failed = 0
    for name, ok, info in cases():
        failed += not ok
        print('%s  %s%s' % ('ok  ' if ok else 'FAIL', name, '' if ok else '\n      ' + json.dumps(info, default=str)[:400]))
    sys.exit(1 if failed else 0)
