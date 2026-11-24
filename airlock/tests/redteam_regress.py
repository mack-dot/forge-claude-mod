#!/usr/bin/env python3
"""Regression tests for the protocol red-team findings F1 to F8 (no Docker).

Each test drives the REAL gateway HTTP handler, policy, gate log and stdio client on
localhost with a fake upstream object. FORGE_ENGINE_DIR selects the engine under test, so the
same file can be run against the code before the fixes (it must fail there) and after.
Run alone: python3 tests/redteam_regress.py   (exit 0 only if every case passes)
Label: FINITE TEST."""
import contextlib
import io
import json
import os
import socket
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.abspath(os.environ.get("FORGE_ENGINE_DIR") or os.path.join(HERE, "..", "engine"))
PKG = os.path.dirname(ENGINE)
os.environ["FORGE_READ_TIMEOUT_S"] = "1"
os.environ["FORGE_MAX_CONN"] = "8"
os.environ.setdefault("FORGE_STATE", tempfile.mkdtemp(prefix="rtpr-state-"))
sys.path.insert(0, ENGINE)
import gateway as G  # noqa: E402
import mcp_client  # noqa: E402
import workspace  # noqa: E402
from gatelog import GateLog  # noqa: E402
from policy import Policy, PolicyError, Refusal, canonical_json  # noqa: E402

TOKEN = "t" * 32
SCHEMAS = {"get_status": {"service": {}}, "charge": {"amount": {}, "invoice_id": {}, "memo": {}},
           "job": {"mode": {}}, "loose": {}}


def doc(**tool_over):
    return {"version": 1,
            "upstreams": [{"name": "up", "transport": "stdio", "command": ["true"]}],
            "tools": {"allow": [{"tool": "get_status", "arg_rules": {"service": {"type": "string", "not_in": ["billing"],
                                                                                 "optional": True}}},
                                dict({"tool": "loose"}, **tool_over)],
                      "deny": ["drop_database"],
                      "gate": [{"tool": "charge", "unit": "value", "value_field": "amount", "cap": 1000,
                                "expires_in_s": 3600, "id_field": "invoice_id"},
                               {"tool": "job", "unit": "count", "cap": 5, "expires_in_s": 3600}]}}


class FakeUpstream:
    proc = None

    def __init__(self):
        self.calls = []

    def request(self, method, params=None, timeout=None):
        self.calls.append(params)
        if params["name"] == "job" and params["arguments"].get("mode") == "boom":
            raise RuntimeError("upstream client bug")
        return {"content": [{"type": "text", "text": "ok"}], "isError": False}


def make_airlock(policy_doc=None):
    A = G.Airlock.__new__(G.Airlock)
    A.policy = Policy(policy_doc or doc(), environ={})
    data = tempfile.mkdtemp(prefix="rtpr-gate-")
    with contextlib.redirect_stdout(io.StringIO()):
        A.logs = {n: GateLog(data, n, c, ei, ea) for n, (c, ei, ea) in A.policy.logs.items()}
    A.up = FakeUpstream()
    A.upstreams = {"up": A.up}
    A.route = {t: "up" for t in A.policy.tools}
    A.upstream_tools = {"up": {t: {"name": t, "inputSchema": {"type": "object", "properties": SCHEMAS.get(t, {})}}
                               for t in A.policy.tools}}
    A.restart_lock = threading.Lock()
    A.canary, A.canary_lock, A.canary_report = "PASSED", threading.Lock(), None
    A.canary_token, A.fp_salt = TOKEN, b"\x01" * 16
    A.ws = {"fingerprints": [], "hidden_names": ["src/secret.ts"], "readonly_files": [], "closed_dirs": []}
    return A


def serve(A):
    G.Handler.A = A
    srv = G.Server(("127.0.0.1", 0), G.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def raw(port, body=b"", head=None, path="/mcp", method="POST", timeout=10, half_close=True):
    s = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    if head is None:
        head = f"content-length: {len(body)}\r\n"
    try:
        s.sendall(f"{method} {path} HTTP/1.1\r\nhost: 127.0.0.1:{port}\r\ncontent-type: application/json\r\n{head}\r\n".encode()
                  + body)
        if half_close:
            s.shutdown(socket.SHUT_WR)
    except OSError:
        pass
    parts = []
    try:
        while True:
            c = s.recv(1 << 16)
            if not c:
                break
            parts.append(c)
    except socket.timeout:
        parts.append(b"<timeout>")
    except OSError:
        pass
    s.close()
    out = b"".join(parts)
    status = out.split(b"\r\n", 1)[0][9:12].decode(errors="replace")
    return status, out.partition(b"\r\n\r\n")[2]


def rpc(port, method, params=None, **kw):
    return raw(port, json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}).encode(), **kw)


