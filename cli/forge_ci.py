#!/usr/bin/env python3
"""Forge CI driver: what the GitHub Action runs. It calls forge_check.py for each gate, prints the normal reports,
writes a job summary and a JSON report, and exits 1 if any gate fails.

  forge_ci.py plan  --root DIR            which toolchains the checks need (written to $GITHUB_OUTPUT)
  forge_ci.py base                        the git ref to compare with, from the event (printed)
  forge_ci.py run   --root DIR [options]  every gate; summary to $GITHUB_STEP_SUMMARY

Test code, not proved. Every verdict comes from forge_check.py; this file only chooses what to run and reports it.
"""
import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CHECKER = os.path.join(HERE, 'forge_check.py')
sys.path.insert(0, HERE)
import forge_check as fc  # noqa: E402  (only its report functions and config reader)

JS_EXT = ('.ts', '.mts', '.cts', '.js', '.mjs', '.cjs', '.tsx', '.jsx')
NODE_TOOLS = ('node', 'npm', 'npx', 'pnpm', 'yarn', 'tsx', 'bun')
ZERO_SHA = '0' * 40


def load(root):
    return fc.load_config(root)


def plan(root):
    """The toolchains the checks use: build targets, diff languages, app files and serviceTest commands."""
    cfg = load(root)
    langs, files, service = set(), [], []
    has_build = has_diff = False
    for c in cfg['checks']:
        b = c.get('build') or {}
        if b:
            has_build = True
            langs.update(b.get('targets') or [])
            st = b.get('serviceTest') or c.get('serviceTest')
            if st and st.get('command'):
                service.append(st['command'][0])
        for d in fc.diff_entries(c):
            has_diff = True
            langs.add(d.get('lang'))
            if d.get('lang') == 'cmd' and d.get('run'):
                service.append(d['run'][0])
        files += fc.app_files(root, c)
    exts = {os.path.splitext(f)[1] for f in files}
    out = {
        'node': 'js' in langs or bool(exts & set(JS_EXT)) or any(os.path.basename(s) in NODE_TOOLS for s in service),
        'go': 'go' in langs or '.go' in exts,
        'java': 'java' in langs,
        'rust': 'rs' in langs,
        'dotnet': 'cs' in langs,
        'build': has_build,
        'diff': has_diff,
        'invariants': True,
    }
    return out


def event_base():
    """(git ref to fetch or None, why). pull_request / pull_request_target / merge_group: the base branch or sha;
    push: the commit before the push; anything else: no base (said so in the summary, not hidden)."""
    event = os.environ.get('GITHUB_EVENT_NAME', '')
    payload = {}
    path = os.environ.get('GITHUB_EVENT_PATH')
    if path and os.path.isfile(path):
        try:
            with open(path, encoding='utf-8') as f:
                payload = json.load(f)
        except ValueError:
            payload = {}
    if event in ('pull_request', 'pull_request_target'):
        ref = os.environ.get('GITHUB_BASE_REF') or (payload.get('pull_request') or {}).get('base', {}).get('ref')
        if ref:
            return ref, '%s event: the base branch %s' % (event, ref)
    if event == 'merge_group':
        sha = (payload.get('merge_group') or {}).get('base_sha')
        if sha:
            return sha, 'merge_group event: the base commit %s' % sha[:12]
    if event == 'push':
        before = payload.get('before')
        if before and before != ZERO_SHA:
            return before, 'push event: the commit before the push, %s' % before[:12]
        return None, 'push event that creates a branch (no commit before it)'
    return None, '%s event has no base' % (event or 'no')


def git(root, *args):
    return subprocess.run(['git', '-C', root] + list(args), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          universal_newlines=True)


def resolve_base(root, given):
    """(ref for --base or None, text for the summary). `given`: '' (from the event), 'none', or a git ref."""
    if given == 'none':
        return None, 'off (base: none)'
    if given:
        if git(root, 'rev-parse', '--verify', '--quiet', given + '^{commit}').returncode != 0:
            ref = given[len('origin/'):] if given.startswith('origin/') else given
            r = git(root, 'fetch', '--no-tags', '--depth=1', 'origin', ref)
            if r.returncode != 0:
                raise RuntimeError('cannot fetch base %s: %s' % (given, r.stdout.strip()))
            sha = git(root, 'rev-parse', 'FETCH_HEAD').stdout.strip()
            return sha, 'compared with %s = %s (input)' % (given, sha[:12])
        return given, 'compared with %s (input)' % given
    ref, why = event_base()
    if not ref:
        return None, 'NOT checked: %s. Set the input base to a git ref to compare' % why
    r = git(root, 'fetch', '--no-tags', '--depth=1', 'origin', ref)
    if r.returncode != 0:
        raise RuntimeError('cannot fetch base %s (%s): %s' % (ref, why, r.stdout.strip()))
    sha = git(root, 'rev-parse', 'FETCH_HEAD').stdout.strip()
    return sha, 'compared with %s (%s)' % (sha[:12], why)


