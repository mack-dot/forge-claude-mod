"""Builds the diff evaluator for compiled languages (Go, Rust) in .forge/cache/, around the person's adapter.

Forge writes the protocol `main` (cli/eval/<lang>/forge_main.*), links the compiled proved code from the check's
build output and the code under test, and compiles. It relies on each toolchain's own incremental build cache
(go build, cargo), so a rebuild after no change is quick and a stale evaluator is not possible.
Nothing in the project outside .forge/cache/ is written. Test tooling: NOT proved.
"""
import os
import re
import shutil
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))


class BuildError(Exception):
    pass


def _write_if_changed(path, text):
    old = None
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            old = f.read()
    if old != text:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(text)


def _run(cmd, cwd, env, what):
    r = subprocess.run(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       stdin=subprocess.DEVNULL, universal_newlines=True, timeout=600)
    if r.returncode != 0:
        raise BuildError('%s failed (exit %d):\n%s' % (what, r.returncode, r.stdout[-1500:]))


def _find_up(start, root, name):
    d = os.path.abspath(start)
    root = os.path.abspath(root)
    while True:
        if os.path.isfile(os.path.join(d, name)):
            return d
        if d == root or os.path.dirname(d) == d:
            return None
        d = os.path.dirname(d)


def build_go(root, emitted_dir, adapter, work):
    """emitted_dir: <out>/<check>/go/<name>-go (Dafny's GOPATH-style output). Returns the evaluator's argv."""
    if not shutil.which('go'):
        raise BuildError('go is not on PATH')
    src = os.path.join(emitted_dir, 'src')
    if not os.path.isdir(src):
        raise BuildError('no Go build output at %s: add "go" to the check\'s build targets and run build' % src)
    pkgs = sorted(d for d in os.listdir(src) if os.path.isdir(os.path.join(src, d)))
    # Dafny's packages have no go.mod: give each a module of its own name, in the cache (never in verified/).
    for p in pkgs:
        dest = os.path.join(work, 'pkgs', p)
        if os.path.isdir(dest):
            shutil.rmtree(dest)
        shutil.copytree(os.path.join(src, p), dest)
        others = ''.join('\t%s v0.0.0\n' % o for o in pkgs if o != p)
        _write_if_changed(os.path.join(dest, 'go.mod'),
                          'module %s\n\ngo 1.21\n%s' % (p, 'require (\n%s)\n' % others if others else ''))
    requires = ['%s v0.0.0' % p for p in pkgs]
    replaces = ['%s => ../pkgs/%s' % (p, p) for p in pkgs]
    # The person's own module, if the adapter imports it.
    mod_dir = _find_up(os.path.dirname(adapter), root, 'go.mod')
    if mod_dir:
        with open(os.path.join(mod_dir, 'go.mod'), encoding='utf-8') as f:
            m = re.search(r'^module\s+(\S+)', f.read(), re.M)
        if m:
            requires.append('%s v0.0.0' % m.group(1))
            replaces.append('%s => %s' % (m.group(1), mod_dir))
    main = os.path.join(work, 'main')
    _write_if_changed(os.path.join(main, 'go.mod'), 'module forgeeval\n\ngo 1.21\n\nrequire (\n%s)\n\nreplace (\n%s)\n' % (
        ''.join('\t%s\n' % r for r in requires), ''.join('\t%s\n' % r for r in replaces)))
    with open(os.path.join(HERE, 'eval', 'go', 'forge_main.go'), encoding='utf-8') as f:
        _write_if_changed(os.path.join(main, 'forge_main.go'), '//go:build forge\n\n' + f.read())
    with open(adapter, encoding='utf-8') as f:
        _write_if_changed(os.path.join(main, 'adapter.go'), f.read())
    env = dict(os.environ, GOWORK='off', GOFLAGS='-mod=mod', GO111MODULE='on')
    exe = os.path.join(work, 'evaluator')
    _run(['go', 'build', '-tags', 'forge', '-o', exe, '.'], main, env, 'go build of the evaluator')
    return [exe]


def build_rust(root, emitted_dir, adapter, work, deps=None):
    """emitted_dir: <out>/<check>/rs/<name>-rust (src/<name>.rs and runtime/). Returns the evaluator's argv."""
    if not shutil.which('cargo'):
        raise BuildError('cargo is not on PATH')
    srcs = [f for f in os.listdir(os.path.join(emitted_dir, 'src')) if f.endswith('.rs')] \
        if os.path.isdir(os.path.join(emitted_dir, 'src')) else []
    runtime = os.path.join(emitted_dir, 'runtime')
    if len(srcs) != 1 or not os.path.isdir(runtime):
        raise BuildError('no Rust build output at %s: add "rs" to the check\'s build targets and run build' % emitted_dir)
    extra = ''.join('%s = %s\n' % (k, v) for k, v in sorted((deps or {}).items()))
    _write_if_changed(os.path.join(work, 'Cargo.toml'), '''[package]
name = "forge_eval"
version = "0.0.0"
edition = "2021"

[dependencies]
dafny_runtime = { path = "%s" }
serde_json = { version = "1", features = ["arbitrary_precision"] }
%s
[workspace]
''' % (runtime.replace('\\', '/'), extra))
    with open(os.path.join(HERE, 'eval', 'rust', 'forge_main.rs'), encoding='utf-8') as f:
        main = f.read()
    # The proved code and the adapter are used where they are, so the adapter's own #[path] lines (for the code
    # under test) stay relative to the adapter's real folder.
    main = main.replace('#[path = "proved.rs"]', '#[path = "%s"]' % os.path.join(emitted_dir, 'src', srcs[0]).replace('\\', '/'))
    main = main.replace('#[path = "adapter.rs"]', '#[path = "%s"]' % adapter.replace('\\', '/'))
    _write_if_changed(os.path.join(work, 'src', 'main.rs'), main)
    # FORGE_CARGO_TARGET_DIR lets several projects (or a test suite) share compiled dependencies.
    env = dict(os.environ, CARGO_TARGET_DIR=os.environ.get('FORGE_CARGO_TARGET_DIR') or os.path.join(work, 'target'))
    _run(['cargo', 'build', '--quiet'], work, env, 'cargo build of the evaluator')
    return [os.path.join(env['CARGO_TARGET_DIR'], 'debug', 'forge_eval')]
