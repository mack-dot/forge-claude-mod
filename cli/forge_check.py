#!/usr/bin/env python3
"""forge check: the one checker used by the Claude Code mod and by CI.

Usage:
  forge_check.py check   [--root DIR] [--json] [--base GIT_REF]
  forge_check.py approve [--root DIR]            (interactive; a person's act)
  forge_check.py approve [--root DIR] --hashes JSON  (approves exactly what `spec --json` showed)
  forge_check.py spec    [--root DIR] [--json]
  forge_check.py build   [--root DIR] [--json] [--check]
  forge_check.py setup   [--root DIR] [--json]     (find Dafny, or download the pinned release)
  forge_check.py mutate  [--root DIR] [--json] [--max N]
  forge_check.py diff    [--root DIR] [--json] [--inputs N] [--budget-ms MS] [--seed S]
  forge_check.py invariants [--root DIR] [--check]   (INVARIANTS.md: every approved rule in plain words)
  forge_check.py bypass  [--root DIR] [--json]       (code outside the rule that reads its "watchedFields")
  forge_check.py bypass  --rule NAME --set-terms TERM...  |  --rule NAME --record FILE:LINE --note TEXT
  forge_check.py bypass  --count [--rule NAME --set-terms TERM...]   (lines per term; with terms: a preview)

`diff` compares code under test (the shipped app function, the wrapper around the proved code) with the
compiled proved rule on fresh random inputs (see forge_diff.py for the line protocol). A disagreement is
shrunk to a small input, saved under .forge/regressions/<check>/, and replayed first on every later run. It
needs a check with "build" (the compiled proved code) and "diff": {"inputs": "<inputs .py>", "lang": "js" |
"py", "adapter": "<file>"} or {"inputs": ..., "lang": "cmd", "run": [program, args...]} for any other
language. It reads only hashes to see whether the compiled code matches the Dafny files.

`mutate` plants small bugs in the implementation files (one at a time, in a temporary copy) and runs the
proof on each. A mutant is "caught" when the check is no longer VERIFIED. A mutant that survives means the
spec does not forbid that change: either the change is harmless (an equivalent mutant) or the spec is too
weak there. Read each survivor. Nothing in the project is changed.

`build` first runs `check`. For each check with a "build" entry it then:
  1. verifies the fuzz harness with Dafny, compiles it to every listed target, runs it,
     and requires the same output from every target (catches one-backend miscompiles);
  2. emits the proved code for every target under <out>/<check name>/<target>/;
  3. requires each "usedBy" file to contain its text (a text check that the app imports it).
With --check nothing is written: the emitted code on disk must equal a fresh build.
Targets: py, js, go, java, cs, rs. Exit 0 only if every check is VERIFIED and BUILT.

Exit codes of `check`: 0 every check VERIFIED; 1 at least one check is not
VERIFIED; 2 usage or configuration error.

Files in the project:
  .forge/forge.json      which code is checked against which spec
  .forge/approved.json   SHA-256 of forge.json and of each spec, written on approval
  .forge/last-check.json verdicts of the last run

LIMIT: the emitted code is a NATIVE CHECK: Dafny's compilers are not verified. The fuzz
run is a FINITE TEST. Only the Dafny model is PROVED.
LIMIT: a VERIFIED verdict says that Dafny proved the listed files against the
approved spec text on this machine. It says nothing about code that no check
lists, about the compiler, or about whether the application calls the proved code.
Dafny: the "dafny" path in forge.json, else `dafny` on PATH, else FORGE_DAFNY, else Dafny 4.11.0 downloaded
once from its GitHub release into ~/.cache/forge-check (SHA-256 pinned below; set FORGE_NO_DOWNLOAD=1
to forbid the download).
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
import platform
import tempfile
import urllib.request
import zipfile

DIR = '.forge'
CONFIG = DIR + '/forge.json'
APPROVED = DIR + '/approved.json'
LAST = DIR + '/last-check.json'
LAST_DIFF = DIR + '/last-diff.json'
DIFF_STATS = DIR + '/diff-stats.json'
REGRESSIONS = DIR + '/regressions'

# Constructs that make Dafny accept a claim without proof. A text scan, not a parser.
UNSOUND = [r'\bassume\b', r'\{:\s*verify\s+false', r'\{:\s*axiom', r'\{:\s*extern', r'\{:\s*only']
TIMEOUT_S = 600
DAFNY_VERSION = '4.11.0'
# Release files of dafny-lang/dafny v4.11.0 and the SHA-256 that GitHub publishes for each.
DAFNY_RELEASES = {
    ('Darwin', 'arm64'): ('dafny-4.11.0-arm64-macos-13.zip',
                          'c90c75e7d5db9c6ccbb7127840dfe43f0ac938b039a7ebed146d8ead383a572f'),
    ('Darwin', 'x86_64'): ('dafny-4.11.0-x64-macos-13.zip',
                           '5fc0de946c5b2fad33f16bd22a5b06f4fd0dfa7f6d770284237e3f1f3ca9f73d'),
    ('Linux', 'x86_64'): ('dafny-4.11.0-x64-ubuntu-22.04.zip',
                          'a46a9ff7cdd720f7955854c78e95df13f4cfe6b80691b05f8654fe19e8267179'),
    ('Windows', 'AMD64'): ('dafny-4.11.0-x64-windows-2022.zip',
                           '3653f05a111ca21e234709ea7b25ce96083fd6c6f10484256ba54110dbc0654d'),
}
TARGETS = {  # target: (tools that must be on PATH, extra Dafny options)
    'py': (['python3'], []),
    'js': (['node', 'npm'], []),
    'go': (['go', 'goimports'], []),
    'java': (['javac', 'java'], []),
    'cs': (['dotnet'], []),
    'rs': (['cargo'], ['--enforce-determinism']),
}


class ConfigError(Exception):
    pass


def sha256(path):
    with open(path, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()


# Sections of a decision record that state nothing binding: editing them needs no new approval. Everything else
# (the rule in the person's words, the confirmed defaults, the choices table) is hashed.
NON_NORMATIVE = ('out of scope', 'notes', 'background', 'history')


def normative_text(text):
    keep, skip = [], False
    for line in text.replace('\r\n', '\n').split('\n'):
        m = re.match(r'#{2,6}\s+(.*)', line)
        if m:
            skip = m.group(1).strip().lower().startswith(NON_NORMATIVE)
        if not skip:
            keep.append(line.rstrip())
    return '\n'.join(keep).strip() + '\n'


def contract_hash(path):
    """The hash an approval stores. A Markdown decision record is hashed without its non-normative sections
    (Out of scope, Notes), so fixing a note does not need a new approval; every other file byte for byte."""
    if path.endswith('.md'):
        return 'n:' + hashlib.sha256(normative_text(read_text(path)).encode('utf-8')).hexdigest()
    return sha256(path)


def approved_matches(approved, rel, path):
    """Approvals written before 0.13 hold the full-file hash of a decision record: accept both."""
    h = approved.get(rel)
    return h is not None and (h == contract_hash(path) or h == sha256(path))


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
        for rel in [c['spec']] + c['files'] + contract_extras(c):
            inside(root, rel)
        b = c.get('binding')
        if b is not None and not (isinstance(b, dict) and 'file' in b and 'prefix' in b):
            raise ConfigError('check %s: "binding" needs "file" and "prefix"' % c['name'])
        bd = c.get('build')
        if bd is not None:
            if not isinstance(bd, dict) or not bd.get('targets') or not bd.get('out'):
                raise ConfigError('check %s: "build" needs "targets" and "out"' % c['name'])
            unknown = [t for t in bd['targets'] if t not in TARGETS]
            if unknown:
                raise ConfigError('check %s: unknown target(s) %s; known: %s'
                                  % (c['name'], ', '.join(unknown), ', '.join(TARGETS)))
            if not re.match(r'^[A-Za-z0-9_]+$', c['name'].replace('-', '_')):
                raise ConfigError('check %s: a built check needs a name of letters, digits, - and _' % c['name'])
            inside(root, bd['out'])
            if bd.get('fuzz'):
                inside(root, bd['fuzz'])
            for u in bd.get('usedBy', []):
                if not (isinstance(u, dict) and 'file' in u and 'contains' in u):
                    raise ConfigError('check %s: each "usedBy" needs "file" and "contains"' % c['name'])
                inside(root, u['file'])
        for df in diff_entries(c):
            ok = isinstance(df, dict) and df.get('inputs') and (
                (df.get('lang') in ('js', 'py', 'go', 'rs', 'java', 'cs') and df.get('adapter'))
                or (df.get('lang') == 'cmd' and isinstance(df.get('run'), list) and df['run']))
            if not ok:
                raise ConfigError('check %s: each "diff" entry needs "inputs" (a Python file) and either "lang": "js", '
                                  '"py", "go", "rs", "java" or "cs" with "adapter", or "lang": "cmd" with "run": [program, args...]'
                                  % c['name'])
            inside(root, df['inputs'])
            if df.get('adapter'):
                inside(root, df['adapter'])
    return cfg


def diff_entries(c):
    """A check's "diff" may be one entry or a list of entries."""
    d = c.get('diff')
    return [] if d is None else d if isinstance(d, list) else [d]


def contract_extras(c):
    """Files that are part of the approved contract besides the spec: the person's answers."""
    return [c[k] for k in ('examples', 'decisions') if c.get(k)]


def contract_files(c):
    return [c['spec']] + contract_extras(c)


def cache_dir():
    base = os.environ.get('XDG_CACHE_HOME') or os.path.join(os.path.expanduser('~'), '.cache')
    return os.path.join(base, 'forge-check')


def cached_dafny():
    exe = 'dafny.exe' if platform.system() == 'Windows' else 'dafny'
    path = os.path.join(cache_dir(), 'dafny-' + DAFNY_VERSION, 'dafny', exe)
    return path if os.path.isfile(path) and os.access(path, os.X_OK) else None


def download_dafny(log=sys.stderr):
    """Download the pinned Dafny release for this machine, check its SHA-256, unpack it into the
    cache. Returns the path of the dafny program, or raises ConfigError."""
    key = (platform.system(), platform.machine())
    if key not in DAFNY_RELEASES:
        raise ConfigError('no pinned Dafny %s download for %s %s; install Dafny yourself and put it '
                          'on PATH' % (DAFNY_VERSION, key[0], key[1]))
    name, want = DAFNY_RELEASES[key]
    url = 'https://github.com/dafny-lang/dafny/releases/download/v%s/%s' % (DAFNY_VERSION, name)
    os.makedirs(cache_dir(), exist_ok=True)
    print('forge: downloading Dafny %s (%s, about 60 MB, once)…' % (DAFNY_VERSION, name), file=log)
    fd, part = tempfile.mkstemp(prefix='dafny-', suffix='.zip.part', dir=cache_dir())
    try:
        digest = hashlib.sha256()
        with os.fdopen(fd, 'wb') as out, urllib.request.urlopen(url, timeout=120) as resp:
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                digest.update(chunk)
                out.write(chunk)
        if digest.hexdigest() != want:
            raise ConfigError('the Dafny download has SHA-256 %s, not the pinned %s; nothing was '
                              'installed' % (digest.hexdigest(), want))
        final = os.path.join(cache_dir(), 'dafny-' + DAFNY_VERSION)
        staging = tempfile.mkdtemp(prefix='dafny-unpack-', dir=cache_dir())
        with zipfile.ZipFile(part) as z:
            for info in z.infolist():
                target = os.path.realpath(os.path.join(staging, info.filename))
                if os.path.commonpath([target, os.path.realpath(staging)]) != os.path.realpath(staging):
                    raise ConfigError('the Dafny zip has a path outside its folder: ' + info.filename)
                z.extract(info, staging)
                mode = (info.external_attr >> 16) & 0o777
                if mode and not info.is_dir():
                    os.chmod(target, mode)
        if os.path.isdir(final):
            shutil.rmtree(final)
        os.replace(staging, final)
    finally:
        if os.path.exists(part):
            os.remove(part)
    path = cached_dafny()
    if not path:
        raise ConfigError('Dafny was unpacked but its program is missing or not executable')
    print('forge: Dafny %s is ready at %s' % (DAFNY_VERSION, path), file=log)
    return path