def refused(A, name, args):
    try:
        A.tools_call({"name": name, "arguments": args})
    except Refusal as r:
        return r.code
    except Exception as e:  # noqa: BLE001
        return f"EXC:{type(e).__name__}"
    return None


# ---------------------------------------------------------------- the findings
def f1(case, A, port):
    st, _ = raw(port, b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}', head="content-length: -1\r\n")
    case("F1: content-length -1 is refused (4xx), the body is not read to EOF", st in ("400", "411", "413"), st)
    st, _ = raw(port, b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}', head="")
    case("F1: a missing content-length gives 411", st == "411", st)
    st, _ = raw(port, b"{}", head=f"content-length: {2 << 20}\r\n", timeout=5)
    case("F1: a declared 2 MiB body gives 413", st == "413", st)
    st, _ = raw(port, b"{}", head="content-length: 0x2\r\n")
    case("F1: a non-decimal content-length gives 400", st == "400", st)
    held = []
    for _ in range(12):
        s = socket.create_connection(("127.0.0.1", port), timeout=5)
        s.sendall(f"POST /mcp HTTP/1.1\r\nhost: 127.0.0.1:{port}\r\ncontent-length: 100\r\n\r\n{{".encode())
        held.append(s)
    got503 = 0
    for s in held[8:]:
        s.settimeout(0.8)
        try:
            got503 += b" 503 " in s.recv(200)
        except OSError:
            pass
    case("F1: connections over the cap get 503 at once (thread count bounded)", got503 >= 1, got503)
    time.sleep(1.8)
    st, b = rpc(port, "tools/list", timeout=5)
    case("F1: held half-sent requests time out; the airlock serves again", st == "200" and b"tools" in b, st)
    for s in held:
        s.close()


def f2(case, A, port):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        st, body = rpc(port, "tools/call", {"name": "Z" * 200000, "arguments": {}})
    longest = max([len(x) for x in buf.getvalue().splitlines()] or [0])
    case("F2: a 200 kB tool name gives a short log line (<= 1000 chars)", longest <= 1000, longest)
    case("F2: ... and a short reply (<= 4000 bytes)", len(body) <= 4000, len(body))
    fa = open(os.path.join(PKG, "forge_airlock.py")).read()
    dc = open(os.path.join(PKG, "proof", "docker-compose.yml")).read()
    case("F2: forge up and the proof compose rotate container logs (max-size)",
         '"max-size"' in fa and "max-size" in dc)


def f3(case, A, port):
    bad = doc()
    bad["tools"]["allow"][0]["arg_rules"]["service"] = {"not_in": ["billing"]}
    case("F3: not_in without a scalar type is a policy error", raises_policy(bad))
    for v in ["Billing", " billing", "billing​", "ｂｉｌｌｉｎｇ", ["billing"],
              {"n": "billing"}]:
        case(f"F3: not_in [billing] refuses {v!r}", refused(A, "get_status", {"service": v}) == "FORGE_ARG_REFUSED")
    case("F3: an allowed value still passes", refused(A, "get_status", {"service": "payments"}) is None)
    case("F3: strict_args is on by default: an undeclared argument is refused",
         refused(A, "charge", {"amount": 1, "invoice_id": "S1", "Amount": 5000}) == "FORGE_ARG_REFUSED")
    case("F3: an argument the upstream inputSchema declares is allowed",
         refused(A, "charge", {"amount": 1, "invoice_id": "S2", "memo": "x"}) is None)
    B = make_airlock(doc(strict_args=False))
    case("F3: strict_args: false is the documented opt-out", refused(B, "loose", {"anything": 1}) is None)


def raises_policy(d):
    try:
        Policy(d, environ={})
    except PolicyError:
        return True
    return False


def f4(case, A, port):
    n0 = len([c for c in A.up.calls if c["name"] == "charge"])
    case("F4: a string id is accepted", refused(A, "charge", {"amount": 1, "invoice_id": "INV-1"}) is None)
    for v in ["INV-1", " INV-1", "INV-1 ", "INV-1\t"]:
        case(f"F4: replay of INV-1 as {v!r} is refused", refused(A, "charge", {"amount": 1, "invoice_id": v})
             in ("FORGE_GATE_REFUSED", "FORGE_ARG_REFUSED"))
    for v in [1, 1.0, [1], {"id": 1}, True, "", "   ", "ＩＮＶ-1", "a b"]:
        case(f"F4: id {v!r} is refused", refused(A, "charge", {"amount": 1, "invoice_id": v}) == "FORGE_ARG_REFUSED")
    refused(A, "charge", {"amount": 1, "invoice_id": " INV-2 "})
    sent = [c["arguments"]["invoice_id"] for c in A.up.calls if c["name"] == "charge"][n0:]
    case("F4: exactly 2 charges reached the upstream, with the normalized ids", sent == ["INV-1", "INV-2"], sent)


