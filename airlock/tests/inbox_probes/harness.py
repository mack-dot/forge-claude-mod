"""Shared harness of the in-box probes (red team M2.3): a throwaway repository with hidden
content in many places of its history, the list of needles (what must never appear in any
output of the box), and `forge up` driven with a KNOWN canary token (patched into the child
process only, so the harness can search the output for it). Standard library only."""
import hashlib
import os
import pty
import select
import signal
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.abspath(os.path.join(HERE, "..", ".."))
FIX = os.path.join(PKG, "proof", "fixtures")
UPSTREAM_KEY = "FAKE_inbox_upstream_key_DEMO_ONLY_not_real_0077"
IMAGE = "forge-airlock-inbox:" + (os.environ.get("FORGEAIR_RUN") or "local")  # per run: parallel runs retag
MSG_MARKER = "FORGE-INBOX-MSG-MARKER-commit-message-5e1a"
SIDE = ("// HIDDEN FIXTURE side branch (merged; not on the first-parent chain).\n"
        "// FORGE-INBOX-MARKER-side-branch-91c3d2aa\nexport const SIDE_SECRET_RATIO = 0.4417;\n")
NOTE_NFC = "notes/café.md"
NOTE = "# HIDDEN NOTE: FORGE-INBOX-MARKER-cafe-note-0b7e44d2\nThe hidden note body, never in the box.\n"

POLICY = """version: 1
upstreams:
  - name: fs
    transport: stdio
    command: [node, /opt/mcpfs/node_modules/@modelcontextprotocol/server-filesystem/dist/index.js, /ws]
    user: 1000
    env: {FS_TOKEN: "${FORGE_INBOX_UPSTREAM}"}
tools:
  allow:
    - {tool: read_text_file, upstream: fs, path_args: {path: read}}
    - {tool: write_file, upstream: fs, path_args: {path: write}}
  deny: [move_file]
paths:
  hidden_paths: ["src/proprietary/**", "**/algo.ts", ".env*", "notes/café.md"]
  readonly_paths: ["tests/**", "spec/**"]
"""