def find_dafny(root, cfg, download=True):
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
    cached = cached_dafny()
    if cached:
        return cached
    if download and not os.environ.get('FORGE_NO_DOWNLOAD'):
        return download_dafny()
    return None


def unsound_in(path):
    with open(path, encoding='utf-8', errors='replace') as f:
        code = re.sub(r'//[^\n]*', '', f.read())
    code = re.sub(r'/\*.*?\*/', '', code, flags=re.S)
    for pattern in UNSOUND:
        if re.search(pattern, code):
            return pattern
    return None


def include_closure(root, files):
    """The listed files and every file they include, directly or not (project-relative)."""
    seen, todo = [], [inside(root, f) for f in files]
    while todo:
        path = todo.pop()
        if path in seen or not os.path.isfile(path):
            continue
        seen.append(path)
        with open(path, encoding='utf-8', errors='replace') as f:
            text = f.read()
        for inc in re.findall(r'^\s*include\s+"([^"]+)"', text, flags=re.M):
            todo.append(inside(root, os.path.relpath(os.path.join(os.path.dirname(path), inc),
                                                      os.path.realpath(root))))
    return [os.path.relpath(x, os.path.realpath(root)) for x in seen]


def changed_since(root, base, paths):
    """Protected paths that differ from the git ref `base`. None if git cannot tell. A decision record counts as
    changed only if its normative part changed (see contract_hash)."""
    out = []
    for rel in paths:
        if rel.endswith('.md') and os.path.isfile(inside(root, rel)):
            # './': the path is relative to root (git -C), not to the top of the repository.
            old = subprocess.run(['git', '-C', root, 'show', '%s:./%s' % (base, rel)], stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, universal_newlines=True)
            if old.returncode == 0:
                if normative_text(old.stdout) != normative_text(read_text(inside(root, rel))):
                    out.append(rel)
                continue
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
        protected = [CONFIG, APPROVED] + sorted({f for c in cfg['checks'] for f in contract_files(c)})
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
        missing_contract = [f for f in contract_files(c) if not os.path.isfile(inside(root, f))]
        if missing_contract:
            put('ERROR', 'missing contract file(s): ' + ', '.join(missing_contract))
            continue
        unapproved = [f for f in contract_files(c) if f not in approved]
        if unapproved:
            put('NOT_APPROVED', '%s never approved. Read and approve.' % ', '.join(unapproved))
            continue
        changed = [f for f in contract_files(c) if not approved_matches(approved, f, inside(root, f))]
        if changed:
            put('SPEC_CHANGED', '%s changed after approval. Read the new version and approve it.'
                % ', '.join(changed))
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
        try:
            closure = include_closure(root, c['files'])
        except ConfigError as e:
            put('ERROR', str(e))
            continue
        for f in closure:
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
        if c.get('examples'):
            # The person's answers, written as asserts against the abstract spec. They verify only
            # if the spec forces exactly the answer the person gave.
            ex = c['examples']
            pattern = unsound_in(inside(root, ex))
            if pattern:
                put('UNSOUND_CONSTRUCT', '%s matches %s' % (ex, pattern))
                continue
            try:
                r = subprocess.run([dafny, 'verify', inside(root, ex)], cwd=root, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, timeout=TIMEOUT_S, universal_newlines=True)
            except subprocess.TimeoutExpired:
                put('ERROR', 'dafny ran longer than %d s on %s' % (TIMEOUT_S, ex))
                continue
            if r.returncode != 0:
                tail = '\n'.join(r.stdout.strip().split('\n')[-12:])
                put('EXAMPLES_FAILED' if r.returncode == 4 else 'ERROR',
                    'the spec does not force the answer you gave in %s (it says otherwise, or it '
                    'allows more than one answer). dafny exit %d: %s' % (ex, r.returncode, tail))
                continue
        try:
            r = subprocess.run([dafny, 'verify', '--verify-included-files'] + [inside(root, f) for f in c['files']],
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
        'warnings': stale_records(root, cfg) + app_syntax_warnings(root, cfg),
        'next_rule': next_rules(root, cfg),
    }


def summary(result):
    v = result['verdicts']
    ok = sum(1 for x in v.values() if x == 'VERIFIED')
    bad = [(n, x) for n, x in v.items() if x != 'VERIFIED']
    if not bad:
        return 'forge ✓ VERIFIED %d/%d (model PROVED; wrappers and other app code NOT proved)' % (ok, len(v))
    return 'forge ✗ %s %s · %d/%d verified' % (bad[0][1], bad[0][0], ok, len(v))


def report(result):
    lines = [summary(result)]
    for name, verdict in result['verdicts'].items():
        lines.append('%s %s: %s' % ('✓' if verdict == 'VERIFIED' else '✗', name, verdict))
    for name, verdict in result['verdicts'].items():
        if verdict != 'VERIFIED':
            lines.append('\n--- %s ---\n%s' % (name, result['detail'][name]))
    lines += ['! ' + w for w in result.get('warnings', [])]
    for name, n in (result.get('next_rule') or {}).items():
        lines.append('next rule for %s: "%s" (%s) is assumed by this rule but not proved. /forge:add it to close '
                     'the gap.' % (name, n['item'], ', '.join(n['code'])))
    if result['verdicts'] and all(x == 'VERIFIED' for x in result['verdicts'].values()):
        lines.append('VERIFIED covers: the Dafny model meets the approved spec. Not covered: Dafny\'s compilers (the '
                     'build fuzzes them), the wrapper that turns app values into model values, and any app code that '
                     'decides the same question without calling the model.')
    return '\n'.join(lines)


# ---------------------------------------------------------------------------- build

def run_to_file(cmd, cwd, out_path, env=None):
    """Run cmd with output to a file (no pipes: a pipe can deadlock). Returns exit code or None."""
    with open(out_path, 'wb') as out:
        try:
            return subprocess.run(cmd, cwd=cwd, stdout=out, stderr=subprocess.STDOUT,
                                  stdin=subprocess.DEVNULL, env=env, timeout=TIMEOUT_S).returncode
        except subprocess.TimeoutExpired:
            return None


def read_text(path):
    with open(path, encoding='utf-8', errors='replace') as f:
        return f.read()


def copy_dafny_sources(root, dest, skip):
    """Copy every .dfy file under root to dest, keeping the layout. Build output is skipped."""
    skip = [os.path.realpath(x) for x in skip]
    for folder, dirs, files in os.walk(root):
        real = os.path.realpath(folder)
        dirs[:] = [d for d in dirs if not d.startswith('.') and d != 'node_modules'
                   and os.path.join(real, d) not in skip]
        for name in files:
            if name.endswith('.dfy'):
                target = os.path.join(dest, os.path.relpath(folder, root))
                os.makedirs(target, exist_ok=True)
                shutil.copy2(os.path.join(folder, name), os.path.join(target, name))