def f5(case, A, port):
    for lit in (b"NaN", b"Infinity", b"-Infinity", b"1e309"):
        st, _ = raw(port, b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"get_status",'
                    b'"arguments":{"service":"s","x":' + lit + b'}}}')
        case(f"F5: {lit.decode()} in a request does not parse (400)", st == "400", st)
    try:
        canonical_json({"x": float("nan")})
        ok = False
    except ValueError:
        ok = True
    case("F5: canonical_json refuses NaN (allow_nan=False)", ok)


def f6(case):
    tmp = tempfile.mkdtemp(prefix="rtpr-poison-")
    ledger = os.path.join(tmp, "ledger")
    spec = {"name": "poison", "transport": "stdio", "timeout_s": 1,
            "command": [sys.executable, "-I", os.path.join(HERE, "poison_mcp.py"), ledger]}
    up, _ = mcp_client.connect(spec)
    up.request("tools/call", {"name": "echo", "arguments": {"text": "poison"}})
    try:
        r = up.request("tools/call", {"name": "echo", "arguments": {"text": "after"}})
        ok = "after" in json.dumps(r)
    except mcp_client.UpstreamError:
        ok = False
    case("F6: non-object JSON lines from the upstream do not kill the reader", ok)
    try:
        up.request("tools/call", {"name": "echo", "arguments": {"text": "hang", "seconds": 2}})
    except mcp_client.UpstreamError:
        pass
    case("F6: on a timeout the stdio upstream is killed", up.proc.poll() is not None)
    time.sleep(2.5)
    case("F6: the timed-out call does not act later (no hang-done)",
         not os.path.exists(ledger) or "hang-done" not in open(ledger).read())
    try:
        up.proc.kill()
    except OSError:
        pass


def f7(case, port):
    A = make_airlock()
    A.canary = "PENDING"
    G.Handler.A = A
    st, _ = raw(port, method="GET", path="/forge/canary/plan", head="")
    case("F7: canary plan without the token gives 403", st == "403", st)
    st, _ = raw(port, method="GET", path="/forge/canary/plan", head=f"x-forge-canary-token: {TOKEN}\r\n")
    case("F7: canary plan with the token gives 200", st == "200", st)
    rep = b'{"result":"PASS","breaches":[]}'
    st, _ = raw(port, rep, path="/forge/canary/report")
    case("F7: a forged PASS report without the token gives 403; state stays PENDING",
         st == "403" and A.canary == "PENDING", (st, A.canary))
    st, _ = raw(port, rep, path="/forge/canary/report",
                head=f"content-length: {len(rep)}\r\nx-forge-canary-token: {TOKEN}\r\n")
    case("F7: the canary's report with the token is accepted", st == "200" and A.canary == "PASSED", (st, A.canary))
    try:
        salted = workspace.fp(b"secret line 123", b"salt-a") != workspace.fp(b"secret line 123", b"salt-b")
    except TypeError:
        salted = False
    case("F7: fingerprints are salted (two salts give two fingerprints)", salted)


def f8(case, A):
    code = refused(A, "job", {"mode": "boom"})
    log = A.logs["job"]
    last = log.state.get(log.steps(), {})
    case("F8: an unexpected upstream-client exception still finishes the step ABORTED",
         last.get("state") == "ABORTED", (code, last.get("state")))


def run(case):
    A = make_airlock()
    srv, port = serve(A)
    try:
        for name, fn in (("F1", lambda: f1(case, A, port)), ("F2", lambda: f2(case, A, port)),
                         ("F3", lambda: f3(case, A, port)), ("F4", lambda: f4(case, A, port)),
                         ("F5", lambda: f5(case, A, port)), ("F6", lambda: f6(case)),
                         ("F8", lambda: f8(case, A)), ("F7", lambda: f7(case, port))):
            try:
                fn()
            except Exception as e:  # noqa: BLE001
                case(f"{name} crashed: {type(e).__name__}: {str(e)[:200]}", False)
    finally:
        srv.shutdown()


def main():
    res = []

    def case(name, ok, detail=""):
        res.append(bool(ok))
        print(("ok    " if ok else "FAIL  ") + name + ("" if ok else f"   {str(detail)[:200]}"), flush=True)
    run(case)
    print(f"redteam_regress ({ENGINE}): {len(res)} cases, {res.count(False)} failed")
    return 1 if res.count(False) else 0


if __name__ == "__main__":
    sys.exit(main())