def call(args, root):
    """Run forge_check.py with --json. Returns (exit code, parsed JSON or None, raw text)."""
    r = subprocess.run([sys.executable, CHECKER] + args + ['--root', root, '--json'], stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, universal_newlines=True, errors='backslashreplace')
    try:
        data = json.loads(r.stdout)
    except ValueError:
        start = r.stdout.find('\n{')  # tool output (npm, dafny) before the JSON
        try:
            data = json.loads(r.stdout[start + 1:]) if start >= 0 else None
        except ValueError:
            data = None
    return r.returncode, data, r.stdout


def fmt(n):
    return format(n, ',')


def status_line(name, verdict, cfg_check, outside, diff_rows, build, inv_ok):
    """One scope-honest line per rule, the same parts as the Claude Code status line. ✓ only if every part passed."""
    good = verdict == 'VERIFIED'
    parts = ['model PROVED' if good else 'model PROVED, %s' % verdict if verdict == 'UNAPPROVED_BYPASS'
             else '%s (model NOT shown proved)' % verdict]
    if diff_rows:
        bad = sum(len(r.get('disagreements') or []) + len((r.get('decisions') or {}).get('failing') or [])
                  for r in diff_rows)
        broken = [r['verdict'] for r in diff_rows if r['verdict'] not in ('DIFF_OK', 'DISAGREE')]
        n = sum(r.get('inputs') or 0 for r in diff_rows)
        good = good and not bad and not broken
        parts.append('fuzz %s' % broken[0] if broken else
                     'fuzz ✗ %d disagreement(s) in %s cases' % (bad, fmt(n)) if bad else
                     'fuzzed %s cases, wrapper NOT proved' % fmt(n))
    else:
        parts.append('app code NOT fuzzed')
    if cfg_check.get('watchedFields'):
        good = good and not outside
        parts.append('%d bypass%s (%d allowed by a person)' % (
            len(outside), '' if len(outside) == 1 else 'es', len(cfg_check.get('allowedBypasses') or [])))
    else:
        parts.append('bypasses NOT searched (no watchedFields)')
    if build is not None:
        good = good and build == 'BUILT'
        parts.append('build %s' % build)
    if inv_ok is not None:
        good = good and inv_ok
        parts.append('INVARIANTS.md %s' % ('current' if inv_ok else 'STALE'))
    return '%s %s: %s' % ('✓' if good else '✗', name, ' · '.join(parts))