def js_node_path():
    """Folder with bignumber.js, which Dafny's JavaScript output needs. Installed once."""
    cache = os.path.join(os.environ.get('XDG_CACHE_HOME') or os.path.expanduser('~/.cache'), 'forge-check', 'js')
    modules = os.path.join(cache, 'node_modules')
    if not os.path.isdir(os.path.join(modules, 'bignumber.js')):
        os.makedirs(cache, exist_ok=True)
        r = subprocess.run(['npm', 'install', '--silent', '--no-audit', '--no-fund', '--prefix', cache,
                            'bignumber.js@9'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           stdin=subprocess.DEVNULL, universal_newlines=True)
        if r.returncode != 0:
            return None
    return modules


def missing_tools(target):
    return [t for t in TARGETS[target][0] if not shutil.which(t)]


def fuzz_target(dafny, work, harness_rel, target, log):
    """Compile and run the harness in one target. Returns (status, normalized output or reason)."""
    missing = missing_tools(target)
    if missing:
        return 'TOOLCHAIN_MISSING', 'not on PATH: ' + ', '.join(missing)
    extra = TARGETS[target][1]
    if target == 'js':
        modules = js_node_path()
        if not modules:
            return 'TOOLCHAIN_MISSING', 'npm could not install bignumber.js'
        code = run_to_file([dafny, 'translate', 'js', '--no-verify', '--include-runtime', harness_rel], work, log)
        if code != 0:
            return 'FAILED', 'dafny translate js: exit %s\n%s' % (code, read_text(log)[-600:])
        env = dict(os.environ, NODE_PATH=modules)
        code = run_to_file(['node', os.path.splitext(harness_rel)[0] + '.js'], work, log, env)
        if code == 0:  # the same harness through the generated ES module (js_wrapper.py) must print the same
            err = js_module_check(work, harness_rel, read_text(log), log)
            if err:
                return 'FAILED', err
    else:
        code = run_to_file([dafny, 'run', '--no-verify', '--target:' + target] + extra + [harness_rel], work, log)
    text = read_text(log)
    if code != 0:
        return 'FAILED', 'exit %s\n%s' % ('timeout' if code is None else code, text[-600:])
    lines = [l for l in text.split('\n') if l.strip() and not l.startswith('Dafny program verifier')]
    return 'PASSED', '\n'.join(lines)


def js_module_check(work, harness_rel, plain_output, log):
    """Runs the harness as the generated ES module, with code generation from strings forbidden (no eval, no new
    Function), and requires the output of the plain emitted script. Returns None or a reason."""
    import js_wrapper
    folder, base = os.path.split(os.path.join(work, os.path.splitext(harness_rel)[0]))
    err = js_wrapper.write_module(folder, base)
    if err:
        return err
    code = run_to_file(['node', '--disallow-code-generation-from-strings', os.path.join(folder, base + '.mjs')],
                       work, log, dict(os.environ, NODE_PATH=''))
    if code != 0:
        return 'the generated ES module of the harness: exit %s\n%s' % (code, read_text(log)[-600:])
    if read_text(log) != plain_output:
        return 'the generated ES module of the harness printed other output than the emitted script'
    return None


def emit_target(dafny, work, files, target, out_dir, name, log, check=None):
    extra = TARGETS[target][1]
    cmd = [dafny, 'translate', target, '--no-verify', '--include-runtime'] + extra + \
          ['--output', os.path.join(out_dir, name)] + files
    code = run_to_file(cmd, work, log)
    if code != 0:
        return 'exit %s\n%s' % (code, read_text(log)[-600:])
    if target == 'go':
        # Dafny's Go output is GOPATH-style (packages named "dafny", "Wallet", ...). Inside the person's own Go
        # module it would break their `go build ./...`; a go.mod of its own makes Go treat it as separate.
        with open(os.path.join(out_dir, name + '-go', 'go.mod'), 'w', encoding='utf-8') as f:
            f.write('// Written by forge: keeps this compiled proved code out of the surrounding Go module.\n'
                    'module forgeverified\n\ngo 1.21\n')
    if target == 'js':  # one ES module with real exports next to the emitted script (cli/js_wrapper.py)
        import js_wrapper
        return js_wrapper.write_module(out_dir, name, check)
    return None


def tree_hashes(folder):
    """Hashes of the emitted files. Caches that running the code creates (__pycache__, .pyc) are not emitted
    code and are skipped, so importing the built Python module does not make the build look stale."""
    out = {}
    for d, dirs, files in os.walk(folder):
        dirs[:] = [x for x in dirs if x != '__pycache__']
        for f in files:
            if f.endswith(('.pyc', '.pyo')):
                continue
            path = os.path.join(d, f)
            out[os.path.relpath(path, folder).replace(os.sep, '/')] = sha256(path)
    return out


def app_files(root, c):
    """The app files a check touches: those that call the built code (usedBy), the code under test the diff
    watches, and the diff adapters (the wrappers)."""
    out = [u['file'] for u in (c.get('build') or {}).get('usedBy', [])]
    for d in diff_entries(c):
        out += list(d.get('watch') or []) + ([d['adapter']] if d.get('adapter') else [])
    seen = []
    for f in out:
        if f not in seen and os.path.isfile(inside(root, f)):
            seen.append(f)
    return seen


_JS_EXT = ('.ts', '.mts', '.cts', '.js', '.mjs', '.cjs')
_IMPORT = re.compile(r'(?:\bfrom\s*|\bimport\s*\(\s*|\brequire\s*\(\s*|^\s*import\s+)["\'](\.{1,2}/[^"\']+)["\']', re.M)


def dependents(root, files, limit=2000):
    """JS/TS files that import any of `files`, directly or through other files (relative imports only): a broken
    file here can break the path to the proved code even though no check names it."""
    tracked = [f for f in project_files(root) if f.endswith(_JS_EXT) and not f.startswith(('node_modules/',))]
    rev = {}
    for f in tracked[:limit]:
        try:
            text = read_text(os.path.join(root, f))
        except OSError:
            continue
        for m in _IMPORT.finditer(text):
            base = os.path.normpath(os.path.join(os.path.dirname(f), m.group(1)))
            for cand in [base] + [base + e for e in _JS_EXT] + [os.path.join(base, 'index' + e) for e in _JS_EXT]:
                if os.path.isfile(os.path.join(root, cand)):
                    rev.setdefault(cand, set()).add(f)
                    break
    seen, todo = set(files), [os.path.normpath(f) for f in files]
    while todo:
        for g in rev.get(todo.pop(), ()):
            if g not in seen:
                seen.add(g)
                todo.append(g)
    return sorted(seen - set(files))


def syntax_errors(root, files):
    """A cheap sanity step, not a proof: does each file at least parse? Returns (errors, unchecked)."""
    errors, unchecked = [], []
    node = shutil.which('node')
    js = [f for f in files if os.path.splitext(f)[1] in _JS_EXT]
    if js and node:
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'eval', 'syntax_check.mjs')
        r = subprocess.run([node, script] + [inside(root, f) for f in js], stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, universal_newlines=True, timeout=300)
        if r.returncode == 1 and r.stdout.strip() and not r.stdout.lstrip().startswith(('file:', 'node:')):
            errors += [os.path.relpath(l.split(': ', 1)[0], root) + ': ' + l.split(': ', 1)[-1]
                       for l in r.stdout.strip().split('\n') if l]
        elif r.returncode != 0:  # 3: no TypeScript parser in this Node; anything else: the checker itself failed
            unchecked += ['%s (this Node cannot parse TypeScript; Node 22.13 or later can)' % f for f in js]
    for rel in files:
        if os.path.splitext(rel)[1] in _JS_EXT and node:
            continue
        path = inside(root, rel)
        ext = os.path.splitext(rel)[1]
        if ext == '.py':
            try:
                compile(read_text(path), rel, 'exec')
            except SyntaxError as e:
                errors.append('%s:%s: %s' % (rel, e.lineno, e.msg))
            continue
        if ext == '.go' and shutil.which('gofmt'):
            r = subprocess.run(['gofmt', '-e', '-l', path], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               universal_newlines=True, timeout=120)
            if r.returncode != 0:
                errors.append('%s: %s' % (rel, r.stdout.strip().split('\n')[0].replace(path, rel)))
            continue
        unchecked.append(rel)
    return errors, unchecked


def build_one(root, cfg, c, dafny, check_only):
    bd = c['build']
    name = c['name'].replace('-', '_')
    out_root = inside(root, bd['out'])
    res = {'verdict': 'BUILT', 'targets': {}, 'notes': []}

    def fail(verdict, note):
        if res['verdict'] == 'BUILT':
            res['verdict'] = verdict
        res['notes'].append(note)

    # 0. sanity: the app files this check touches must at least parse (a missing brace is caught here, not in
    # production). Optional "typecheck": a command run in the project folder, for example
    # ["npx", "--no-install", "tsc", "--noEmit"].
    touched = app_files(root, c)
    errs, unchecked = syntax_errors(root, touched + dependents(root, touched))
    if errs:
        fail('APP_SYNTAX_ERROR', 'app file(s) do not parse: ' + '; '.join(errs[:5]))
        return res
    if unchecked:
        res['notes'].append('not syntax-checked (no checker for this file type here): ' + ', '.join(unchecked))
    if bd.get('typecheck'):
        try:
            r = subprocess.run(list(bd['typecheck']), cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               universal_newlines=True, timeout=600)
            if r.returncode != 0:
                fail('TYPECHECK_FAILED', '%s: exit %d\n%s' % (' '.join(bd['typecheck']), r.returncode, r.stdout[-800:]))
                return res
        except (OSError, subprocess.TimeoutExpired) as e:
            fail('TYPECHECK_FAILED', '%s: %s' % (' '.join(bd['typecheck']), e))
            return res

    with tempfile.TemporaryDirectory() as tmp:
        # 1. fuzz: one clean copy of the sources per target, so builds do not touch the project
        fuzz = bd.get('fuzz')
        outputs = {}
        if not fuzz:
            fail('NO_FUZZ', 'no "fuzz" harness: the compiled code would be untested. Add one.')
        else:
            pattern = unsound_in(inside(root, fuzz))
            if pattern:
                fail('UNSOUND_CONSTRUCT', '%s matches %s' % (fuzz, pattern))
            r = subprocess.run([dafny, 'verify', inside(root, fuzz)], cwd=root, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, universal_newlines=True)
            if r.returncode != 0:
                fail('FUZZ_NOT_VERIFIED', 'dafny verify %s: exit %d\n%s' % (fuzz, r.returncode, r.stdout[-600:]))
            else:
                for t in bd['targets']:
                    work = os.path.join(tmp, 'fuzz-' + t)
                    copy_dafny_sources(root, work, [out_root])
                    status, text = fuzz_target(dafny, work, fuzz, t, os.path.join(tmp, 'fuzz-%s.log' % t))
                    entry = {'fuzz': status}
                    if status == 'PASSED':
                        outputs[t] = text
                        entry['fuzz_output_sha256'] = hashlib.sha256(text.encode()).hexdigest()
                        entry['fuzz_output_lines'] = text.count('\n') + 1
                    else:
                        entry['fuzz_detail'] = text
                        fail('TOOLCHAIN_MISSING' if status == 'TOOLCHAIN_MISSING' else 'FUZZ_FAILED',
                             '%s: %s' % (t, text.split('\n')[0]))
                    res['targets'][t] = entry
                if len(set(outputs.values())) > 1:
                    fail('TARGETS_DIFFER', 'the targets printed different results: '
                         + ', '.join('%s=%s' % (t, res['targets'][t]['fuzz_output_sha256'][:12]) for t in outputs))

        # 2. emit the proved code
        work = os.path.join(tmp, 'emit-src')
        copy_dafny_sources(root, work, [out_root])
        fresh = os.path.join(tmp, 'emit')
        for t in bd['targets']:
            entry = res['targets'].setdefault(t, {})
            err = emit_target(dafny, work, c['files'], t, os.path.join(fresh, t), name,
                              os.path.join(tmp, 'emit-%s.log' % t), c['name'])
            if err:
                entry['emit'] = 'FAILED'
                entry['emit_detail'] = err
                fail('EMIT_FAILED', '%s: %s' % (t, err.split('\n')[0]))
                continue
            entry['emit'] = 'OK'
            entry['files'] = tree_hashes(os.path.join(fresh, t))
            dest = os.path.join(out_root, c['name'], t)
            if check_only:
                on_disk = tree_hashes(dest) if os.path.isdir(dest) else {}
                if on_disk != entry['files']:
                    changed = sorted(set(on_disk) ^ set(entry['files'])
                                     | {k for k in on_disk if entry['files'].get(k) != on_disk[k]})
                    fail('STALE_BUILD', '%s/%s/%s differs from a fresh build (%s). Run build again; '
                         'never edit emitted code by hand.' % (bd['out'], c['name'], t, ', '.join(changed[:4])))
            elif res['verdict'] == 'BUILT':
                if os.path.isdir(dest):
                    shutil.rmtree(dest)
                shutil.copytree(os.path.join(fresh, t), dest)

    # 3. does the application import the built code? (a text check)
    for u in bd.get('usedBy', []):
        path = inside(root, u['file'])
        if not os.path.isfile(path) or u['contains'] not in read_text(path):
            fail('NOT_WIRED', '%s does not contain %r, so the app may not call the built code'
                 % (u['file'], u['contains']))
    if not bd.get('usedBy'):
        res['notes'].append('no "usedBy": nothing checks that the app calls the built code')
    # 4. does a service test of the app call the built code? (a live trace, js target)
    if bd.get('serviceTest'):
        service_test(root, c, res, fail)
    return res


def service_test(root, c, res, fail):
    """Runs the check's "serviceTest" command with FORGE_TRACE set. The generated js module writes one line per
    process on its first call. SERVICE_TEST_FAILED if the command fails, NOT_CALLED if no call was traced."""
    st = c['build']['serviceTest']
    cmd = st.get('command') if isinstance(st, dict) else None
    if not isinstance(cmd, list) or not cmd or not all(isinstance(x, str) for x in cmd):
        fail('SERVICE_TEST_FAILED', '"serviceTest" needs "command": [program, args...]')
        return
    fd, trace = tempfile.mkstemp(prefix='forge-trace-')
    os.close(fd)
    try:
        log = trace + '.log'
        code = run_to_file(cmd, inside(root, st.get('cwd', '.')), log, dict(os.environ, FORGE_TRACE=trace))
        calls = sum(1 for line in read_text(trace).split('\n') if line.split('\t')[0] == c['name'])
        res['service_test'] = {'command': cmd, 'exit': code, 'calls': calls}
        if code != 0:
            fail('SERVICE_TEST_FAILED', 'serviceTest %s: exit %s\n%s' % (' '.join(cmd), 'timeout' if code is None
                                                                       else code, read_text(log)[-400:]))
        elif calls == 0:
            fail('NOT_CALLED', 'serviceTest %s passed, but no process called the built code (the generated js '
                 'module traces its first call; a loader that evaluates the .js file is not traced)' % ' '.join(cmd))
        else:
            res['notes'].append('serviceTest: %d process(es) called the built code' % calls)
    finally:
        for f in (trace, trace + '.log'):
            if os.path.exists(f):
                os.remove(f)


