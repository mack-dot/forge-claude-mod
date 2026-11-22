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


def _java_files(path):
    if os.path.isfile(path):
        return [path] if path.endswith('.java') else []
    out = []
    for d, _, files in os.walk(path):
        out += [os.path.join(d, f) for f in files if f.endswith('.java')]
    return sorted(out)


def build_java(root, emitted_dir, adapter, work, sources=None):
    """emitted_dir: <out>/<check>/java/<name>-java. `sources`: files or folders (relative to the project) with the
    Java code under test, compiled together with the evaluator. Java has no incremental build tool here, so the
    evaluator is recompiled only when the content of any of these files changes (a SHA-256 over all of them)."""
    import hashlib
    if not (shutil.which('javac') and shutil.which('java')):
        raise BuildError('javac and java are not on PATH (a JDK is needed)')
    if not os.path.isdir(emitted_dir):
        raise BuildError('no Java build output at %s: add "java" to the check\'s build targets and run build' % emitted_dir)
    template = os.path.join(HERE, 'eval', 'java', 'ForgeMain.java')
    files = _java_files(emitted_dir) + [template]
    for s in sources or []:
        p = os.path.join(root, s)
        if not os.path.exists(p):
            raise BuildError('java_sources: %s does not exist' % s)
        files += _java_files(p)
    h = hashlib.sha256()
    for f in files + [adapter]:
        h.update(f.encode())
        with open(f, 'rb') as fh:
            h.update(fh.read())
    stamp = os.path.join(work, 'classes.sha256')
    classes = os.path.join(work, 'classes')
    if not (os.path.exists(stamp) and open(stamp).read() == h.hexdigest() and os.path.isdir(classes)):
        if os.path.isdir(classes):
            shutil.rmtree(classes)
        os.makedirs(classes)
        # The adapter's public class is ForgeAdapter, whatever its file is called.
        _write_if_changed(os.path.join(work, 'src', 'ForgeAdapter.java'), open(adapter, encoding='utf-8').read())
        argfile = os.path.join(work, 'sources.txt')
        with open(argfile, 'w', encoding='utf-8') as f:
            f.write('\n'.join('"%s"' % x.replace('\\', '/') for x in files + [os.path.join(work, 'src', 'ForgeAdapter.java')]))
        _run(['javac', '-nowarn', '-encoding', 'UTF-8', '-d', classes, '@' + argfile], work, dict(os.environ),
             'javac of the evaluator')
        with open(stamp, 'w') as f:
            f.write(h.hexdigest())
    return ['java', '-cp', classes, 'ForgeMain']


def build_cs(root, emitted_dir, adapter, work, sources=None):
    """emitted_dir: <out>/<check>/cs (one .cs file with the Dafny runtime). `sources`: files or folders with the C#
    code under test. Rebuilt only when the content of any of these files changes (a SHA-256 over all of them)."""
    import hashlib
    if not shutil.which('dotnet'):
        raise BuildError('dotnet is not on PATH (the .NET SDK is needed)')
    emitted = [os.path.join(emitted_dir, f) for f in sorted(os.listdir(emitted_dir)) if f.endswith('.cs')] \
        if os.path.isdir(emitted_dir) else []
    if not emitted:
        raise BuildError('no C# build output at %s: add "cs" to the check\'s build targets and run build' % emitted_dir)
    files = emitted + [os.path.join(HERE, 'eval', 'cs', 'ForgeMain.cs'), adapter]
    for s in sources or []:
        p = os.path.join(root, s)
        if not os.path.exists(p):
            raise BuildError('cs_sources: %s does not exist' % s)
        files += [p] if os.path.isfile(p) else sorted(os.path.join(d, f) for d, _, fs in os.walk(p) for f in fs if f.endswith('.cs'))
    h = hashlib.sha256()
    for f in files:
        h.update(f.encode())
        with open(f, 'rb') as fh:
            h.update(fh.read())
    stamp, out = os.path.join(work, 'bin.sha256'), os.path.join(work, 'bin')
    dll = os.path.join(out, 'forgeeval.dll')
    if not (os.path.exists(stamp) and open(stamp).read() == h.hexdigest() and os.path.exists(dll)):
        major = subprocess.run(['dotnet', '--version'], stdout=subprocess.PIPE, universal_newlines=True).stdout.strip().split('.')[0]
        includes = ''.join('    <Compile Include="%s" />\n' % f.replace('\\', '/') for f in files)
        _write_if_changed(os.path.join(work, 'forgeeval.csproj'), '''<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <OutputType>Exe</OutputType>
    <TargetFramework>net%s.0</TargetFramework>
    <EnableDefaultCompileItems>false</EnableDefaultCompileItems>
    <Nullable>disable</Nullable>
    <TreatWarningsAsErrors>false</TreatWarningsAsErrors>
    <NoWarn>$(NoWarn);CS0162;CS0164;CS0168;CS0219;CS0414;CS1998;CS8981</NoWarn>
  </PropertyGroup>
  <ItemGroup>
%s  </ItemGroup>
</Project>
''' % (major, includes))
        _run(['dotnet', 'build', '-c', 'Release', '-o', out, '--nologo', '-v', 'q'], work,
             dict(os.environ, DOTNET_CLI_TELEMETRY_OPTOUT='1', DOTNET_NOLOGO='1'), 'dotnet build of the evaluator')
        with open(stamp, 'w') as f:
            f.write(h.hexdigest())
    return ['dotnet', dll]
