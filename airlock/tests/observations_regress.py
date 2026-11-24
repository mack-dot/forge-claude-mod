"""Regression tests for the review observations that the owner asked to fix (PR #29, after V1-V7).

  O1  Listen side: with two Origin headers, the airlock used the first one (an allowed Origin first, a foreign one
      second was served).
  O2  Listen side: no Host header check (DNS rebinding): a request with a foreign Host was served.
  O3  Canary egress probe: a proxy that did not answer within 5 s (or answered something other than 403) counted
      as "refused" (PASS). Now only an explicit 403 passes; anything else is a BREACH (fail closed).
  O4  --pass-env: a variable whose VALUE equals an upstream secret (or the model key) under another NAME was
      passed to the agent. Now `forge up` stops with exit 2 (the values are compared as digests, never printed).
  O5  apply_changes was not all-or-nothing: an interrupt (Ctrl-C) or an error part-way left some files new and
      some old. Now every change is staged first, then verified, then renamed in one pass with signals held.
  O6  Policy path globs matched only the exact spelling: on the case-sensitive box 'Secrets/key' was NOT hidden by
      'secrets/**' and 'TESTS/x' was writable through MCP, although the sync-back rule (case and Unicode folding,
      for a case-insensitive host) treats them as the same path. Now hidden and read-only rules also match the
      folded form (stricter only; visible_paths is not widened).

Stdlib only, temp dirs, in-process servers. Run: python3 airlock/tests/observations_regress.py"""
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
import time
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.join(HERE, "..")
sys.path.insert(0, os.path.join(PKG, "engine"))
sys.path.insert(0, os.path.join(PKG, "agent"))
sys.path.insert(0, PKG)
import sync  # noqa: E402
from policy import Policy, Refusal  # noqa: E402

DOC = {"version": 1,
       "upstreams": [{"name": "fs", "transport": "stdio", "command": ["node", "x.js", "/ws"]}],
       "tools": {"allow": [{"tool": "read_file", "upstream": "fs", "path_args": {"path": "read"}},
                           {"tool": "write_file", "upstream": "fs", "path_args": {"path": "write"}}]},
       "paths": {"hidden_paths": ["secrets/**", "src/café/**"], "readonly_paths": ["tests/**"]}}


