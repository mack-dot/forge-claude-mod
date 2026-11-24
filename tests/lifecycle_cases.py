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

    # ---- state files and timeouts
    ok, info = _write_json_atomic()
    out.append(('state: a reader never sees a half-written .forge/last-diff.json (atomic write)', ok, info))
    ok, info = _fuzz_verify_timeout()
    out.append(('build: a hanging dafny verify of the fuzz harness times out (FUZZ_NOT_VERIFIED)', ok, info))
    return out


if __name__ == '__main__':
    failed = 0
    for name, ok, info in cases():
        failed += not ok
        print('%s  %s%s' % ('ok  ' if ok else 'FAIL', name, '' if ok else '\n      ' + json.dumps(info, default=str)[:400]))
    sys.exit(1 if failed else 0)