def _git(repo, *args, date="2026-11-01T10:00:00Z"):
    env = dict(os.environ, GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date, GIT_CONFIG_NOSYSTEM="1",
               GIT_CONFIG_GLOBAL="/dev/null")
    subprocess.run(["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                    "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false"] + list(args),
                   check=True, capture_output=True, env=env)


def _put(repo, rel, data, binary=False):
    p = os.path.join(repo, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb" if binary else "w", encoding=None if binary else "utf-8") as f:
        f.write(data)


def make_repo(tmp):
    """History: c1 public files + core v1 + .env (FAKE key), message with a marker;
    c2 core v2 + algo.ts + the hidden note; a side branch with side.ts merged with --no-ff
    (the side commit is not first-parent); a tag on the side commit; c3 deletes .env;
    forge.yaml committed; one uncommitted public change."""
    repo = os.path.join(tmp, "repo")
    os.makedirs(repo)
    subprocess.run(["git", "init", "-q", "-b", "main", repo], check=True)
    pub = os.path.join(FIX, "repo")
    for d, _, files in os.walk(pub):
        for f in files:
            rel = os.path.relpath(os.path.join(d, f), pub)
            if rel in ("src/lib/algo.ts", "src/proprietary/core.ts"):
                continue
            _put(repo, rel, open(os.path.join(d, f), "rb").read(), binary=True)
    _put(repo, "src/proprietary/core.ts", open(os.path.join(FIX, "history", "core_v1.ts"), "rb").read(), True)
    _put(repo, ".env", open(os.path.join(FIX, "history", "dot-env.fake"), "rb").read(), True)
    _put(repo, "forge.yaml", POLICY)
    _put(repo, ".gitignore", "node_modules/\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", f"initial import, core v1 ({MSG_MARKER})")
    _put(repo, "src/proprietary/core.ts", open(os.path.join(pub, "src/proprietary/core.ts"), "rb").read(), True)
    _put(repo, "src/lib/algo.ts", open(os.path.join(pub, "src/lib/algo.ts"), "rb").read(), True)
    _put(repo, NOTE_NFC, NOTE)
    _git(repo, "add", "-A", date="2026-11-02T10:00:00Z")
    _git(repo, "commit", "-q", "-m", "core v2, algo, note", date="2026-11-02T10:00:00Z")
    _git(repo, "checkout", "-q", "-b", "side")
    _put(repo, "src/proprietary/side.ts", SIDE)
    _git(repo, "add", "-A", date="2026-11-02T11:00:00Z")
    _git(repo, "commit", "-q", "-m", "side work", date="2026-11-02T11:00:00Z")
    _git(repo, "tag", "-a", "-m", "tag on the side branch", "side-tag", date="2026-11-02T11:00:00Z")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "-q", "--no-ff", "-m", "merge side", "side", date="2026-11-02T12:00:00Z")
    os.remove(os.path.join(repo, ".env"))
    _git(repo, "add", "-A", date="2026-11-03T10:00:00Z")
    _git(repo, "commit", "-q", "-m", "remove .env", date="2026-11-03T10:00:00Z")
    with open(os.path.join(repo, "README.md"), "a") as f:
        f.write("uncommitted-change\n")
    return repo


def hidden_texts():
    """(label, content) of every hidden file version in the history."""
    pub = os.path.join(FIX, "repo")
    return [("src/proprietary/core.ts@v1", open(os.path.join(FIX, "history", "core_v1.ts"), "rb").read()),
            ("src/proprietary/core.ts@v2", open(os.path.join(pub, "src/proprietary/core.ts"), "rb").read()),
            ("src/lib/algo.ts", open(os.path.join(pub, "src/lib/algo.ts"), "rb").read()),
            (".env", open(os.path.join(FIX, "history", "dot-env.fake"), "rb").read()),
            ("src/proprietary/side.ts", SIDE.encode()),
            (NOTE_NFC, NOTE.encode())]


def needles(token):
    """(label, bytes): hidden lines (>= 12 chars, stripped), hidden names, the commit-message
    marker, the canary token, the upstream key. None of them may appear in any box output."""
    out = []
    for label, data in hidden_texts():
        for i, ln in enumerate(data.splitlines(), 1):
            s = ln.strip()
            if len(s) >= 12:
                out.append((f"hidden line {i} of {label}", s))
    # (src/lib/algo.ts is named in the public README of the fixture, so it is not a needle)
    for name in ("src/proprietary/core.ts", "src/proprietary/side.ts", "proprietary/core.ts",
                 "proprietary/side.ts", "side-tag"):
        out.append((f"hidden name {name}", name.encode()))
    # host paths: the home folder, the temp folder (the run folder lives there), the user name
    home = os.path.realpath(os.path.expanduser("~"))
    tmp = os.path.realpath(tempfile.gettempdir())
    for label, v in (("host home path", home), ("host temp path", tmp), ("host temp path", tempfile.gettempdir()),
                     ("host user name", os.environ.get("USER", ""))):
        if len(v) >= 5 and v not in ("/tmp", "/private/tmp", "root", "runner"):
            out.append((label, v.rstrip("/").encode()))
    # (the name notes/cafe.md itself is a hidden_paths pattern in forge.yaml, which the box may read)
    out.append(("source commit message marker", MSG_MARKER.encode()))
    out.append(("canary token", token.encode()))
    out.append(("upstream key", UPSTREAM_KEY.encode()))
    return out


def find_needles(output, token):
    """[(family, label, line_no)] for every needle in the output. The family is taken from the
    '[dump <family>]' prefix of the line; other lines count as 'other'. Values are not returned."""
    hits = []
    ns = needles(token)
    for i, raw in enumerate(output.split(b"\n"), 1):
        fam = "other"
        if raw.startswith(b"[dump "):
            fam = raw[6:raw.find(b"]")].decode(errors="replace")
        for label, n in ns:
            if n in raw:
                hits.append((fam, label, i))
    return hits


def repo_key(repo):
    return hashlib.sha256(os.path.realpath(repo).encode()).hexdigest()[:12]


CHILD = r"""
import os, secrets, sys
sys.path.insert(0, sys.argv[1])
_t = os.environ.pop("FORGE_INBOX_TOKEN")
secrets.token_hex = lambda n=None: _t   # test harness only: the canary token is known to the harness
import forge_airlock
sys.exit(forge_airlock.main(sys.argv[2:]))
"""


def forge_up(repo, command, token, pass_env=None, answer=None, timeout=None, keep=False):
    """Run `forge up --repo R [--pass-env N]... -- <command>`; returns (exit, output bytes).
    answer=None: stdin is not a terminal (nothing is synced back). answer='y'/'n': a
    pseudo-terminal answers the y/N prompt. A run past `timeout` is interrupted (exit 124)."""
    env = dict(os.environ, FORGE_INBOX_TOKEN=token, FORGE_INBOX_UPSTREAM=UPSTREAM_KEY, **(pass_env or {}))
    # an image tag of its own: a parallel `forge up` of another checkout retags forge-airlock:local
    env.setdefault("FORGE_AIRLOCK_IMAGE", IMAGE)
    argv = [sys.executable, "-c", CHILD, PKG, "up", "--repo", repo] + (["--keep"] if keep else [])
    for n in (pass_env or {}):
        argv += ["--pass-env", n]
    argv += ["--"] + list(command)
    t0 = time.time()
    timeout = timeout or int(os.environ.get("FORGE_INBOX_TIMEOUT", "420"))
    if answer is None:
        p = subprocess.Popen(argv, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, start_new_session=True)
        try:
            out, _ = p.communicate(timeout=timeout)
            return p.returncode, out
        except subprocess.TimeoutExpired:
            _interrupt(p.pid, repo)
            out, _ = p.communicate()
            return 124, out + b"\n[harness] TIMEOUT\n"
    pid, fd = pty.fork()
    if pid == 0:
        os.execve(sys.executable, argv, env)
    buf, sent, timed_out, stage = b"", False, False, 0
    while True:
        # past the timeout: SIGINT (forge up cleans up), 40 s later SIGKILL; keep reading the
        # terminal meanwhile, else the child blocks on a full terminal buffer while it exits
        late = time.time() - t0 - timeout
        if late > 0 and stage == 0:
            timed_out, stage = True, 1
            _kill(pid, signal.SIGINT)
        elif late > 40 and stage == 1:
            stage = 2
            _kill(pid, signal.SIGKILL)
        elif late > 60:
            break
        r, _, _ = select.select([fd], [], [], 1.0)
        if not r:
            continue
        try:
            chunk = os.read(fd, 65536)
        except OSError:
            break
        if not chunk:
            break
        buf += chunk
        if not sent and b"[y/N]" in buf:
            os.write(fd, (answer + "\n").encode())
            sent = True
    _, status = os.waitpid(pid, 0)
    os.close(fd)
    if timed_out:
        cleanup(repo)
    code = 124 if timed_out else os.waitstatus_to_exitcode(status)
    return code, buf.replace(b"\r\n", b"\n") + (b"\n[harness] TIMEOUT\n" if timed_out else b"")


def _kill(pid, sig):
    try:
        os.kill(pid, sig)
    except ProcessLookupError:
        pass


def _interrupt(pid, repo):
    for sig, wait in ((signal.SIGINT, 30), (signal.SIGKILL, 5)):
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            break
        t = time.time()
        while time.time() - t < wait:
            try:
                if os.waitpid(pid, os.WNOHANG)[0]:
                    break
            except ChildProcessError:
                break
            time.sleep(0.5)
    cleanup(repo)


def cleanup(repo, logs=False):
    key = repo_key(repo)
    ids = subprocess.run(["docker", "ps", "-aq", "--filter", f"name=forgeair-{key}"], capture_output=True,
                         text=True).stdout.split()
    if ids:
        subprocess.run(["docker", "rm", "-f"] + ids, capture_output=True)
    for v in subprocess.run(["docker", "volume", "ls", "-q"], capture_output=True, text=True).stdout.split():
        if v.startswith(f"forgeair-{key}") or (logs and v == f"forge-airlock-logs-{key}"):
            subprocess.run(["docker", "volume", "rm", "-f", v], capture_output=True)
    for n in subprocess.run(["docker", "network", "ls", "-q", "--filter", f"name=forgeair-{key}"],
                            capture_output=True, text=True).stdout.split():
        subprocess.run(["docker", "network", "rm", n], capture_output=True)


def leftovers(repo):
    key = repo_key(repo)
    c = subprocess.run(["docker", "ps", "-aq", "--filter", f"name=forgeair-{key}"], capture_output=True,
                       text=True).stdout.split()
    n = subprocess.run(["docker", "network", "ls", "-q", "--filter", f"name=forgeair-{key}"], capture_output=True,
                       text=True).stdout.split()
    v = [x for x in subprocess.run(["docker", "volume", "ls", "-q"], capture_output=True, text=True).stdout.split()
         if x.startswith(f"forgeair-{key}")]
    return len(c) + len(n) + len(v)
