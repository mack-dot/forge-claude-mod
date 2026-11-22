#!/usr/bin/env python3
"""forge check: the one checker used by the Claude Code mod and by CI.

Usage:
  forge_check.py check   [--root DIR] [--json] [--base GIT_REF]
  forge_check.py approve [--root DIR]            (interactive; a person's act)
  forge_check.py spec    [--root DIR]

Exit codes of `check`: 0 every check VERIFIED; 1 at least one check is not
VERIFIED; 2 usage or configuration error.

Files in the project:
  .forge/forge.json      which code is checked against which spec
  .forge/approved.json   SHA-256 of forge.json and of each spec, written on approval
  .forge/last-check.json verdicts of the last run

LIMIT: a VERIFIED verdict says that Dafny proved the listed files against the
approved spec text on this machine. It says nothing about code that no check
lists, about the compiler, or about whether the application calls the proved code.
Python 3.8+, standard library only.
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

DIR = '.forge'
CONFIG = DIR + '/forge.json'
APPROVED = DIR + '/approved.json'
LAST = DIR + '/last-check.json'

# Constructs that make Dafny accept a claim without proof. A text scan, not a parser.
UNSOUND = [r'\bassume\b', r'\{:\s*verify\s+false', r'\{:\s*axiom', r'\{:\s*extern', r'\{:\s*only']
TIMEOUT_S = 600


class ConfigError(Exception):
    pass


def sha256(path):
    with open(path, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()


def read_json(path, default=None):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except json.JSONDecodeError as e:
        raise ConfigError('%s is not valid JSON: %s' % (path, e))


def inside(root, rel):
    """Absolute path of rel; rel must stay inside root."""
    path = os.path.realpath(os.path.join(root, rel))
    if os.path.commonpath([path, os.path.realpath(root)]) != os.path.realpath(root):
        raise ConfigError('%s is outside the project' % rel)
    return path


def load_config(root):
    cfg = read_json(os.path.join(root, CONFIG))
    if cfg is None:
        raise ConfigError('no %s. Set a check up first (/forge in Claude Code).' % CONFIG)
    checks = cfg.get('checks')
    if not isinstance(checks, list) or not checks:
        raise ConfigError('%s has no checks' % CONFIG)
    names = set()
    for c in checks:
        for key in ('name', 'spec', 'files'):
            if key not in c:
                raise ConfigError('a check in %s has no "%s"' % (CONFIG, key))
        if c['name'] in names:
            raise ConfigError('two checks are named %s' % c['name'])
        names.add(c['name'])
        if not isinstance(c['files'], list) or not c['files']:
            raise ConfigError('check %s has no files' % c['name'])
        for rel in [c['spec']] + c['files']:
            inside(root, rel)
        b = c.get('binding')
        if b is not None and not (isinstance(b, dict) and 'file' in b and 'prefix' in b):
            raise ConfigError('check %s: "binding" needs "file" and "prefix"' % c['name'])
    return cfg


def find_dafny(root, cfg):
    configured = cfg.get('dafny')
    if configured:
        path = configured if os.path.isabs(configured) else os.path.join(root, configured)
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    on_path = shutil.which('dafny')
    if on_path:
        return on_path
    env = os.environ.get('FORGE_DAFNY')
    if env and os.path.isfile(env) and os.access(env, os.X_OK):
        return env
    return None


def unsound_in(path):
    with open(path, encoding='utf-8', errors='replace') as f:
        code = re.sub(r'//[^\n]*', '', f.read())
    code = re.sub(r'/\*.*?\*/', '', code, flags=re.S)
    for pattern in UNSOUND:
        if re.search(pattern, code):
            return pattern
    return None


def changed_since(root, base, paths):
    """Protected paths that differ from the git ref `base`. None if git cannot tell."""
    out = []
    for rel in paths:
        r = subprocess.run(['git', '-C', root, 'diff', '--quiet', base, '--', rel],
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        if r.returncode == 1:
            out.append(rel)
        elif r.returncode != 0:
            return None
    return out


def run_checks(root, base=None):
    cfg = load_config(root)
    approved = read_json(os.path.join(root, APPROVED), {}) or {}
    dafny = find_dafny(root, cfg)
    verdicts, detail = {}, {}

    config_ok = approved.get(CONFIG) == sha256(os.path.join(root, CONFIG))
    approval_changed = []
    if base:
        protected = [CONFIG, APPROVED] + sorted({c['spec'] for c in cfg['checks']})
        approval_changed = changed_since(root, base, protected)
        if approval_changed is None:
            raise ConfigError('cannot compare with git ref %s (fetch it first)' % base)

    for c in cfg['checks']:
        name, spec = c['name'], c['spec']

        def put(verdict, text):
            verdicts[name] = verdict
            detail[name] = text

        if approval_changed:
            put('APPROVAL_CHANGED', 'changed against %s: %s. A person with the right to approve '
                'specs must review this change.' % (base, ', '.join(approval_changed)))
            continue
        if not config_ok:
            put('CONFIG_CHANGED', '%s is not approved as it is now (a check, spec path, file list '
                'or binding changed). Read it and approve it.' % CONFIG)
            continue
        spec_path = inside(root, spec)
        if not os.path.isfile(spec_path):
            put('ERROR', 'spec file %s is missing' % spec)
            continue
        if spec not in approved:
            put('NOT_APPROVED', '%s was never approved. Read it and approve it.' % spec)
            continue
        if approved[spec] != sha256(spec_path):
            put('SPEC_CHANGED', '%s changed after it was approved. Read the new version and '
                'approve it.' % spec)
            continue
        missing = [f for f in c['files'] if not os.path.isfile(inside(root, f))]
        if missing:
            put('ERROR', 'missing file(s): ' + ', '.join(missing))
            continue
        b = c.get('binding')
        if b:
            bpath = inside(root, b['file'])
            text = ''
            if os.path.isfile(bpath):
                with open(bpath, encoding='utf-8', errors='replace') as f:
                    text = f.read()
            if not text.startswith(b['prefix']):
                put('BINDING_BROKEN', '%s no longer starts with the approved include and refines '
                    'lines, so it may not be checked against %s.' % (b['file'], spec))
                continue
        hit = None
        for f in c['files']:
            pattern = unsound_in(inside(root, f))
            if pattern:
                hit = '%s matches %s' % (f, pattern)
                break
        if hit:
            put('UNSOUND_CONSTRUCT', hit + ': it lets Dafny accept a claim without proof. Remove it.')
            continue
        if not dafny:
            put('ERROR', 'Dafny was not found: set "dafny" in %s, put dafny on PATH, or set '
                'FORGE_DAFNY.' % CONFIG)
            continue
        try:
            r = subprocess.run([dafny, 'verify'] + [inside(root, f) for f in c['files']],
                               cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               timeout=TIMEOUT_S, universal_newlines=True)
        except subprocess.TimeoutExpired:
            put('ERROR', 'dafny ran longer than %d s' % TIMEOUT_S)
            continue
        tail = '\n'.join(r.stdout.strip().split('\n')[-12:])
        # Dafny exit codes: 0 verified; 4 verification failure; others are tool or input errors.
        verdict = 'VERIFIED' if r.returncode == 0 else 'FAILED' if r.returncode == 4 else 'ERROR'
        put(verdict, 'dafny exit %d: %s' % (r.returncode, tail))

    return {
        'at': datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds'),
        'verdicts': verdicts,
        'detail': detail,
    }


def summary(result):
    v = result['verdicts']
    ok = sum(1 for x in v.values() if x == 'VERIFIED')
    bad = [(n, x) for n, x in v.items() if x != 'VERIFIED']
    if not bad:
        return 'forge ✓ VERIFIED %d/%d' % (ok, len(v))
    return 'forge ✗ %s %s · %d/%d verified' % (bad[0][1], bad[0][0], ok, len(v))


def report(result):
    lines = [summary(result)]
    for name, verdict in result['verdicts'].items():
        lines.append('%s %s: %s' % ('✓' if verdict == 'VERIFIED' else '✗', name, verdict))
    for name, verdict in result['verdicts'].items():
        if verdict != 'VERIFIED':
            lines.append('\n--- %s ---\n%s' % (name, result['detail'][name]))
    return '\n'.join(lines)


def contract_of(path):
    keep = []
    with open(path, encoding='utf-8', errors='replace') as f:
        for line in f.read().split('\n'):
            if re.match(r'\s*(//|requires\b|ensures\b|(ghost\s+)?(predicate|function|method)\b|datatype\b)', line) \
                    or re.search(r'\b(ensures|requires)\b', line):
                keep.append(line.rstrip())
    if len(keep) > 80:
        keep = keep[:80] + ['… (%d more lines in the file)' % (len(keep) - 80)]
    return '\n'.join(keep)


def show_specs(root):
    """Text of what an approval covers, and the hashes of exactly that text."""
    cfg = load_config(root)
    approved = read_json(os.path.join(root, APPROVED), {}) or {}
    hashes = {CONFIG: sha256(os.path.join(root, CONFIG))}

    def state(key):
        if approved.get(key) == hashes[key]:
            return 'approved'
        return 'CHANGED since it was approved' if key in approved else 'not approved yet'

    parts = ['=== which code is checked against which spec: %s (%s) ===\n%s'
             % (CONFIG, state(CONFIG), json.dumps(cfg, indent=2))]
    for c in cfg['checks']:
        spec = c['spec']
        if spec in hashes:
            continue
        hashes[spec] = sha256(inside(root, spec))
        parts.append('=== %s: %s (%s, sha256 %s…) ===\n%s'
                     % (c['name'], spec, state(spec), hashes[spec][:12], contract_of(inside(root, spec))))
    return '\n\n'.join(parts), hashes


def write_json(root, rel, value):
    os.makedirs(os.path.join(root, DIR), exist_ok=True)
    with open(os.path.join(root, rel), 'w', encoding='utf-8') as f:
        f.write(json.dumps(value, indent=2, ensure_ascii=False) + '\n')


def main(argv=None):
    p = argparse.ArgumentParser(prog='forge_check.py', description='Forge proof check')
    p.add_argument('command', choices=['check', 'approve', 'spec'])
    p.add_argument('--root', default='.', help='project folder (default: current folder)')
    p.add_argument('--json', action='store_true', help='print the result as JSON')
    p.add_argument('--base', help='git ref; any change to forge.json, approved.json or a spec '
                                  'against it gives APPROVAL_CHANGED (for pull requests)')
    p.add_argument('--no-write', action='store_true', help='do not write ' + LAST)
    a = p.parse_args(argv)
    root = os.path.abspath(a.root)
    try:
        if a.command == 'spec':
            print(show_specs(root)[0])
            return 0
        if a.command == 'approve':
            if not (sys.stdin.isatty() and sys.stdout.isatty()):
                print('approve needs a person at a terminal.', file=sys.stderr)
                return 2
            text, hashes = show_specs(root)
            print(text)
            if input('\nApprove exactly this? Type yes: ').strip() != 'yes':
                print('Not approved.')
                return 1
            for key, h in hashes.items():
                if sha256(os.path.join(root, key)) != h:
                    print('%s changed while you read it. Run approve again.' % key)
                    return 1
            write_json(root, APPROVED, hashes)
            print('approved: ' + ', '.join(hashes))
            return 0
        result = run_checks(root, a.base)
        if not a.no_write:
            write_json(root, LAST, result)
        print(json.dumps(result, indent=2, ensure_ascii=False) if a.json else report(result))
        return 0 if all(v == 'VERIFIED' for v in result['verdicts'].values()) else 1
    except ConfigError as e:
        if a.json:
            print(json.dumps({'error': str(e)}))
        else:
            print('forge: ' + str(e), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