def run(a):
    root = os.path.abspath(a.root)
    cfg = load(root)
    p = plan(root)
    report = {'root': a.root, 'gates': {}, 'verdicts': {}, 'ok': True}
    lines = ['## Forge CI', '']
    rows = []  # (gate, result, what it covers)

    def gate(key, ok, text, covers):
        report['gates'][key] = {'ok': ok, 'text': text}
        if ok is False:
            report['ok'] = False
        rows.append((key, ('✓ ' if ok else '✗ ' if ok is False else '– ') + text, covers))

    # 1. Base for APPROVAL_CHANGED.
    try:
        base, base_text = resolve_base(root, a.base)
    except RuntimeError as e:
        base, base_text = None, 'ERROR: %s' % e
        gate('base', False, base_text, 'APPROVAL_CHANGED')
    else:
        gate('base', None if base is None else True, base_text, 'APPROVAL_CHANGED against the base')

    # 2. check: proofs, approvals, binding, unsound constructs (included files too), bypass gate.
    print('::group::forge check' + (' --base ' + base if base else ''))
    code, res, raw = call(['check', '--no-write'] + (['--base', base] if base else []), root)
    if res and 'verdicts' in res:
        print(fc.report(res) + ''.join('\n' + l for l in fc.outside_lines(res.get('outside') or {})))
    else:
        print(raw)
    print('::endgroup::')
    verdicts = (res or {}).get('verdicts') or {}
    report['verdicts']['check'] = verdicts
    report['check'] = res
    ok = code == 0 and bool(verdicts) and all(v == 'VERIFIED' for v in verdicts.values())
    gate('check', ok, (', '.join('%s %s' % kv for kv in verdicts.items()) if verdicts else
                       'ERROR: %s' % ((res or {}).get('error') or raw.strip()[-300:])),
         'Dafny proof; NOT_APPROVED, SPEC_CHANGED, CONFIG_CHANGED, APPROVAL_CHANGED, BINDING_BROKEN, '
         'UNSOUND_CONSTRUCT (included files too), EXAMPLES_FAILED, UNAPPROVED_BYPASS')
    outside = (res or {}).get('outside') or {}
    n_bypass = sum(len(v) for v in outside.values())
    allowed = sum(len(c.get('allowedBypasses') or []) for c in cfg['checks'])
    watched = [c['name'] for c in cfg['checks'] if c.get('watchedFields')]
    report['bypasses'] = {'unreviewed': n_bypass, 'allowed': allowed, 'watched_checks': watched}
    gate('bypass', (n_bypass == 0) if watched else None,
         ('%d unreviewed, %d allowed by a person' % (n_bypass, allowed)) if watched else
         'NOT searched: no check has watchedFields', 'UNAPPROVED_BYPASS (a text search, not data flow)')

    # 3. build --check.
    build_v = {}
    if a.build == 'false':
        gate('build', None, 'off (build: false)', 'STALE_BUILD, APP_SYNTAX_ERROR, NOT_CALLED')
    elif not p['build']:
        gate('build', None, 'no check has a "build" entry', 'STALE_BUILD, APP_SYNTAX_ERROR, NOT_CALLED')
    else:
        print('::group::forge build --check')
        code, res_b, raw = call(['build', '--check', '--no-write'], root)
        print(fc.build_report(res_b) if res_b and 'verdicts' in res_b else raw)
        print('::endgroup::')
        build_v = {n: b['verdict'] for n, b in ((res_b or {}).get('build') or {}).items()}
        report['verdicts']['build'] = build_v
        ok = code == 0 and bool(build_v)
        gate('build', ok, ', '.join('%s %s' % kv for kv in build_v.items()) or
             'not run: %s' % ('check is not VERIFIED' if res_b and 'verdicts' in res_b else raw.strip()[-300:]),
             'compiled code fuzzed per target, committed emitted code equals a fresh build (STALE_BUILD), '
             'APP_SYNTAX_ERROR, serviceTest (NOT_CALLED)')

    # 4. INVARIANTS.md.
    inv_ok = None
    if a.invariants == 'false':
        gate('invariants', None, 'off (invariants: false)', 'INVARIANTS_STALE')
    else:
        print('::group::forge invariants --check')
        r = subprocess.run([sys.executable, CHECKER, 'invariants', '--check', '--root', root],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
        print(r.stdout.strip())
        print('::endgroup::')
        inv_ok = r.returncode == 0
        report['verdicts']['invariants'] = r.stdout.strip().split(':')[0] if r.stdout.strip() else 'ERROR'
        gate('invariants', inv_ok, r.stdout.strip().split('\n')[-1][:300], 'INVARIANTS_STALE')

    # 5. Differential fuzz sweep.
    diff_rows = {}
    seed = None
    if a.diff == 'false':
        gate('diff', None, 'off (diff: false)', 'DISAGREE, THROWS_ON_MESSY_INPUT')
    elif not p['diff']:
        gate('diff', None, 'no check has a "diff" entry: app code NOT fuzzed', 'DISAGREE, THROWS_ON_MESSY_INPUT')
    else:
        seed = int(a.seed) if a.seed else int(os.environ.get('GITHUB_RUN_ID') or 0) % (2 ** 32) or 1
        if a.tier:  # forge.json "fuzz.tiers" (cli/forge_steer.py) instead of diff-inputs / diff-budget-seconds
            import forge_steer
            try:
                t = forge_steer.tier(cfg, a.tier)
            except forge_steer.TierError as e:
                raise fc.ConfigError(str(e))
            a.inputs, a.budget_s = t['inputs'], max(1, t['budget_ms'] // 1000)
            args = ['diff', '--no-write', '--tier', a.tier, '--seed', str(seed)]
        else:
            args = ['diff', '--no-write', '--inputs', str(a.inputs), '--budget-ms', str(int(a.budget_s) * 1000),
                    '--seed', str(seed)]
        print('::group::forge diff (%s%s inputs, budget %s s per entry, seed %d)' % (
            'tier %s: ' % a.tier if a.tier else '', fmt(a.inputs), a.budget_s, seed))
        code, res_d, raw = call(args, root)
        print(fc.diff_report(res_d) if res_d and 'checks' in res_d else raw)
        print('::endgroup::')
        diff_rows = (res_d or {}).get('checks') or {}
        report['verdicts']['diff'] = {k: r['verdict'] for k, r in diff_rows.items()}
        report['diff'] = {}
        for k, r in diff_rows.items():
            d = {x: r.get(x) for x in ('verdict', 'inputs', 'out_of_domain', 'replayed', 'ms', 'seed',
                                       'per_candidate', 'detail', 'error')}
            d['disagreements'] = len(r.get('disagreements') or [])
            d['failing_decisions'] = len((r.get('decisions') or {}).get('failing') or [])
            report['diff'][k] = d
        short = [k for k, r in diff_rows.items() if r['verdict'] == 'DIFF_OK' and (r.get('inputs') or 0) < a.inputs]
        ok = code == 0 and bool(diff_rows)
        text = '; '.join('%s %s, %s inputs, %s ms' % (k, r['verdict'], fmt(r.get('inputs') or 0), fmt(r.get('ms') or 0))
                         for k, r in diff_rows.items()) or 'ERROR: %s' % raw.strip()[-300:]
        if short:
            text += ' (time budget reached before %s inputs: %s)' % (fmt(a.inputs), ', '.join(short))
        gate('diff', ok, 'seed %d: %s' % (seed, text), 'DISAGREE, THROWS_ON_MESSY_INPUT (a FINITE TEST)')

    # Summary.
    lines += ['**%s**' % ('PASS' if report['ok'] else 'FAIL'), '']
    for c in cfg['checks']:
        name = c['name']
        rows_c = [r for k, r in diff_rows.items() if k == name or k.startswith(name + ':')]
        lines.append('- ' + status_line(name, verdicts.get(name, 'ERROR'), c, outside.get(name) or [], rows_c,
                                        build_v.get(name), inv_ok))
    lines += ['', '| Gate | Result | Covers |', '| --- | --- | --- |']
    lines += ['| %s | %s | %s |' % (k, t.replace('|', '\\|').replace('\n', ' '), cov) for k, t, cov in rows]
    rel = os.path.relpath(root)
    rel = a.root if rel.startswith('..') else rel
    fcp = '"$FORGE"/cli/forge_check.py'
    cmd = ['# FORGE = a clone of mack-dot/forge-claude-mod at the ref this workflow uses']
    if base:
        cmd.append('git fetch --no-tags --depth=1 origin %s' % base)
    cmd.append('python3 %s check --root %s%s' % (fcp, rel, ' --base ' + base if base else ''))
    if 'build' in report['verdicts']:
        cmd.append('python3 %s build --check --no-write --root %s' % (fcp, rel))
    if inv_ok is not None:
        cmd.append('python3 %s invariants --check --root %s' % (fcp, rel))
    if seed is not None and a.tier:
        cmd.append('python3 %s diff --no-write --root %s --tier %s --seed %d' % (fcp, rel, a.tier, seed))
    elif seed is not None:
        cmd.append('python3 %s diff --no-write --root %s --inputs %d --budget-ms %d --seed %d'
                   % (fcp, rel, a.inputs, int(a.budget_s) * 1000, seed))
    lines += ['', 'Reproduce locally, in this repository:', '', '```sh', '\n'.join(cmd), '```', '',
              'Labels: the Dafny model is PROVED against the approved spec. The compiled code, the wrapper and '
              'other app code are NOT proved: the build fuzz and the diff sweep are FINITE TESTS, and the bypass '
              'gate is a text search.']
    report['reproduce'] = cmd
    text = '\n'.join(lines) + '\n'
    print(text)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as f:
            f.write(text)
    out_path = a.report or os.path.join(os.environ.get('RUNNER_TEMP') or root, 'forge-ci-report.json')
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as f:
            f.write('result=%s\nreport=%s\n' % ('pass' if report['ok'] else 'fail', out_path))
    return 0 if report['ok'] else 1


def main(argv=None):
    ap = argparse.ArgumentParser(prog='forge_ci.py')
    ap.add_argument('command', choices=['plan', 'base', 'run'])
    ap.add_argument('--root', default='.')
    ap.add_argument('--base', default='', help="'' = from the event; 'none' = off; else a git ref")
    ap.add_argument('--build', default='auto', choices=['auto', 'true', 'false'])
    ap.add_argument('--invariants', default='true', choices=['true', 'false'])
    ap.add_argument('--diff', default='auto', choices=['auto', 'true', 'false'])
    ap.add_argument('--inputs', type=int, default=50000)
    ap.add_argument('--budget-s', type=int, default=600)
    ap.add_argument('--seed', default='')
    ap.add_argument('--tier', default='', choices=['', 'ci', 'nightly'],
                    help="diff sweep from forge.json fuzz.tiers (ci, nightly) instead of --inputs/--budget-s")
    ap.add_argument('--report', default='')
    a = ap.parse_args(argv)
    if a.command == 'base':
        ref, why = event_base()
        print(json.dumps({'ref': ref, 'why': why}))
        return 0
    if a.command == 'plan':
        p = plan(os.path.abspath(a.root))
        print(json.dumps(p))
        if os.environ.get('GITHUB_OUTPUT'):
            with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as f:
                f.write(''.join('%s=%s\n' % (k, 'true' if v else 'false') for k, v in p.items()))
        return 0
    try:
        return run(a)
    except fc.ConfigError as e:
        print('forge: ' + str(e), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