def run_build(root, check_only):
    result = run_checks(root)
    result['build'] = {}
    if any(v != 'VERIFIED' for v in result['verdicts'].values()):
        return result
    cfg = load_config(root)
    dafny = find_dafny(root, cfg)
    for c in cfg['checks']:
        if 'build' in c:
            result['build'][c['name']] = build_one(root, cfg, c, dafny, check_only)
    if not check_only:
        for c in cfg['checks']:
            b = result['build'].get(c['name'])
            if b and b['verdict'] == 'BUILT':
                manifest = {
                    'check': c['name'],
                    'labels': 'Dafny model: PROVED. This emitted code: NATIVE CHECK (unverified compiler). '
                              'Fuzz run: FINITE TEST.',
                    'sources': {f: sha256(inside(root, f)) for f in [c['spec']] + c['files']},
                    'targets': b['targets'],
                }
                dest = os.path.join(inside(root, c['build']['out']), c['name'])
                with open(os.path.join(dest, 'forge-build.json'), 'w', encoding='utf-8') as f:
                    f.write(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
    return result


def build_report(result):
    lines = [report(result)]
    for name, b in result.get('build', {}).items():
        parts = []
        for t, e in b['targets'].items():
            parts.append('%s fuzz=%s%s emit=%s' % (t, e.get('fuzz', '-'),
                         ' (%d lines)' % e['fuzz_output_lines'] if 'fuzz_output_lines' in e else '',
                         e.get('emit', '-')))
        lines.append('%s build %s: %s\n  %s' % ('✓' if b['verdict'] == 'BUILT' else '✗', name,
                                                b['verdict'], '\n  '.join(parts)))
        lines += ['  note: ' + n for n in b['notes']]
    if not result.get('build') and all(v == 'VERIFIED' for v in result['verdicts'].values()):
        lines.append('no check has a "build" entry')
    return '\n'.join(lines)


# ---------------------------------------------------------------------------- mutate

# Small, typical bugs: (name, pattern, replacement). Applied to one occurrence at a time, outside comments
# and outside the approved binding prefix.
MUTATIONS = [
    ('<= to <', r'<=', '<'),
    ('< to <=', r'(?<![<=!])<(?![=:<])', '<='),
    ('>= to >', r'>=', '>'),
    ('> to >=', r'(?<![-=>])>(?![=>])', '>='),
    ('== to !=', r'==(?!>)', '!='),
    ('!= to ==', r'!=', '=='),
    ('&& to ||', r'&&', '||'),
    ('|| to &&', r'\|\|', '&&'),
    ('+ to -', r'(?<=\s)\+(?=\s)', '-'),
    ('- to +', r'(?<=\s)-(?=\s)', '+'),
    ('+ 1 removed', r'\s\+\s1\b', ''),
    ('0 to 1', r'(?<![\w.])0(?![\w.])', '1'),
    ('true to false', r'\btrue\b', 'false'),
    ('false to true', r'\bfalse\b', 'true'),
]


def mutation_sites(text, skip_until):
    """(name, start, end, replacement) for every mutation site outside comments and the binding prefix."""
    code = list(text)
    for m in re.finditer(r'//[^\n]*|/\*.*?\*/', text, flags=re.S):
        for i in range(m.start(), m.end()):
            code[i] = ' '
    masked = ''.join(code)
    sites = []
    for name, pattern, new in MUTATIONS:
        for m in re.finditer(pattern, masked):
            if m.start() >= skip_until:
                sites.append((name, m.start(), m.end(), new))
    return sorted(sites, key=lambda x: (x[1], x[0]))


def run_mutate(root, limit):
    base = run_checks(root)
    if any(v != 'VERIFIED' for v in base['verdicts'].values()):
        raise ConfigError('every check must be VERIFIED before mutation testing: ' + json.dumps(base['verdicts']))
    cfg = load_config(root)
    out = {'mutants': [], 'caught': 0, 'survived': 0, 'skipped': 0}
    jobs = []
    for c in cfg['checks']:
        for f in c['files']:
            text = read_text(inside(root, f))
            b = c.get('binding')
            skip = len(b['prefix']) if b and b['file'] == f and text.startswith(b['prefix']) else 0
            for site in mutation_sites(text, skip):
                jobs.append((c['name'], f, text, site))
    if len(jobs) > limit:  # spread the sample over the whole file list instead of taking the first ones
        step = len(jobs) / float(limit)
        jobs = [jobs[int(i * step)] for i in range(limit)]
    for name, f, text, (mname, start, end, new) in jobs:
        mutated = text[:start] + new + text[end:]
        line = text.count('\n', 0, start) + 1
        with tempfile.TemporaryDirectory() as tmp:
            copy = os.path.join(tmp, 'p')
            shutil.copytree(root, copy, ignore=shutil.ignore_patterns('.git', 'node_modules', 'verified'))
            with open(os.path.join(copy, f), 'w', encoding='utf-8') as fh:
                fh.write(mutated)
            verdict = run_checks(copy)['verdicts'].get(name, 'ERROR')
        status = 'survived' if verdict == 'VERIFIED' else 'caught'
        out[status] += 1
        out['mutants'].append({'check': name, 'file': f, 'line': line, 'mutation': mname,
                               'verdict': verdict, 'status': status,
                               'text': text.split('\n')[line - 1].strip()[:120]})
    return out


def mutate_report(out):
    total = out['caught'] + out['survived']
    lines = ['forge mutate: %d of %d planted bugs caught' % (out['caught'], total)]
    for m in out['mutants']:
        if m['status'] == 'survived':
            lines.append('  SURVIVED %s:%d  %s  in: %s' % (m['file'], m['line'], m['mutation'], m['text']))
    if out['survived']:
        lines.append('A survivor is a change the spec allows. Either it is harmless (an equivalent mutant), or '
                     'the spec is too weak there: read each one.')
    return '\n'.join(lines)


# ---------------------------------------------------------------------------- diff

def build_is_fresh(root, c):
    """True if the emitted code's manifest names the current hashes of the spec and the Dafny files."""
    manifest = os.path.join(inside(root, c['build']['out']), c['name'], 'forge-build.json')
    if not os.path.isfile(manifest):
        return False
    sources = (read_json(manifest, {}) or {}).get('sources', {})
    want = [c['spec']] + c['files']
    return sorted(sources) == sorted(want) and all(sources[f] == sha256(inside(root, f)) for f in want)


def run_diff(root, inputs=None, budget_ms=None, seed=None):
    """Runs every diff entry through the language-neutral core (forge_diff.py)."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import forge_diff
    cfg = load_config(root)
    here = os.path.dirname(os.path.abspath(__file__))
    out = {'at': datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds'), 'checks': {}}
    jobs = []
    for c in cfg['checks']:
        entries = diff_entries(c)
        for df in entries:
            jobs.append((c, df, c['name'] if len(entries) == 1 else '%s:%s' % (c['name'], df.get('name') or df['lang'])))
    for c, df, key in jobs:
        res = out['checks'][key] = {}
        if not c.get('build'):
            res['verdict'] = 'NO_BUILD'
            res['detail'] = 'diff compares against the compiled proved code: add a "build" entry first'
            continue
        if not build_is_fresh(root, c):
            res['verdict'] = 'BUILD_STALE'
            res['detail'] = 'the compiled proved code does not match the current Dafny files: run build'
            continue
        n = inputs if inputs is not None else df.get('inputs_per_run', 1500)
        ms = budget_ms if budget_ms is not None else df.get('budget_ms', 1500)
        s = seed if seed is not None else int.from_bytes(os.urandom(4), 'little')
        env = dict(os.environ)
        if df['lang'] == 'js':
            if not shutil.which('node'):
                res['verdict'], res['detail'] = 'TOOLCHAIN_MISSING', 'node is not on PATH'
                continue
            modules = js_node_path()
            if not modules:
                res['verdict'], res['detail'] = 'TOOLCHAIN_MISSING', 'npm could not install bignumber.js'
                continue
            env['NODE_PATH'] = modules
            cmd = ['node', os.path.join(here, 'eval', 'node_eval.mjs'), inside(root, df['adapter'])]
        elif df['lang'] == 'py':
            cmd = [sys.executable, os.path.join(here, 'eval', 'py_eval.py'), inside(root, df['adapter'])]
        elif df['lang'] in ('go', 'rs', 'java', 'cs'):
            import eval_build
            target = df['lang']
            if target not in c['build'].get('targets', []):
                res['verdict'] = 'NO_BUILD'
                res['detail'] = 'add "%s" to the check\'s build targets, approve, and run build' % target
                continue
            name = c['name'].replace('-', '_')
            emitted = os.path.join(inside(root, c['build']['out']), c['name'], target)
            if target != 'cs':  # C# output is one file directly in <out>/<check>/cs
                emitted = os.path.join(emitted, '%s-%s' % (name, {'go': 'go', 'rs': 'rust', 'java': 'java'}[target]))
            work = os.path.join(root, DIR, 'cache', 'diff-%s-%s' % (key.replace(':', '-'), target))
            try:
                if target == 'go':
                    cmd = eval_build.build_go(root, emitted, inside(root, df['adapter']), work)
                elif target == 'java':
                    cmd = eval_build.build_java(root, emitted, inside(root, df['adapter']), work, df.get('java_sources'))
                elif target == 'cs':
                    cmd = eval_build.build_cs(root, emitted, inside(root, df['adapter']), work, df.get('cs_sources'))
                else:
                    cmd = eval_build.build_rust(root, emitted, inside(root, df['adapter']), work, df.get('rust_deps'))
            except eval_build.BuildError as e:
                missing = 'is not on PATH' in str(e)
                res['verdict'] = 'TOOLCHAIN_MISSING' if missing else 'DIFF_ERROR'
                res['detail' if missing else 'error'] = str(e)
                continue
        else:
            cmd = list(df['run'])
        data = forge_diff.run_entry(cmd, root, env, inside(root, df['inputs']), s, n, ms,
                                    os.path.join(root, REGRESSIONS, key.replace(':', '-')),
                                    messy=df.get('messy', True) is not False)
        res.update(data)
        if data.get('error') and 'Unknown file extension ".ts"' in data['error']:
            res['verdict'] = 'TOOLCHAIN_MISSING'
            res['detail'] = ('this Node cannot import TypeScript directly (%s); it needs Node 22.18 or newer'
                             % subprocess.run(['node', '--version'], stdout=subprocess.PIPE,
                                              universal_newlines=True).stdout.strip())
        elif data.get('error'):
            res['verdict'] = 'DIFF_ERROR'
        elif data.get('disagreements') or (data.get('decisions') or {}).get('failing'):
            res['verdict'] = 'DISAGREE'
        else:
            res['verdict'] = 'DIFF_OK'
    return out


def update_diff_stats(root, out):
    """Running totals per diff key: how much searching has happened, and how much since the last NEW failure.
    A long stretch with nothing new is evidence (not proof) that the easy bugs are gone."""
    path = os.path.join(root, DIFF_STATS)
    stats = read_json(path, {}) or {}
    for key, r in out['checks'].items():
        if r['verdict'] not in ('DIFF_OK', 'DISAGREE'):
            continue
        st = stats.setdefault(key, {'runs': 0, 'inputs': 0, 'first_run': out['at'], 'last_new_failure': None,
                                    'runs_since_new': 0, 'inputs_since_new': 0})
        st['runs'] += 1
        st['inputs'] += r.get('inputs', 0)
        failing_now = sorted(f['id'] for f in (r.get('decisions') or {}).get('failing', []))
        newly_failing = [f for f in failing_now if f not in st.get('failing_decisions', [])]
        st['failing_decisions'] = failing_now
        if any(d.get('new') for d in r.get('disagreements', [])) or newly_failing:
            st['last_new_failure'], st['runs_since_new'], st['inputs_since_new'] = out['at'], 0, 0
        else:
            st['runs_since_new'] += 1
            st['inputs_since_new'] += r.get('inputs', 0)
        st['last_run'] = out['at']
        r['stats'] = st
    write_json(root, DIFF_STATS, stats)


def messy_line(d):
    """One line under a messy disagreement: which field was odd, and what kind of failure it is."""
    m = d['messy']
    what = ('THROWS_ON_MESSY_INPUT (%s): the wrapper crashed instead of failing closed' % d['got']['__throws__']
            if d.get('kind') == 'THROWS_ON_MESSY_INPUT' else 'the wrapper answered differently from the fail-closed answer')
    return '    messy field %s = %s: %s' % (m['field'], m['as'], what)


def messy_summary(m):
    return ('  messy inputs: %d sent to %s (%d field/kind pairs tried; %d not judged: no fail-closed answer and the '
            'proved rule cannot take them)' % (m['inputs'], ', '.join(m['candidates']), m['pairs_tried'], m['not_judged']))


def diff_report(out):
    if not out['checks']:
        return 'forge diff: no check has a "diff" entry yet (/forge fuzz sets one up; /forge add does it for code that already exists)'
    lines = []
    for name, r in out['checks'].items():
        v = r['verdict']
        dec = r.get('decisions') or {}
        dec_line = ('  your recorded decisions: %d of %d agree%s' % (
            dec['agree'], dec['total'], '; failing: ' + ', '.join('%s (%s)' % (f['id'], ', '.join(f['candidates']))
                                                             for f in dec['failing']) if dec['failing'] else '')
                    if dec.get('total') else '  your recorded decisions: none listed in the adapter (add "decisions")')
        st = r.get('stats')
        st_line = ('  searched so far: %s inputs in %d runs; nothing new for %d runs (%s inputs)%s' % (
            format(st['inputs'], ','), st['runs'], st['runs_since_new'], format(st['inputs_since_new'], ','),
            '' if st['last_new_failure'] is None else ', last new failure ' + st['last_new_failure'])
                   if st else None)
        if v == 'DIFF_OK':
            lines.append('✓ %s: %d inputs (%d skipped, outside the rule), %d saved cases replayed, 0 disagreements '
                         'for %s, %d ms, seed %s' % (name, r['inputs'], r['out_of_domain'], r['replayed'],
                                                     ', '.join(r.get('candidates') or []), r['ms'], r['seed']))
            lines.append(dec_line)
            if r.get('messy'):
                lines.append(messy_summary(r['messy']))
            if st_line:
                lines.append(st_line)
        elif v == 'DISAGREE':
            lines.append('✗ %s: %d failing decision(s), %d other disagreement(s) in %d random inputs (%d ms, seed %s); '
                         'per candidate: %s'
                         % (name, len(dec.get('failing', [])), len(r['disagreements']), r['inputs'], r['ms'], r['seed'],
                            ', '.join('%s %d' % kv for kv in (r.get('per_candidate') or {}).items())))
            groups = {}
            for d in r['disagreements']:
                groups.setdefault((d['candidate'], d.get('group', '')), []).append(d)
            if len(groups) < len(r['disagreements']):
                lines.append('  %d kind(s) of wrong answer; examples of different shape under each:' % len(groups))
            for d in sorted(r['disagreements'], key=lambda x: (x['candidate'], x.get('group', ''))):
                lines.append('  %s%s: input %s → %s %s, %s says %s  [%s]' % (
                    'NEW ' if d.get('new') else '', d['candidate'], json.dumps(d['input'], ensure_ascii=False)[:200],
                    'expected (fail-closed or proved)' if d.get('messy') else 'proved rule says',
                    json.dumps(d['reference'], ensure_ascii=False)[:120], d['candidate'],
                    json.dumps(d['got'], ensure_ascii=False)[:120], d['file']))
                if d.get('messy'):
                    lines.append(messy_line(d))
            lines.append(dec_line)
            if r.get('messy'):
                lines.append(messy_summary(r['messy']))
            if st_line:
                lines.append(st_line)
            lines.append('  Each one is a bug in that code, or a place where the spec says something other than '
                         'what you meant. Saved cases under %s/%s/ run first next time.' % (REGRESSIONS, name.replace(':', '-')))
        else:
            lines.append('✗ %s: %s: %s' % (name, v, (r.get('detail') or r.get('error') or '')[:600]))
    return '\n'.join(lines)


# ---------------------------------------------------------------------------- invariants (the citadel)

def _section(text, heading):
    """Lines under a '## heading' (up to the next '## ')."""
    m = re.search(r'^##\s+' + re.escape(heading) + r'[^\n]*\n(.*?)(?=^##\s|\Z)', text, flags=re.M | re.S)
    return m.group(1).strip() if m else ''


def _guarantees(spec_text):
    """Numbered clause comments of the spec ('// W1: ...'), in plain words."""
    out = []
    for m in re.finditer(r'^\s*//\s*([A-Z][A-Za-z]{0,3}\d+[a-z]?)\s*[:.]\s*(.+)$', spec_text, flags=re.M):
        out.append((m.group(1), m.group(2).strip()))
    return out


def invariants_text(root):
    """INVARIANTS.md, generated only from the files an approval covers and forge.json. Deterministic: no times,
    no verdicts (those change; run check for them)."""
    cfg = load_config(root)
    approved = read_json(os.path.join(root, APPROVED), {}) or {}
    lines = ['# Invariants', '',
             'Generated by Forge from the approved rules (`forge_check.py invariants`). Do not edit by hand: CI fails',
             'when this file differs from a fresh generation, so a change to any rule shows up here as a plain diff.',
             'Current proof verdicts: run `/forge check`. A rule here is proved only for its Dafny model; see',
             '"Trust base" below.', '']
    rows, sections = [], []
    for c in cfg['checks']:
        name = c['name']
        files = contract_files(c)
        state = []
        for f in files:
            p = inside(root, f)
            if not os.path.isfile(p):
                state.append('%s missing' % f)
            elif not approved_matches(approved, f, p):
                state.append('%s %s' % (f, 'changed since approval' if f in approved else 'not approved'))
        if approved.get(CONFIG) != sha256(os.path.join(root, CONFIG)):
            state.append('forge.json not approved as it is')
        spec_text = read_text(inside(root, c['spec']))
        dec_text = read_text(inside(root, c['decisions'])) if c.get('decisions') and os.path.isfile(inside(root, c['decisions'])) else ''
        words = re.search(r'Rule in the person\'s words:\s*(.+)', dec_text)
        words = words.group(1).strip() if words else '(no decision record)'
        g = _guarantees(spec_text)
        choices = [l for l in _section(dec_text, 'Choices').split('\n') if l.startswith('|')]
        n_dec = max(0, len(choices) - 2)
        targets = (c.get('build') or {}).get('targets', [])
        fuzzed = []
        for d in diff_entries(c):
            fuzzed.append('%s (%s)' % (', '.join(d.get('watch') or [d.get('adapter', '?')]), d['lang']))
        rows.append('| %s | %s | %d | %d | %s | %s | %s |' % (
            name, words.replace('|', '/'), len(g), n_dec, 'yes' if not state else '**no**',
            ', '.join(targets) or '—', '; '.join(fuzzed) or '—'))
        sec = ['## %s' % name, '', '> %s' % words, '',
               '**Approval:** %s' % ('all %d contract files and forge.json match their approved hashes.' % len(files)
                                     if not state else 'NOT approved as written: ' + '; '.join(state) + '.'), '']
        sec += ['### Guarantees (from `%s`)' % c['spec'], '']
        sec += ['- **%s** %s' % (k, v) for k, v in g] or ['- (the spec has no numbered clause comments; see the formal clauses)']
        sec += ['', '<details><summary>The approved spec, in full</summary>', '', '```dafny', spec_text.rstrip(), '```',
                '', '</details>', '']
        if choices:
            sec += ['### Decisions (each one is checked by Dafny against the spec in `%s`)' % c.get('examples', '?'), ''] + choices + ['']
        defaults = _section(dec_text, 'Defaults')
        if defaults:
            sec += ['### Defaults confirmed', '', defaults, '']
        scope = _section(dec_text, 'Out of scope')
        sec += ['### Not guaranteed by this rule', '', scope or '- (the decision record names nothing out of scope)', '']
        code = ['- Proved implementation: %s' % ', '.join('`%s`' % f for f in c['files'])]
        if c.get('binding'):
            code.append('- Bound to the spec by the exact first lines of `%s`' % c['binding']['file'])
        if targets:
            code.append('- Compiled to: %s (into `%s/%s/`; compiled code is a NATIVE CHECK)' % (', '.join(targets), c['build']['out'], name))
        for u in (c.get('build') or {}).get('usedBy', []):
            code.append('- Used by: `%s`' % u['file'])
        if c.get('watchedFields'):
            code.append('- Code outside the rule is searched (`bypass`) for: %s; %d allowed bypass(es), each approved here (UNAPPROVED_BYPASS for any other)' % (
                ', '.join('`%s`' % t for t in c['watchedFields']), len(c.get('allowedBypasses') or [])))
        for d in diff_entries(c):
            code.append('- Fuzzed against the shipped code (%s): %s' % (d['lang'], ', '.join('`%s`' % w for w in d.get('watch') or []) or d.get('adapter')))
        sec += ['### Code', ''] + code + ['']
        sections += sec
    lines += ['| Rule | In the owner\'s words | Guarantees | Decisions | Approved | Compiled to | Fuzzed against |',
              '| --- | --- | --- | --- | --- | --- | --- |'] + rows + ['']
    lines += sections
    lines += ['## Trust base (what every rule above rests on)', '',
              '- The Dafny 4.11 verifier and the Z3 solver: a bug there could accept a false proof.',
              '- The spec saying what the owner meant: the decisions above are checked against it; nothing checks the',
              '  spec against intent beyond them.',
              '- Dafny\'s compilers, for any compiled code (NATIVE CHECK), and the thin unproved wrappers around it.',
              '- The application calling the proved code: `usedBy` is a text check, not a call-graph analysis.',
              '- Fuzzing is a FINITE TEST: it finds differences; it does not prove their absence.', '',
              '## Not covered', '',
              'Only the files listed under "Code" are checked by a rule. Everything else in this repository has no Forge',
              'guarantee.', '']
    return '\n'.join(lines)


def invariants_path(root, cfg):
    return inside(root, cfg.get('invariants', 'INVARIANTS.md'))


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


def spec_digest(text):
    """The spec for reading at approval: everything, except the bodies of lemmas and methods (proof steps and code,
    which Dafny checks). Predicate and function bodies stay: they ARE the meaning of the rule. The approval hashes
    the whole file."""
    out, skipping, depth, pending = [], False, 0, False
    for line in text.split('\n'):
        st = line.strip()
        if skipping:
            depth += line.count('{') - line.count('}')
            if depth <= 0:
                skipping = False
                out.append(line[:len(line) - len(line.lstrip())] + '{ … }  (body checked by Dafny, not shown)')
            continue
        if re.match(r'((ghost|static)\s+)*(lemma|method|constructor)\b', st):
            pending = True
        elif pending and re.match(r'(predicate|function|datatype|type|const|module|class|trait)\b', st):
            pending = False
        if pending and st.startswith('{'):
            pending = False
            depth = line.count('{') - line.count('}')
            if depth > 0:
                skipping = True
                continue
            out.append(line[:len(line) - len(line.lstrip())] + '{ … }  (body checked by Dafny, not shown)')
            continue
        out.append(line.rstrip())
    return '\n'.join(out).rstrip()


def config_digest(cfg):
    lines = []
    for c in cfg['checks']:
        lines.append('- %s: proves %s against spec %s%s' % (c['name'], ', '.join(c['files']), c['spec'],
                     '; bound by the first lines of %s' % c['binding']['file'] if c.get('binding') else ''))
        if c.get('watchedFields'):
            lines.append('  watched fields (code outside the rule that decides on them is listed): %s'
                         % ', '.join(map(str, c['watchedFields'])))
        bd = c.get('build') or {}
        if bd:
            lines.append('  build: %s into %s/; app callers: %s' % (', '.join(bd.get('targets', [])), bd.get('out'),
                         ', '.join(u['file'] for u in bd.get('usedBy', [])) or 'none listed'))
        for d in diff_entries(c):
            lines.append('  fuzz: %s (%s) against the proved model' % (', '.join(d.get('watch') or [d.get('adapter', '?')]), d['lang']))
    return '\n'.join(lines)


def show_specs(root, full=False):
    """Text of what an approval covers, and the hashes of exactly the files it covers. By default a digest for
    reading (every spec clause complete, every answer, the whole decision record); full=True prints every file."""
    cfg = load_config(root)
    approved = read_json(os.path.join(root, APPROVED), {}) or {}
    hashes = {CONFIG: sha256(os.path.join(root, CONFIG))}

    def state(key):
        if approved.get(key) == hashes[key] or (key in approved and key != CONFIG
                                                and approved_matches(approved, key, os.path.join(root, key))):
            return 'approved'
        return 'CHANGED since it was approved' if key in approved else 'not approved yet'

    parts = ['=== which code is checked against which spec: %s (%s) ===\n%s'
             % (CONFIG, state(CONFIG), json.dumps(cfg, indent=2) if full else config_digest(cfg))]
    for c in cfg['checks']:
        for rel in contract_files(c):
            if rel in hashes:
                continue
            hashes[rel] = contract_hash(inside(root, rel))
            # The whole file, every word: an approval covers the complete file (by hash), so the person must be
            # able to read all of it. (Before 0.12.1 the spec was shortened to its clause lines.)
            body = read_text(inside(root, rel)).rstrip()
            if not full and state(rel) == 'approved':
                # Already approved and unchanged: one line, so the screen shows only what is new.
                parts.append('=== %s %s: %s (approved, unchanged, sha256 %s…) ===' % (
                    c['name'], 'spec' if rel == c['spec'] else 'your answers' if rel == c.get('examples')
                    else 'decision record', rel, hashes[rel][:12]))
                continue
            if not full and rel == c['spec']:
                body = spec_digest(body)
            elif not full and rel == c.get('examples'):
                ids = re.findall(r'//\s*(D\d+[a-z]?)\b[^\n]*', body)
                body = ('%d answers written as Dafny asserts (%s); each must follow from the spec, or check fails with '
                        'EXAMPLES_FAILED.' % (len(set(ids)), ', '.join(sorted(set(ids), key=lambda x: (len(x), x))))
                        if ids else body)
            kind = 'spec' if rel == c['spec'] else 'your answers' if rel == c.get('examples') else 'decision record'
            parts.append('=== %s %s: %s (%s, sha256 %s…) ===\n%s'
                         % (c['name'], kind, rel, state(rel), hashes[rel][:12], body))
    if not full:
        parts.append('This is a digest. The approval covers the complete files above by their sha256 (shown). '
                     'Read every line: /forge approve --full')
    return '\n\n'.join(parts), hashes


DESCRIBED = DIR + '/described.json'
_PATHLIKE = re.compile(r'(?<![\w/.-])((?:[\w.-]+/)*[\w.-]+\.[A-Za-z]{1,5})(?::\d+)?(?![\w/])')
_IDENT = re.compile(r'`([A-Za-z_$][\w$]{2,})(?:\(\))?`')
_SKIP_DIRS = ('.git', 'node_modules', '.forge', 'dist', 'build', 'target', '__pycache__', 'vendor')
_TEST_FILE = re.compile(r'(^|/)(tests?|__tests__|spec)/|[._-](test|spec)\.[A-Za-z]+$|(^|/)test_[^/]+\.py$')


def project_files(root):
    """Tracked files (git ls-files), or a walk that skips build, vendor and hidden folders."""
    try:
        r = subprocess.run(['git', '-C', root, 'ls-files', '-z'], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except OSError:  # no git on this machine: walk the folder
        r = None
    if r is not None and r.returncode == 0 and r.stdout:
        return [f for f in r.stdout.decode('utf-8', 'replace').split('\0') if f and os.path.isfile(os.path.join(root, f))]
    out = []
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if x not in _SKIP_DIRS and not x.startswith('.')]
        out += [os.path.relpath(os.path.join(d, f), root) for f in files]
    return out


def described_files(root, c):
    """Code a decision record talks about: file paths it names (`gate.ts`, `src/x.py:47`) and functions it names in
    backticks, found by a definition (function / def / func / fn / class / const …). The check's own contract and
    proved files are left out. A heuristic: it finds what the record names, not what it means."""
    rel = c.get('decisions')
    if not rel or not os.path.isfile(inside(root, rel)):
        return {}
    return refs_in_text(root, c, read_text(inside(root, rel)))


def refs_in_text(root, c, text):
    own = set(contract_files(c) + [c['spec']] + c['files'] + [CONFIG])
    out_dir = (c.get('build') or {}).get('out')
    found = set()
    for m in _PATHLIKE.finditer(text):
        cand = m.group(1)
        p = os.path.join(root, cand)
        if os.path.isfile(p) and cand not in own:
            found.add(os.path.normpath(cand))
    idents = sorted({m.group(1) for m in _IDENT.finditer(text)})
    code_ext = ('.ts', '.tsx', '.js', '.mjs', '.cjs', '.py', '.go', '.rs', '.java', '.cs', '.kt', '.rb')
    out_norm = os.path.normpath(out_dir) + os.sep if out_dir else None
    for ident in idents:
        pat = re.compile(r'\b(?:function|def|func|fn|class|const|let|var)\s+%s\b|^\s*(?:export\s+)?%s\s*[:=]\s*(?:async\s*)?\('
                         % (re.escape(ident), re.escape(ident)), re.M)
        hits = []
        for relf in project_files(root):
            if not relf.endswith(code_ext) or relf in own or _TEST_FILE.search(relf):
                continue
            if out_norm and os.path.normpath(relf).startswith(out_norm):
                continue
            full = os.path.join(root, relf)
            if os.path.getsize(full) <= 2000000 and pat.search(read_text(full)):
                hits.append(relf)
        # A name defined in many places says nothing about which code the record means: skip it.
        if 1 <= len(hits) <= 2:
            found.update(hits)
    return {f: sha256(os.path.join(root, f)) for f in sorted(found)}


def record_described(root):
    """Written at approval: the code each decision record describes, as it was when the person approved it."""
    cfg = load_config(root)
    write_json(root, DESCRIBED, {c['name']: described_files(root, c) for c in cfg['checks'] if c.get('decisions')})


def app_syntax_warnings(root, cfg):
    """check: the app files the checks touch, and every file that imports them, must parse (a warning in check;
    build fails with APP_SYNTAX_ERROR)."""
    touched = []
    for c in cfg['checks']:
        touched += [f for f in app_files(root, c) if f not in touched]
    if not touched:
        return []
    errs, _ = syntax_errors(root, touched + dependents(root, touched))
    return ['APP_SYNTAX_ERROR %s' % e for e in errs[:10]]


def stale_records(root, cfg):
    """Warnings (never a failed verdict): code a decision record describes changed after approval."""
    seen = read_json(os.path.join(root, DESCRIBED), {}) or {}
    out = []
    for c in cfg['checks']:
        changed = [f for f, h in sorted((seen.get(c['name']) or {}).items())
                   if not os.path.isfile(os.path.join(root, f)) or sha256(os.path.join(root, f)) != h]
        if changed:
            out.append('DECISION_NOTE_STALE %s: the decision record %s describes %s, which changed after approval. '
                       'Re-read the record: it may no longer match the code (the proof approval is not affected).'
                       % (c['name'], c['decisions'], ', '.join(changed)))
    return out


def next_rules(root, cfg):
    """For each check: the first "Out of scope" item that names code in this repo (a backticked function or a file
    path), as the suggested next rule. The record lists what the rule assumes; proving one of those assumptions
    closes a gap the proof depends on. A heuristic over the person's own words."""
    out = {}
    for c in cfg['checks']:
        rel = c.get('decisions')
        if not rel or not os.path.isfile(inside(root, rel)):
            continue
        for line in _section(read_text(inside(root, rel)), 'Out of scope').split('\n'):
            item = line.strip().lstrip('-*').strip()
            if not item:
                continue
            refs = sorted(refs_in_text(root, c, item))
            if refs:
                out[c['name']] = {'item': item, 'code': refs}
                break
    return out



def write_json(root, rel, value):
    os.makedirs(os.path.join(root, DIR), exist_ok=True)
    with open(os.path.join(root, rel), 'w', encoding='utf-8', errors='backslashreplace') as f:  # a lone surrogate
        f.write(json.dumps(value, indent=2, ensure_ascii=False) + '\n')  # (messy fuzz) is written as its JSON escape


# ---------------------------------------------------------------------------- bypass
# A proof covers the proved function only. Code elsewhere that decides the same question itself (reads the same
# field, repeats the same comparison) goes AROUND the rule. `bypass` searches the project for the check's
# "watchedFields" outside the rule's own files and reports every place the person has not reviewed yet.
# LIMIT: a text search per line, not a data-flow analysis. It misses aliases (`const f = body.for`), dynamic
# access (`body[key]`), terms split over lines, and code that decides the same question under other names.

LAST_BYPASS = DIR + '/last-bypass.json'
BYPASS_SKIP_DIRS = {'.git', 'node_modules', 'dist', 'build', 'verified', DIR, 'vendor', 'third_party',
                    'third-party', '.venv', 'venv', '__pycache__', 'target', '.next', 'coverage', 'bower_components'}
# Documentation does not run, and lockfiles are generated: neither can go around a rule.
BYPASS_SKIP_EXT = ('.md', '.markdown', '.rst', '.txt', '.adoc', '.lock', '.map', '.svg', '.min.js')
BYPASS_SKIP_NAMES = {'package-lock.json', 'yarn.lock', 'pnpm-lock.yaml', 'Cargo.lock', 'go.sum', 'poetry.lock'}
BYPASS_MAX_BYTES = 1 << 20
_BRACE_LANGS = ('.ts', '.tsx', '.mts', '.cts', '.js', '.jsx', '.mjs', '.cjs', '.java', '.cs', '.kt', '.scala',
                '.swift', '.php', '.c', '.cc', '.cpp', '.h', '.hpp', '.dart')
_DEF_PATTERNS = [
    re.compile(r'^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)'),                                        # Python
    re.compile(r'\bfunction\s*\*?\s*([A-Za-z_$][\w$]*)\s*[(<]'),                                 # JS/TS
    re.compile(r'^\s*(?:export\s+)?(?:(?:const|let|var)\s+|(?:(?:public|private|protected|static|readonly)\s+)*)'
               r'([A-Za-z_$][\w$]*)\s*(?::[^=]+)?=\s*(?:async\s*)?(?:function\b|(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*'
               r'(?::[^=]+)?=>)'),                                                              # JS/TS arrow
    re.compile(r'^\s*([A-Za-z_$][\w$]*)\s*:\s*(?:async\s*)?(?:function\b|(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>)'),
    re.compile(r'^\s*func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)'),                                   # Go
    re.compile(r'^\s*(?:pub(?:\([^)]*\))?\s+)?(?:(?:async|unsafe|const|extern)\s+)*fn\s+([A-Za-z_]\w*)'),  # Rust
]
_NOT_NAMES = {'if', 'for', 'while', 'switch', 'catch', 'return', 'new', 'else', 'do', 'try', 'using', 'lock',
              'foreach', 'await', 'typeof', 'sizeof', 'throw', 'yield', 'function', 'public', 'private',
              'protected', 'internal', 'static', 'final', 'abstract', 'async', 'override', 'virtual', 'sealed',
              'synchronized', 'readonly', 'export', 'default', 'super', 'this', 'when', 'match', 'fixed'}


def _indent(line):
    return len(line) - len(line.lstrip(' \t'))


def _method_name(line, next_line):
    """A method or function header in a brace language (Java, C#, a TS/JS class or object). A cheap heuristic."""
    s = line.strip()
    if not s or s.endswith(';') or s.startswith(('//', '*', '/*', '#', '@', '}', '=')):
        return None
    if not (s.endswith('{') or (s.endswith(')') or re.search(r'\)\s*:\s*[^=;{]+$', s)) and next_line.startswith('{')):
        return None
    if re.match(r'(?:if|for|while|switch|catch|return|else|new|await|throw|yield|do|try|using|lock|foreach)\b', s):
        return None
    for m in re.finditer(r'([A-Za-z_$][\w$]*)\s*(?:<[^()]*>)?\s*\(', s):
        if m.group(1) in _NOT_NAMES:
            continue
        if '=' in s[:m.start()].replace('=>', '').replace('==', ''):
            return None  # an assignment from a call, not a definition
        return m.group(1)
    return None


def enclosing_function(lines, idx, path):
    """Name of the function that holds lines[idx], or None: the nearest less-indented definition line above it."""
    brace = path.endswith(_BRACE_LANGS)

    def name_of(j):
        for p in _DEF_PATTERNS:
            m = p.search(lines[j])
            if m and m.group(1) not in _NOT_NAMES:
                return m.group(1)
        if brace:
            nxt = next((lines[k].strip() for k in range(j + 1, min(j + 4, len(lines))) if lines[k].strip()), '')
            return _method_name(lines[j], nxt)
        return None

    own = name_of(idx)
    if own:
        return own
    threshold = _indent(lines[idx])
    for j in range(idx - 1, -1, -1):
        text = lines[j]
        if not text.strip() or re.match(r'^\s*[{}()\[\];,]*\s*$', text) or text.strip().startswith(('//', '#', '*', '/*')):
            continue
        ind = _indent(text)
        if ind >= threshold:
            continue
        name = name_of(j)
        if name:
            return name
        threshold = ind
        if ind == 0:
            return None
    return None


def _skipped_path(rel):
    parts = rel.replace(os.sep, '/').split('/')
    name = parts[-1]
    return (any(p in BYPASS_SKIP_DIRS for p in parts[:-1]) or name in BYPASS_SKIP_NAMES
            or name.lower().endswith(BYPASS_SKIP_EXT))


def project_files(root):
    """Text files of the project: git's tracked and untracked-but-not-ignored files, else a walk."""
    try:
        r = subprocess.run(['git', '-C', root, 'ls-files', '-z', '--cached', '--others', '--exclude-standard'],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except OSError:  # no git on this machine (minimal containers): walk the folder instead
        r = None
    if r is not None and r.returncode == 0:
        rels = sorted(set(p for p in r.stdout.decode('utf-8', 'replace').split('\0') if p))
    else:
        rels = []
        for folder, dirs, files in os.walk(root):
            dirs[:] = sorted(d for d in dirs if d not in BYPASS_SKIP_DIRS)
            for name in sorted(files):
                rels.append(os.path.relpath(os.path.join(folder, name), root).replace(os.sep, '/'))
    out = []
    for rel in rels:
        path = os.path.join(root, rel)
        if _skipped_path(rel) or not os.path.isfile(path) or os.path.getsize(path) > BYPASS_MAX_BYTES:
            continue
        with open(path, 'rb') as f:
            if b'\0' in f.read(8192):
                continue  # binary
        out.append(rel)
    return out


def scope_matchers(c):
    terms = c.get('watchedFields') or []
    if not isinstance(terms, list) or not all(isinstance(t, str) and t.strip() for t in terms):
        raise ConfigError('check %s: "watchedFields" must be a list of non-empty strings (a /regex/ between '
                          'slashes, else literal text)' % c['name'])
    out = []
    for t in terms:
        try:
            is_re = len(t) > 2 and t.startswith('/') and t.endswith('/')
            out.append((t, re.compile(t[1:-1] if is_re else re.escape(t))))
        except re.error as e:
            raise ConfigError('check %s: watched field %r is not a valid regular expression: %s' % (c['name'], t, e))
    for e in c.get('allowedBypasses') or []:
        if not (isinstance(e, dict) and isinstance(e.get('file'), str)
                and re.match(r'^[0-9a-f]{64}$', str(e.get('line_text_sha256', '')))):
            raise ConfigError('check %s: each "allowedBypasses" entry needs "file" and "line_text_sha256" (64 hex '
                              'digits)' % c['name'])
    return out


def rule_own_files(root, cfg, c):
    """Project-relative files (and folders, ending in /) that belong to the rule itself: never reported."""
    files = set(contract_files(c)) | set(c['files'])
    bd = c.get('build') or {}
    if bd.get('fuzz'):
        files.add(bd['fuzz'])
    dfy = [f for f in files if f.endswith('.dfy') and os.path.isfile(inside(root, f))]
    try:
        files |= set(include_closure(root, dfy))
    except ConfigError:
        pass
    for d in diff_entries(c):
        files |= {d[k] for k in ('inputs', 'adapter') if d.get(k)}
    files.add(os.path.relpath(invariants_path(root, cfg), os.path.realpath(root)))
    norm = {os.path.normpath(f).replace(os.sep, '/') for f in files}
    if bd.get('out'):
        norm.add(os.path.normpath(bd['out']).replace(os.sep, '/') + '/')
    return norm


def line_sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def run_bypass(root, override=None):
    """override: {check name: terms} to search with instead of its watchedFields (a preview; nothing is written)."""
    cfg = load_config(root)
    for c in cfg['checks']:
        if override and c['name'] in override:
            c['watchedFields'] = list(override[c['name']])
    approved = read_json(os.path.join(root, APPROVED), {}) or {}
    out = {'at': datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds'), 'checks': {},
           'no_terms': [], 'config_approved': approved.get(CONFIG) == sha256(os.path.join(root, CONFIG))}
    with_terms = [c for c in cfg['checks'] if c.get('watchedFields')]
    out['no_terms'] = [c['name'] for c in cfg['checks'] if not c.get('watchedFields')]
    files = project_files(root) if with_terms else []
    cache = {}
    for c in with_terms:
        matchers = scope_matchers(c)
        own = rule_own_files(root, cfg, c)
        watched = {w for d in diff_entries(c) for w in d.get('watch') or []}
        reviewed = c.get('allowedBypasses') or []
        res = out['checks'][c['name']] = {'terms': [t for t, _ in matchers], 'hits': [], 'reviewed': 0,
                                          'outdated_reviews': [], 'files_searched': 0}
        used = set()
        for rel in files:
            if rel in own or any(o.endswith('/') and rel.startswith(o) for o in own):
                continue
            res['files_searched'] += 1
            if rel not in cache:
                cache[rel] = read_text(os.path.join(root, rel)).replace('\r\n', '\n').split('\n')
            lines = cache[rel]
            for i, text in enumerate(lines):
                terms = [t for t, rx in matchers if rx.search(text)]
                if not terms:
                    continue
                h = line_sha(text)
                match = [k for k, e in enumerate(reviewed) if e['file'] == rel and e['line_text_sha256'] == h]
                if match:
                    used.update(match)
                    res['reviewed'] += 1
                    continue
                res['hits'].append({'file': rel, 'line': i + 1, 'terms': terms, 'text': text.strip()[:200],
                                    'function': enclosing_function(lines, i, rel), 'line_text_sha256': h,
                                    'fuzzed_file': rel in watched})
        res['outdated_reviews'] = [e for k, e in enumerate(reviewed) if k not in used]
    k = sum(len(r['hits']) for r in out['checks'].values())
    out['unreviewed'] = k
    out['verdict'] = 'NO_SCOPE_TERMS' if not with_terms else 'BYPASS_FOUND' if k else 'BYPASS_CLEAR'
    return out


def outside_of(root):
    """For `check`: the unreviewed bypass hits per check (never changes a verdict). {} when nothing is watched."""
    try:
        out = run_bypass(root)
    except Exception as e:  # the search must never crash `check`; checks with watchedFields fail closed (main)
        return {}, '%s: %s' % (type(e).__name__, e)
    return ({name: [{'file': h['file'], 'line': h['line'], 'function': h['function'], 'term': h['terms'][0],
                     'text': h['text']} for h in r['hits']] for name, r in out['checks'].items()}, None)


def outside_lines(outside):
    lines = []
    for name, hits in outside.items():
        if hits:
            terms = sorted({h['term'] for h in hits})
            lines.append('%d place%s decide%s with %s outside %s: %s' % (
                len(hits), '' if len(hits) == 1 else 's', 's' if len(hits) == 1 else '', ', '.join(terms), name,
                ', '.join('%s:%d%s' % (h['file'], h['line'], ' ' + h['function'] if h['function'] else '') for h in hits[:12])
                + (', … and %d more (forge_check.py bypass lists all)' % (len(hits) - 12) if len(hits) > 12 else '')))
    if lines:
        lines.append('(not reviewed yet; /forge:bypass explains each one. A proof covers only the proved rule.)')
    return lines


def bypass_counts(out):
    """--count: unreviewed lines per term, to judge a term set before saving it."""
    lines = []
    for name, r in out['checks'].items():
        lines.append('%s: %d unreviewed line(s) in %d file(s) (%d file(s) searched; %d allowed)' % (
            name, len(r['hits']), len({h['file'] for h in r['hits']}), r['files_searched'], r['reviewed']))
        for t in r['terms']:
            hs = [h for h in r['hits'] if t in h['terms']]
            lines.append('  %5d  %s  (%d file(s))' % (len(hs), t, len({h['file'] for h in hs})))
    return '\n'.join(lines) or 'no check has "watchedFields" (pass --rule NAME --set-terms … --count to preview)'


def gate_bypass(result, outside, error):
    """The hard gate: a check whose proof passed, but with code outside the rule that uses its watchedFields and is
    not in its approved allowedBypasses, is UNAPPROVED_BYPASS. A proof failure (or any other verdict) still wins."""
    for name, verdict in list(result['verdicts'].items()):
        if verdict != 'VERIFIED':
            continue
        hits = outside.get(name) or []
        if hits:
            result['verdicts'][name] = 'UNAPPROVED_BYPASS'
            result['detail'][name] = ('the proof passed, but %d place(s) outside the rule use its watchedFields and are '
                                      'not in its approved allowedBypasses:\n%s\nRoute each through the proved rule, '
                                      'or allow it (/forge:bypass) and approve forge.json again.' % (
                                          len(hits), '\n'.join('  %s:%d%s  [%s]  %s' % (
                                              h['file'], h['line'], '  in ' + h['function'] if h['function'] else '',
                                              h['term'], h['text']) for h in hits[:40])
                                          + ('\n  … and %d more (forge_check.py bypass)' % (len(hits) - 40) if len(hits) > 40 else '')))


def bypass_report(out):
    if out['verdict'] == 'NO_SCOPE_TERMS':
        return ('forge bypass: NO_SCOPE_TERMS: no check has "watchedFields", so nothing was searched. /forge:bypass '
                'proposes them from the approved rule.')
    k = out['unreviewed']
    lines = ['forge bypass %s %s: %d place%s outside the rule not reviewed yet' % (
        '✗' if k else '✓', out['verdict'], k, '' if k == 1 else 's')]
    for name, r in out['checks'].items():
        lines.append('  %s: %d file(s) searched for %s; %d hit(s), %d allowed (allowedBypasses)' % (
            name, r['files_searched'], ', '.join(r['terms']), len(r['hits']) + r['reviewed'], r['reviewed']))
        for h in r['hits']:
            lines.append('    %s:%d  %s[%s]%s  %s' % (h['file'], h['line'], 'in %s  ' % h['function'] if h['function'] else '',
                                                    ', '.join(h['terms']), '  (a fuzzed file)' if h['fuzzed_file'] else '',
                                                    h['text']))
        for e in r['outdated_reviews']:
            lines.append('    outdated allowed bypass (the line changed or is gone): %s: %s' % (e['file'], e.get('note', '')))
    if out['no_terms']:
        lines.append('  no "watchedFields" yet (not searched): ' + ', '.join(out['no_terms']))
    if not out['config_approved']:
        lines.append('  NOTE: %s is not approved as it is now, so these terms and reviews are not approved either '
                     '(/forge approve).' % CONFIG)
    if k:
        lines.append('Each place may decide the same question as the proved rule without going through it. '
                     '/forge:bypass explains each one and records your answer.')
    lines.append('A text search (FINITE TEST of the listed terms): it misses aliases, dynamic field access and '
                 'other names for the same question.')
    return '\n'.join(lines)


def _bypass_edit(root, rule, set_terms, record, note):
    """Writes watchedFields or a allowedBypasses entry into forge.json (the person approves it again after)."""
    cfg = load_config(root)
    c = next((x for x in cfg['checks'] if x['name'] == rule), None)
    if c is None:
        raise ConfigError('no check named %s' % rule)
    if set_terms is not None:
        c['watchedFields'] = list(set_terms)
        scope_matchers(c)
        what = 'watchedFields of %s: %s' % (rule, ', '.join(set_terms))
    else:
        m = re.match(r'^(.+):(\d+)$', record or '')
        if not m or not (note or '').strip():
            raise ConfigError('--record needs FILE:LINE and --note "why this place is fine"')
        rel, n = os.path.normpath(m.group(1)).replace(os.sep, '/'), int(m.group(2))
        hit = next((h for h in run_bypass(root)['checks'].get(rule, {}).get('hits', [])
                    if h['file'] == rel and h['line'] == n), None)
        if hit is None:
            raise ConfigError('%s:%d is not an unreviewed bypass hit of %s (run bypass first)' % (rel, n, rule))
        c.setdefault('allowedBypasses', []).append({'file': rel, 'line_text_sha256': hit['line_text_sha256'],
                                                   'note': note.strip()})
        what = 'reviewed %s:%d for %s' % (rel, n, rule)
    with open(os.path.join(root, CONFIG), 'w', encoding='utf-8') as f:
        f.write(json.dumps(cfg, indent=2, ensure_ascii=False) + '\n')
    return 'wrote %s (%s). %s changed: the person approves it again (/forge approve, then /forge approve yes).' % (
        CONFIG, what, CONFIG)


def main(argv=None):
    # Dafny's bundled .NET runtime stops on Linux systems without the ICU library (minimal images, many servers):
    # "Couldn't find a valid ICU package". Dafny does not need culture data, so run it without (found in a clean
    # python:3.12-slim container).
    os.environ.setdefault('DOTNET_SYSTEM_GLOBALIZATION_INVARIANT', '1')
    p = argparse.ArgumentParser(prog='forge_check.py', description='Forge proof check')
    p.add_argument('command', choices=['check', 'approve', 'spec', 'build', 'setup', 'mutate', 'diff', 'invariants',
                                       'bypass'])
    p.add_argument('--root', default='.', help='project folder (default: current folder)')
    p.add_argument('--json', action='store_true', help='print the result as JSON')
    p.add_argument('--base', help='git ref; any change to forge.json, approved.json or a spec '
                                  'against it gives APPROVAL_CHANGED (for pull requests)')
    p.add_argument('--no-write', action='store_true', help='do not write ' + LAST)
    p.add_argument('--hashes', help='approve: the hashes that `spec --json` printed; refused if any '
                                    'file changed since')
    p.add_argument('--max', type=int, default=40, help='mutate: at most this many mutants')
    p.add_argument('--inputs', type=int, help='diff: at most this many random inputs (default from forge.json, 1500)')
    p.add_argument('--budget-ms', type=int, help='diff: time budget per check in ms (default 1500)')
    p.add_argument('--seed', type=int, help='diff: random seed (default: a fresh one each run)')
    p.add_argument('--full', action='store_true', help='spec/approve: print every approved file in full')
    p.add_argument('--check', action='store_true', help='build: write nothing; the emitted code on '
                                                        'disk must equal a fresh build')
    p.add_argument('--rule', help='bypass: the check whose watchedFields or allowedBypasses to write')
    p.add_argument('--set-terms', nargs='+', metavar='TERM', help='bypass --rule NAME: write its watchedFields')
    p.add_argument('--record', metavar='FILE:LINE', help='bypass --rule NAME: record a hit as reviewed (with --note)')
    p.add_argument('--count', action='store_true', help='bypass: only count unreviewed lines per term (with --rule '
                                                        'NAME --set-terms …: a preview; nothing is written)')
    p.add_argument('--note', help='bypass --record: why that place is fine, in the person\'s words')
    a = p.parse_args(argv)
    root = os.path.abspath(a.root)
    try:
        if a.command == 'bypass':
            if a.count:
                if a.set_terms is not None and not a.rule:
                    raise ConfigError('--set-terms needs --rule NAME')
                out = run_bypass(root, {a.rule: a.set_terms} if a.set_terms is not None else None)
                print(json.dumps({n: {'unreviewed': len(r['hits']), 'per_term': {t: sum(t in h['terms'] for h in r['hits'])
                                  for t in r['terms']}} for n, r in out['checks'].items()}) if a.json else bypass_counts(out))
                return 0
            if a.set_terms is not None or a.record:
                if not a.rule:
                    raise ConfigError('--set-terms and --record need --rule NAME')
                print(_bypass_edit(root, a.rule, a.set_terms, a.record, a.note))
                return 0
            out = run_bypass(root)
            if not a.no_write:
                write_json(root, LAST_BYPASS, out)
            print(json.dumps(out, indent=2, ensure_ascii=False) if a.json else bypass_report(out))
            return 1 if out['verdict'] == 'BYPASS_FOUND' else 0
        if a.command == 'invariants':
            text = invariants_text(root)
            path = invariants_path(root, load_config(root))
            if a.check:
                current = read_text(path) if os.path.isfile(path) else None
                if current != text:
                    print('INVARIANTS_STALE: %s differs from a fresh generation; run invariants and commit it'
                          % os.path.relpath(path, root))
                    return 1
                print('INVARIANTS_OK: %s is up to date' % os.path.relpath(path, root))
                return 0
            with open(path, 'w', encoding='utf-8') as f:
                f.write(text)
            print('wrote %s (%d rules)' % (os.path.relpath(path, root), len(load_config(root)['checks'])))
            return 0
        if a.command == 'diff':
            if hasattr(sys.stdout, 'reconfigure'):
                sys.stdout.reconfigure(errors='backslashreplace')  # messy inputs may hold a lone surrogate
            out = run_diff(root, a.inputs, a.budget_ms, a.seed)
            if not a.no_write:
                update_diff_stats(root, out)
                write_json(root, LAST_DIFF, out)
            print(json.dumps(out, indent=2, ensure_ascii=False) if a.json else diff_report(out))
            return 0 if all(r['verdict'] == 'DIFF_OK' for r in out['checks'].values()) else 1
        if a.command == 'mutate':
            out = run_mutate(root, a.max)
            print(json.dumps(out, indent=2) if a.json else mutate_report(out))
            return 0
        if a.command == 'setup':
            cfg = read_json(os.path.join(root, CONFIG), {}) or {}
            path = find_dafny(root, cfg)
            r = subprocess.run([path, '--version'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               universal_newlines=True) if path else None
            version = r.stdout.strip().split('\n')[-1] if r and r.returncode == 0 else None
            if a.json:
                print(json.dumps({'dafny': path, 'version': version, 'pinned': DAFNY_VERSION}))
            else:
                print('dafny: %s\nversion: %s (pinned download: %s)' % (path, version, DAFNY_VERSION))
            return 0 if version else 1
        if a.command == 'spec':
            text, hashes = show_specs(root, a.full)
            print(json.dumps({'text': text, 'hashes': hashes}, ensure_ascii=False) if a.json else text)
            return 0
        if a.command == 'approve' and a.hashes:
            try:
                shown = json.loads(a.hashes)
            except ValueError:
                print('--hashes is not JSON', file=sys.stderr)
                return 2
            current = show_specs(root)[1]
            if shown != current:
                differ = sorted(k for k in set(shown) | set(current) if shown.get(k) != current.get(k))
                print('changed since it was shown: %s. Read it again.' % ', '.join(differ))
                return 1
            write_json(root, APPROVED, current)
            record_described(root)
            print('\n'.join('approved %s  sha256 %s…' % (k, h[:12]) for k, h in current.items()))
            return 0
        if a.command == 'approve':
            if not (sys.stdin.isatty() and sys.stdout.isatty()):
                print('approve needs a person at a terminal.', file=sys.stderr)
                return 2
            text, hashes = show_specs(root, a.full)
            print(text)
            if input('\nApprove exactly this? Type yes: ').strip() != 'yes':
                print('Not approved.')
                return 1
            for key, h in hashes.items():
                if (sha256 if key == CONFIG else contract_hash)(os.path.join(root, key)) != h:
                    print('%s changed while you read it. Run approve again.' % key)
                    return 1
            write_json(root, APPROVED, hashes)
            record_described(root)
            print('approved: ' + ', '.join(hashes))
            return 0
        if a.command == 'build':
            result = run_build(root, a.check)
            print(json.dumps(result, indent=2, ensure_ascii=False) if a.json else build_report(result))
            ok = all(v == 'VERIFIED' for v in result['verdicts'].values()) and \
                all(b['verdict'] == 'BUILT' for b in result['build'].values())
            return 0 if ok else 1
        result = run_checks(root, a.base)
        result['outside'], outside_error = outside_of(root)
        if outside_error:
            result['outside_error'] = outside_error
            for c in load_config(root)['checks']:
                if c.get('watchedFields') and result['verdicts'].get(c['name']) == 'VERIFIED':
                    result['verdicts'][c['name']] = 'ERROR'
                    result['detail'][c['name']] = 'the bypass search did not run: ' + outside_error
        gate_bypass(result, result['outside'], outside_error)
        if not a.no_write:
            write_json(root, LAST, result)
        text = report(result) + ''.join('\n' + l for l in outside_lines(result['outside']))
        print(json.dumps(result, indent=2, ensure_ascii=False) if a.json else text)
        return 0 if all(v == 'VERIFIED' for v in result['verdicts'].values()) else 1
    except ConfigError as e:
        if a.json:
            print(json.dumps({'error': str(e)}))
        else:
            print('forge: ' + str(e), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