# ---------------------------------------------------------------- O1, O2
def _gateway():
    import gateway as G
    A = G.Airlock.__new__(G.Airlock)
    A.policy = Policy(DOC, resolve=False)
    A.upstreams, A.upstream_tools, A.route = {}, {}, {}
    A.canary, A.canary_lock, A.canary_token, A.sessions = "PASSED", threading.Lock(), "t" * 32, {}
    A.ws_mount = "off"
    A.tools_list = lambda: {"tools": []}
    G.Handler.A = A
    srv = G.Server(("127.0.0.1", 0), G.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return G, srv, srv.server_address[1]


def _raw(port, head, method="POST", path="/mcp"):
    body = b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
    s = socket.create_connection(("127.0.0.1", port), timeout=5)
    s.sendall((f"{method} {path} HTTP/1.1\r\n{head}content-type: application/json\r\n"
               f"content-length: {len(body)}\r\n\r\n").encode() + body)
    try:
        d = s.recv(300)
    except OSError:
        d = b""
    s.close()
    parts = d.split(b" ", 2)
    return int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None


def o1_o2_listen_checks():
    G, srv, port = _gateway()
    saved = list(G.ALLOWED_ORIGINS)
    G.ALLOWED_ORIGINS[:] = ["http://ok.example"]
    h = f"host: 127.0.0.1:{port}\r\n"
    out = []
    try:
        st = _raw(port, h + "origin: http://ok.example\r\norigin: http://evil.example\r\n")
        out.append(("O1 listen: two Origin headers (allowed first, foreign second) are refused 403", st == 403, st))
        st = _raw(port, h + "origin: http://ok.example\r\norigin: http://ok.example\r\n")
        out.append(("O1 listen: two Origin headers are refused even when both are allowed", st == 403, st))
        st = _raw(port, h + "origin: http://ok.example\r\n")
        out.append(("O1 listen: control: one allowed Origin is served", st == 200, st))
        for host in ("attacker.example", f"attacker.example:{port}", f"127.0.0.1.attacker.example:{port}",
                     f"localhost.:{port}", f"127.0.0.1:{port + 1 if port < 65535 else port - 1}"):
            st = _raw(port, f"host: {host}\r\n")
            out.append((f"O2 listen: a foreign Host {host!r} is refused (421)", st == 421, st))
        st = _raw(port, "")
        out.append(("O2 listen: a request without Host is refused (421)", st == 421, st))
        st = _raw(port, f"host: 127.0.0.1:{port}\r\nhost: attacker.example\r\n")
        out.append(("O2 listen: two Host headers are refused (421)", st == 421, st))
        st = _raw(port, "host: attacker.example\r\n", method="GET", path="/healthz")
        out.append(("O2 listen: GET /healthz with a foreign Host is refused too (421)", st == 421, st))
        oks = [_raw(port, f"host: {x}\r\n") for x in (f"127.0.0.1:{port}", f"localhost:{port}", f"airlock:{port}",
                                                       f"[::1]:{port}", "airlock", f"AIRLOCK:{port}")]
        out.append(("O2 listen: control: the airlock's own names and loopback are served", oks == [200] * 6, oks))
    finally:
        G.ALLOWED_ORIGINS[:] = saved
        srv.shutdown()
        srv.server_close()
    return out


# ---------------------------------------------------------------- O3
def _fake_proxy(mode):
    """mode 'slow': accepts, waits 2 s, closes without an answer. 'deny': answers 403 at once."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(8)

    def loop():
        while True:
            try:
                c, _ = s.accept()
            except OSError:
                return

            def h(c=c):
                try:
                    c.recv(4096)
                    if mode == "slow":
                        time.sleep(2)
                    else:
                        c.sendall(b"HTTP/1.1 403 Forbidden\r\ncontent-length: 0\r\n\r\n")
                except OSError:
                    pass
                finally:
                    c.close()
            threading.Thread(target=h, daemon=True).start()
    threading.Thread(target=loop, daemon=True).start()
    return s, s.getsockname()[1]


def _run_canary(proxy_port, root):
    import canary as C
    import mcpc
    plan = {"agent_root": root, "fp_salt": "", "fingerprints": [], "secret_fps": [], "hidden_names": [],
            "airlock_paths": [], "readonly_files": [], "closed_dirs": [], "egress_targets": [],
            "egress_proxy_refuse": ["example.com:443"], "egress_proxy": f"http://127.0.0.1:{proxy_port}",
            "deny": [], "unlisted": [], "mcp_read": [], "mcp_write": [], "mcp_rev": []}
    report = {}

    def http(method, path, body=None, timeout=60):
        if method == "GET":
            return 200, plan
        report.update(body)
        return 200, {"state": "PASSED" if body["result"] == "PASS" else "BREACH"}
    saved = (mcpc.http, mcpc.tool_names, os.walk, os.makedirs, {k: os.environ.get(k) for k in
                                                                 ("HTTPS_PROXY", "https_proxy")})
    real_walk, real_makedirs = os.walk, os.makedirs
    mcpc.http, mcpc.tool_names = http, lambda: set()
    os.walk = lambda top, *a, **k: iter([]) if top == "/" else real_walk(top, *a, **k)  # no whole-disk scan
    os.makedirs = lambda p, *a, **k: None if p == "/tmp/forge-canary" else real_makedirs(p, *a, **k)
    for k in ("HTTPS_PROXY", "https_proxy"):
        os.environ.pop(k, None)
    C.BREACHES[:] = []
    if hasattr(C, "PROXY_TIMEOUT_S"):
        C.PROXY_TIMEOUT_S = 1
    try:
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            code = C.main()
    finally:
        mcpc.http, mcpc.tool_names, os.walk, os.makedirs = saved[:4]
        for k, v in saved[4].items():
            if v is not None:
                os.environ[k] = v
    return code, report, buf.getvalue()


def o3_canary_slow_proxy():
    out = []
    tmp = tempfile.mkdtemp()
    socks = []
    try:
        root = os.path.join(tmp, "ws")
        os.makedirs(root)
        subprocess.run(["git", "init", "-q", root], check=True, capture_output=True)
        s, port = _fake_proxy("slow")
        socks.append(s)
        code, rep, log = _run_canary(port, root)
        out.append(("O3 canary: a proxy that does not answer CONNECT in time is a BREACH (fail closed), not a pass",
                    rep.get("result") == "BREACH" and code == 10, (code, rep.get("result"), rep.get("breaches"))))
        out.append(("O3 canary: the message says the refusal could not be verified",
                    any("could not verify" in b for b in rep.get("breaches", [])), rep.get("breaches")))
        s, port = _fake_proxy("deny")
        socks.append(s)
        code, rep, _ = _run_canary(port, root)
        out.append(("O3 canary: control: a proxy that answers 403 passes", rep.get("result") == "PASS" and code == 0,
                    (code, rep.get("breaches"))))
    finally:
        for s in socks:
            s.close()
        shutil.rmtree(tmp, ignore_errors=True)
    return out


# ---------------------------------------------------------------- O4
class _Reached(Exception):
    pass


def o4_pass_env_value():
    import forge_airlock as FA
    tmp = tempfile.mkdtemp()
    out = []
    doc = dict(DOC, upstreams=DOC["upstreams"] + [{"name": "pay", "transport": "http", "url": "http://pay:1/mcp",
                                                   "headers": {"Authorization": "Bearer ${FORGE_T_UPSTREAM}"}}])
    secret = "FAKE-value-" + os.urandom(8).hex()
    env_saved = {k: os.environ.get(k) for k in ("FORGE_T_UPSTREAM", "FORGE_T_COPY", "FORGE_T_OTHER",
                                                 "FORGE_CANARY_TOKEN")}
    saved = (FA.need_docker, FA.build_images, FA.load_policy, FA.B.docker_runtimes, FA.stage_repo, FA.run)
    try:
        repo = os.path.join(tmp, "repo")
        os.makedirs(repo)
        subprocess.run(["git", "init", "-q", repo], check=True, capture_output=True)
        with open(os.path.join(repo, "forge.yaml"), "w") as f:
            json.dump(doc, f)
        pol = Policy(doc, resolve=False)
        os.environ.update(FORGE_T_UPSTREAM=secret, FORGE_T_COPY=secret, FORGE_T_OTHER="FAKE-other-value")
        FA.need_docker, FA.build_images = (lambda: None), (lambda claude: None)
        FA.load_policy = lambda p, r=None: (pol, {})
        FA.B.docker_runtimes = lambda: {"runc"}

        def reached(repo, dest):
            raise _Reached()
        FA.stage_repo = reached
        FA.run = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0)

        def up(name):
            err = io.StringIO()
            try:
                with contextlib.redirect_stderr(err):
                    FA.main(["up", "--repo", repo, "--pass-env", name, "--", "sh"])
            except _Reached:
                return "started", err.getvalue()
            except SystemExit as e:
                return e.code, err.getvalue()
            return "returned", err.getvalue()
        code, err = up("FORGE_T_COPY")
        out.append(("O4 --pass-env: a variable whose value equals an upstream secret stops the start (exit 2)",
                    code == 2, (code, err[-300:])))
        out.append(("O4 --pass-env: the message names both variables and never prints the value",
                    "FORGE_T_COPY" in err and "FORGE_T_UPSTREAM" in err and secret not in err, err[-300:]))
        code, err = up("FORGE_T_OTHER")
        out.append(("O4 --pass-env: control: a variable with another value still starts", code == "started",
                    (code, err[-200:])))
    finally:
        (FA.need_docker, FA.build_images, FA.load_policy, FA.B.docker_runtimes, FA.stage_repo, FA.run) = saved
        for k, v in env_saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(tmp, ignore_errors=True)
    return out


# ---------------------------------------------------------------- O5
def _snapshot(root):
    snap = {}
    for d, dirs, files in os.walk(root):
        for n in dirs:
            snap[os.path.relpath(os.path.join(d, n), root)] = "dir"
        for n in files:
            p = os.path.join(d, n)
            with open(p, "rb") as f:
                snap[os.path.relpath(p, root)] = f.read()
    return snap


def o5_all_or_nothing():
    out = []
    pol = Policy(DOC, resolve=False)
    points = [("os.open", 1), ("os.open", 2), ("os.replace", 1), ("os.replace", 2), ("os.remove", 1)]
    for fn_name, nth in points:
        tmp = tempfile.mkdtemp()
        try:
            real, stage, ex = (os.path.join(tmp, x) for x in ("real", "stage", "ex"))
            for r in (real, stage):
                os.makedirs(os.path.join(r, "src"))
                for rel, txt in (("src/a.txt", "old a\n"), ("src/c.txt", "old c\n")):
                    with open(os.path.join(r, rel), "w") as f:
                        f.write(txt)
            os.makedirs(os.path.join(ex, "files"))
            changes = []
            for i, (rel, status, data) in enumerate((("src/a.txt", "modified", b"new a\n"),
                                                     ("src/new/b.txt", "added", b"new b\n"),
                                                     ("src/c.txt", "deleted", None)), 1):
                c = {"path": rel, "status": status, "symlink": False}
                if data is not None:
                    with open(os.path.join(ex, "files", str(i)), "wb") as f:
                        f.write(data)
                    c.update(file=f"files/{i}", sha256=hashlib.sha256(data).hexdigest(), mode="100644")
                changes.append(c)
            apply, rejected = sync.plan(pol, changes, ex, stage, real)
            old = _snapshot(real)
            new = dict(old)
            new.update({"src/a.txt": b"new a\n", "src/new": "dir", "src/new/b.txt": b"new b\n"})
            new.pop("src/c.txt")
            orig = getattr(os, fn_name.split(".")[1])
            count = [0]

            def hooked(*a, **k):
                count[0] += 1
                if count[0] == nth:
                    os.kill(os.getpid(), signal.SIGINT)  # a real Ctrl-C at this point
                return orig(*a, **k)
            setattr(os, fn_name.split(".")[1], hooked)
            interrupted = False
            try:
                sync.apply_changes(apply, real)
                time.sleep(0.05)
            except KeyboardInterrupt:
                interrupted = True
            finally:
                setattr(os, fn_name.split(".")[1], orig)
            got = _snapshot(real)
            temps = [p for p in got if os.path.basename(p).startswith(".forge-airlock.")]
            state = "old" if got == old else "new" if got == new else "MIXED"
            out.append((f"O5 apply: Ctrl-C at {fn_name} call {nth}: the repository is all old or all new "
                        f"(got {state}), no temp file, the Ctrl-C is not lost",
                        not rejected and state != "MIXED" and not temps and interrupted,
                        (state, interrupted, temps, sorted(got))))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return out


# ---------------------------------------------------------------- O6
def o6_policy_fold():
    pol = Policy(DOC, resolve=False)
    nfd = unicodedata.normalize("NFD", "src/café/price.ts")
    out = []
    for rel in ("Secrets/key", "SECRETS/key", nfd, "SRC/Café/x"):
        out.append((f"O6 policy: {rel!r} is hidden like the sync-back rule says",
                    pol.is_hidden(rel) and sync.refusal(pol, rel) is not None, (pol.is_hidden(rel),
                                                                                   sync.refusal(pol, rel))))
    out.append(("O6 policy: 'TESTS/x.ts' is read-only like the sync-back rule says",
                pol.readonly.match("TESTS/x.ts") and sync.refusal(pol, "TESTS/x.ts") is not None))
    tmp = tempfile.mkdtemp()
    try:
        doc = json.loads(json.dumps(DOC))
        doc["paths"]["mcp_root"] = tmp
        p = Policy(doc, resolve=False)
        res = {}
        for raw, mode in (("Secrets/key", "read"), ("TESTS/x.ts", "write")):
            try:
                p.check_path(raw, mode)
                res[raw] = "allowed"
            except Refusal as e:
                res[raw] = e.code
        out.append(("O6 policy: MCP refuses 'Secrets/key' (read) and 'TESTS/x.ts' (write)",
                    res == {"Secrets/key": "FORGE_PATH_HIDDEN", "TESTS/x.ts": "FORGE_PATH_READONLY"}, res))
    except Exception as e:  # noqa: BLE001 - mcp_root may not be a paths key in this schema
        out.append(("O6 policy: MCP check", False, f"{type(e).__name__}: {e}"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    out.append(("O6 policy: control: a normal path is not hidden or read-only",
                not pol.is_hidden("src/app.ts") and not pol.readonly.match("src/app.ts")))
    vis = Policy(dict(DOC, paths={"visible_paths": ["src/**"]}), resolve=False)
    out.append(("O6 policy: control: visible_paths is not widened by folding ('SRC/x' stays hidden in the box)",
                vis.is_hidden("SRC/x") and not vis.is_hidden("src/x")))
    return out


def cases():
    out = []
    for fn in (o1_o2_listen_checks, o3_canary_slow_proxy, o4_pass_env_value, o5_all_or_nothing, o6_policy_fold):
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
