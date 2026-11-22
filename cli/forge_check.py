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
    return cfg


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
        changed = [f for f in contract_files(c) if approved[f] != sha256(inside(root, f))]
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
    else:
        code = run_to_file([dafny, 'run', '--no-verify', '--target:' + target] + extra + [harness_rel], work, log)
    text = read_text(log)
    if code != 0:
        return 'FAILED', 'exit %s\n%s' % ('timeout' if code is None else code, text[-600:])
    lines = [l for l in text.split('\n') if l.strip() and not l.startswith('Dafny program verifier')]
    return 'PASSED', '\n'.join(lines)


def emit_target(dafny, work, files, target, out_dir, name, log):
    extra = TARGETS[target][1]
    cmd = [dafny, 'translate', target, '--no-verify', '--include-runtime'] + extra + \
          ['--output', os.path.join(out_dir, name)] + files
    code = run_to_file(cmd, work, log)
    if code != 0:
        return 'exit %s\n%s' % (code, read_text(log)[-600:])
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


def build_one(root, cfg, c, dafny, check_only):
    bd = c['build']
    name = c['name'].replace('-', '_')
    out_root = inside(root, bd['out'])
    res = {'verdict': 'BUILT', 'targets': {}, 'notes': []}

    def fail(verdict, note):
        if res['verdict'] == 'BUILT':
            res['verdict'] = verdict
        res['notes'].append(note)

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
                              os.path.join(tmp, 'emit-%s.log' % t))
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
    return res


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
        for rel in contract_files(c):
            if rel in hashes:
                continue
            hashes[rel] = sha256(inside(root, rel))
            body = contract_of(inside(root, rel)) if rel == c['spec'] else read_text(inside(root, rel))
            kind = 'spec' if rel == c['spec'] else 'your answers' if rel == c.get('examples') else 'decision record'
            parts.append('=== %s %s: %s (%s, sha256 %s…) ===\n%s'
                         % (c['name'], kind, rel, state(rel), hashes[rel][:12], body))
    return '\n\n'.join(parts), hashes


def write_json(root, rel, value):
    os.makedirs(os.path.join(root, DIR), exist_ok=True)
    with open(os.path.join(root, rel), 'w', encoding='utf-8') as f:
        f.write(json.dumps(value, indent=2, ensure_ascii=False) + '\n')


def main(argv=None):
    p = argparse.ArgumentParser(prog='forge_check.py', description='Forge proof check')
    p.add_argument('command', choices=['check', 'approve', 'spec', 'build', 'setup', 'mutate'])
    p.add_argument('--root', default='.', help='project folder (default: current folder)')
    p.add_argument('--json', action='store_true', help='print the result as JSON')
    p.add_argument('--base', help='git ref; any change to forge.json, approved.json or a spec '
                                  'against it gives APPROVAL_CHANGED (for pull requests)')
    p.add_argument('--no-write', action='store_true', help='do not write ' + LAST)
    p.add_argument('--hashes', help='approve: the hashes that `spec --json` printed; refused if any '
                                    'file changed since')
    p.add_argument('--max', type=int, default=40, help='mutate: at most this many mutants')
    p.add_argument('--check', action='store_true', help='build: write nothing; the emitted code on '
                                                        'disk must equal a fresh build')
    a = p.parse_args(argv)
    root = os.path.abspath(a.root)
    try:
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
            text, hashes = show_specs(root)
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
            print('\n'.join('approved %s  sha256 %s…' % (k, h[:12]) for k, h in current.items()))
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
        if a.command == 'build':
            result = run_build(root, a.check)
            print(json.dumps(result, indent=2, ensure_ascii=False) if a.json else build_report(result))
            ok = all(v == 'VERIFIED' for v in result['verdicts'].values()) and \
                all(b['verdict'] == 'BUILT' for b in result['build'].values())
            return 0 if ok else 1
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
