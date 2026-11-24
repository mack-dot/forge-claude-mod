#!/usr/bin/env python3
"""M2.2 tests (no Docker): MCP Streamable HTTP (upstream and listen side), multi-upstream
composition with namespaced tool names, and `roles:` profiles that can only narrow.

The e2e part runs on localhost with REAL processes: the airlock's gateway handler (HTTP listen
side), its policy, gate logs (the compiled #91 verifier) and upstream clients, in front of
mocks/mock_streamable_http.py (as an HTTP server in JSON mode, as one in SSE mode, and over stdio)
and mocks/mock_crash_mcp.py (stdio). Every gate decision goes through the same Policy.check_call
and Airlock.tools_call for every transport; the ledgers of the mocks count real side effects.

FORGE_ENGINE_DIR selects the engine under test (negative control: the code before M2.2, or an
engine with a gate removed, must fail cases here). --only <word> runs only the cases whose name
contains the word. Run alone: python3 tests/m22_regress.py (exit 0 only if every case passes).
Label: FINITE TEST."""
import contextlib
import http.client
import io
import json
import os
import random
import socket
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.abspath(os.environ.get("FORGE_ENGINE_DIR") or os.path.join(HERE, "..", "engine"))
MOCKS = os.path.join(HERE, "..", "mocks")
MOCK = os.path.join(MOCKS, "mock_streamable_http.py")
CRASH = os.path.join(MOCKS, "mock_crash_mcp.py")
os.environ.setdefault("FORGE_STATE", tempfile.mkdtemp(prefix="m22-state-"))
sys.path.insert(0, ENGINE)
import gateway as G  # noqa: E402
import mcp_client  # noqa: E402
from gatelog import GateLog  # noqa: E402
from policy import Policy, PolicyError, Refusal  # noqa: E402

KEY = "FAKE_mock_bearer_not_real_0000"
ONLY = sys.argv[sys.argv.index("--only") + 1] if "--only" in sys.argv else ""
CLEAN = []  # processes and dirs to remove at the end


def raises(fn, contains=""):
    try:
        fn()
    except PolicyError as e:
        return contains in str(e)
    return False


# ---------------------------------------------------------------- processes
def start_mock(*args, port=0):
    p = subprocess.Popen([sys.executable, "-I", MOCK, "--port", str(port)] + list(args), stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL, text=True)
    line = p.stdout.readline()
    if not line.startswith("PORT "):
        raise RuntimeError(f"mock did not start: {line!r}")
    CLEAN.append(p)
    return p, int(line.split()[1])


def ledger_rows(path, tool=None):
    if not os.path.exists(path):
        return 0
    rows = [json.loads(x) for x in open(path) if x.strip()]
    return sum(1 for r in rows if tool is None or r.get("tool") == tool or r.get("mode") == tool)


class Env:
    """Two HTTP mocks (JSON, SSE), one stdio mock with the same tools, one crashing stdio mock."""

    def __init__(self):
        self.dir = tempfile.mkdtemp(prefix="m22-")
        self.led = {k: os.path.join(self.dir, f"{k}.jsonl") for k in ("http", "sse", "local", "crashy")}
        self.http_proc, self.http_port = start_mock("--key", KEY, "--ledger", self.led["http"], "--oversize", "256")
        self.sse_proc, self.sse_port = start_mock("--sse", "--ledger", self.led["sse"])

    def restart_http(self):
        self.http_proc.kill()
        self.http_proc.wait()
        for _ in range(50):
            try:
                self.http_proc, _p = start_mock("--key", KEY, "--ledger", self.led["http"], port=self.http_port)
                return True
            except RuntimeError:
                time.sleep(0.1)
        return False


def base_doc(env, **over):
    ns = {"type": "string", "not_in": ["billing"], "optional": True}
    d = {"version": 1, "namespace": True,
         "upstreams": [
             {"name": "local", "command": [sys.executable, "-I", MOCK, "--stdio", "--ledger", env.led["local"]]},
             {"name": "http", "url": f"http://127.0.0.1:{env.http_port}/mcp",
              "headers": {"authorization": "Bearer ${MOCK_KEY}"}, "timeout_s": 5},
             {"name": "sse", "url": f"http://127.0.0.1:{env.sse_port}/mcp", "timeout_s": 5},
             {"name": "crashy", "transport": "stdio", "command": [sys.executable, "-I", CRASH],
              "env": {"CRASH_LEDGER": env.led["crashy"]}, "timeout_s": 5}],
         "tools": {
             "allow": [{"tool": f"{u}__get_status", "arg_rules": {"service": ns}} for u in ("local", "http", "sse")]
             + [{"tool": "sse__job"}, {"tool": "local__job"}, {"tool": "crashy__crashy_job"}],
             "deny": ["local__drop_database", "http__drop_database", "sse__drop_database"],
             "gate": [{"tool": "http__charge", "unit": "value", "value_field": "amount", "cap": 1000,
                       "expires_in_s": 3600, "id_field": "invoice_id"},
                      {"tool": "http__job", "unit": "count", "cap": 100, "expires_in_s": 3600},
                      {"tool": "sse__deploy", "unit": "count", "cap": 3, "expires_in_s": 3600},
                      {"tool": "local__deploy", "unit": "count", "cap": 3, "expires_in_s": 3600}]},
         "paths": {"hidden_paths": [".env*"], "visible_paths": ["src/**", "docs/**", "README.md"]},
         "roles": {"junior": {"tools": ["http__get_status", "sse__deploy", "local__get_status"],
                              "cap": {"sse__deploy": 1}, "hidden_paths": ["src/billing/**"],
                              "visible_paths": ["src/ui/**", "README.md"]},
                   "contractor": {"tools": ["http__get_status"]},
                   "senior": {}}}
    d.update(over)
    return d


def live(doc, role=None):
    """A real Airlock object (no workspace, no canary run: canary set PASSED) with real upstreams."""
    A = G.Airlock.__new__(G.Airlock)
    A.policy = Policy(doc, environ={"MOCK_KEY": KEY}, role=role)
    data = tempfile.mkdtemp(prefix="m22-gate-")
    with contextlib.redirect_stdout(io.StringIO()):
        A.logs = {n: GateLog(data, n, c, ei, ea) for n, (c, ei, ea) in A.policy.logs.items()}
    A.connect_upstreams()
    A.restart_lock = threading.Lock()
    A.canary, A.canary_lock, A.canary_report = "PASSED", threading.Lock(), None
    A.canary_token, A.fp_salt, A.llm, A.ws_mount, A.desk = "t" * 32, b"\x01" * 16, None, "off", None
    A.ws = {"fingerprints": [], "hidden_names": [], "readonly_files": [], "closed_dirs": []}
    return A


def serve(A):
    G.Handler.A = A
    srv = G.Server(("127.0.0.1", 0), G.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


class Client:
    """A Streamable HTTP MCP client of the airlock's listen side (what claude in the box does)."""

    def __init__(self, port, session=True):
        self.port, self.sid, self.n = port, None, 0
        if session:
            st, h, b = self.post({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                                  "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                                             "clientInfo": {"name": "m22", "version": "0"}}})
            self.sid = h.get("mcp-session-id")
            self.init = (st, b)

    def post(self, msg, headers=None, raw=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        h = {"content-type": "application/json", "accept": "application/json, text/event-stream"}
        if self.sid:
            h["mcp-session-id"] = self.sid
            h["mcp-protocol-version"] = "2025-06-18"
        h.update(headers or {})
        c.request("POST", "/mcp", body=raw if raw is not None else json.dumps(msg).encode(), headers=h)
        r = c.getresponse()
        body = r.read()
        hdrs = {k.lower(): v for k, v in r.getheaders()}
        c.close()
        try:
            return r.status, hdrs, json.loads(body or b"null")
        except ValueError:
            return r.status, hdrs, body

    def call(self, tool, **args):
        """(forge_code or None, result or error)"""
        self.n += 1
        st, _, msg = self.post({"jsonrpc": "2.0", "id": self.n, "method": "tools/call",
                                "params": {"name": tool, "arguments": args}})
        if not isinstance(msg, dict):
            return f"HTTP{st}", msg
        if "error" in msg:
            return (msg["error"].get("data") or {}).get("forge_code") or f"RPC{msg['error'].get('code')}", msg["error"]
        return None, msg["result"]

    def tools(self):
        self.n += 1
        return sorted(t["name"] for t in self.post({"jsonrpc": "2.0", "id": self.n, "method": "tools/list"})[2]
                      ["result"]["tools"])


def text(res):
    return "".join(c.get("text", "") for c in (res or {}).get("content", []))


# ---------------------------------------------------------------- unit: policy
def unit_policy(case, env):
    d = base_doc(env)
    p = Policy(d, environ={"MOCK_KEY": KEY})
    case("policy: url-only upstream is http, command-only is stdio (transport inferred)",
         p.upstreams["http"]["transport"] == "http" and p.upstreams["local"]["transport"] == "stdio")
    for bad, why in (({"name": "x", "url": "http://h/mcp", "command": ["a"]}, "exactly one"),
                     ({"name": "x", "url": "file:///etc/passwd"}, "http:// or https://"),
                     ({"name": "x", "url": "http://user:pw@h/mcp"}, "user info"),
                     ({"name": "x", "url": "http:///mcp"}, "user info")):
        dd = base_doc(env)
        dd["upstreams"].append(bad)
        case(f"policy: bad http upstream refused at load ({why})", raises(lambda: Policy(dd, environ={"MOCK_KEY": KEY}),
                                                                         why))
    case("policy: namespaced tools route to their upstream (http__charge -> http / charge)",
         p.tools["http__charge"].upstream == "http" and p.tools["http__charge"].raw == "charge")
    for name, why in (("nope__get_status", "known upstream"), ("get_status", "known upstream"),
                      ("http__", "known upstream")):
        dd = base_doc(env)
        dd["tools"]["allow"].append({"tool": name})
        case(f"policy: namespace: true refuses tool name {name!r} at load", raises(lambda: Policy(dd, environ={"MOCK_KEY": KEY}), why))
    dd = base_doc(env)
    dd["tools"]["allow"].append({"tool": "http__deploy", "upstream": "sse"})
    case("policy: upstream: that contradicts the namespace is refused", raises(lambda: Policy(dd, environ={"MOCK_KEY": KEY}),
                                                                              "does not match"))
    # planted namespacing collisions: 'a' + 'b__c' and 'a__b' + 'c' are both 'a__b__c'
    for up, why in (("a__b", "would collide"), ("a_", "would collide"), ("HTTP", "differ only in case")):
        dd = base_doc(env)
        dd["upstreams"].append({"name": up, "url": "http://127.0.0.1:1/mcp"})
        case(f"policy: namespacing collision fails load (upstream {up!r})", raises(lambda: Policy(dd, environ={"MOCK_KEY": KEY}), why))
    dd = base_doc(env)
    dd["tools"]["deny"].append("ghost__drop_database")
    case("policy: a deny entry with an unknown namespace fails load (typo guard)",
         raises(lambda: Policy(dd, environ={"MOCK_KEY": KEY}), "known upstream"))
    case("policy: namespace must be a boolean", raises(lambda: Policy(base_doc(env, namespace="yes"), environ={"MOCK_KEY": KEY}),
                                                       "true or false"))


def unit_roles(case, env):
    E = {"MOCK_KEY": KEY}
    base = Policy(base_doc(env), environ=E)
    j = Policy(base_doc(env), environ=E, role="junior")
    case("role: junior keeps only its tools", sorted(j.tools) == ["http__get_status", "local__get_status", "sse__deploy"])
    case("role: junior's cap is an extra smaller log (sse__deploy.role-junior cap 1) and the base log stays",
         j.logs.get("sse__deploy.role-junior", (None,))[0] == 1 and j.logs["sse__deploy"][0] == 3)
    case("role: junior adds hidden paths and restricts visible paths",
         j.is_hidden("src/billing/x.ts") and j.is_hidden("src/app.ts") and not j.is_hidden("src/ui/b.ts")
         and not base.is_hidden("src/app.ts"))
    case("role: senior ({}) equals the base policy", sorted(Policy(base_doc(env), environ=E, role="senior").tools)
         == sorted(base.tools))
    case("role: no role selected -> the base policy", sorted(Policy(base_doc(env), environ=E).tools) == sorted(base.tools))
    case("role: an unknown role fails closed", raises(lambda: Policy(base_doc(env), environ=E, role="admin"),
                                                      "not defined"))
    dd = base_doc(env)
    dd.pop("roles")
    case("role: a role selected without roles: fails closed", raises(lambda: Policy(dd, environ=E, role="junior"),
                                                                     "not defined"))
    widen = [
        ({"tools": ["http__rotate_keys"]}, "not allowed or gated", "a tool the base does not list"),
        ({"tools": ["http__drop_database"]}, "denied by the base", "a tool the base denies"),
        ({"cap": {"sse__deploy": 4}}, "above the base cap", "a cap above the base cap"),
        ({"cap": 5000}, "above the base cap", "an integer cap above a base cap"),
        ({"cap": {"http__get_status": 1}}, "not a gated tool", "a cap on an ungated tool"),
        ({"visible_paths": ["**"]}, "not inside the base", "visible_paths ** over a narrower base"),
        ({"visible_paths": ["infra/**"]}, "not inside the base", "visible_paths outside the base"),
        ({"visible_paths": ["*.ts"]}, "not inside the base", "a basename visible pattern (any depth)"),
        ({"paths": {"hidden_paths": []}}, "unknown keys", "an unknown key (paths)"),
    ]
    for spec, why, what in widen:
        dd = base_doc(env)
        dd["roles"]["evil"] = spec
        case(f"role: widening fails load: {what}", raises(lambda: Policy(dd, environ=E, role="evil"), why), why)
        case(f"role: ... also when the widening role is NOT selected ({what})", raises(lambda: Policy(dd, environ=E), why))
    for rn in ("Admin", "a b", "x" * 30):
        dd = base_doc(env)
        dd["roles"][rn] = {}
        case(f"role: bad role name {rn[:12]!r} fails load", raises(lambda: Policy(dd, environ=E), "must match"))
    # property test: for random role specs that load, the role never shows a path the base hides
    # and never offers a tool the base does not offer or a cap above the base cap
    rng = random.Random(22)
    paths = ["README.md", "src/app.ts", "src/ui/b.ts", "src/ui/deep/c.ts", "src/billing/x.ts", "docs/a.md",
             "docs/x/.env", ".env", "infra/main.tf", "src/ui/.env.local", "lib/z.py", "docs/ui/b.md"]
    pats = ["src/**", "src/ui/**", "docs/**", "README.md", "**", "infra/**", "*.md", "src/ui", "docs/x/**", "lib/**"]
    loaded = refused = bad = 0
    for _ in range(400):
        spec = {}
        if rng.random() < 0.7:
            spec["visible_paths"] = rng.sample(pats, rng.randint(1, 3))
        if rng.random() < 0.5:
            spec["hidden_paths"] = rng.sample(pats, rng.randint(1, 2))
        if rng.random() < 0.5:
            spec["tools"] = rng.sample(sorted(base.tools) + ["http__deploy", "sse__drop_database"], rng.randint(1, 4))
        if rng.random() < 0.4:
            spec["cap"] = rng.choice([0, 1, 3, 4, 999, 1000, 1001])
        dd = base_doc(env)
        dd["roles"] = {"r": spec}
        try:
            r = Policy(dd, environ=E, role="r")
        except PolicyError:
            refused += 1
            continue
        loaded += 1
        for x in paths:
            if base.is_hidden(x) and not r.is_hidden(x):
                bad += 1
        for n, tp in r.tools.items():
            if n not in base.tools or (tp.gated and (tp.role_cap if tp.role_cap is not None else tp.cap) > base.tools[n].cap):
                bad += 1
    case(f"role: property test, 400 random roles ({loaded} loaded, {refused} refused at load): none widens "
         "(paths, tools, caps)", bad == 0 and loaded > 50 and refused > 50, bad)


# ---------------------------------------------------------------- e2e
def e2e(case, env):
    A = live(base_doc(env))
    srv, port = serve(A)
    c = Client(port)
    case("http listen: initialize answers 200 with an Mcp-Session-Id", c.init[0] == 200 and bool(c.sid), c.init)
    names = c.tools()
    case("compose: tools/list shows the namespaced tools of 4 upstreams (stdio, http json, http sse, stdio)",
         {"local__get_status", "http__get_status", "sse__get_status", "http__charge", "sse__deploy",
          "crashy__crashy_job"} <= set(names) and not any(n.endswith("drop_database") for n in names), names)
    case("compose: the raw (un-namespaced) name get_status is refused (default deny)",
         c.call("get_status")[0] == "FORGE_TOOL_BLOCKED")
    # identical gates on every transport
    rows = {}
    for u in ("local", "http", "sse"):
        before = ledger_rows(env.led[u])
        rows[u] = (c.call(f"{u}__drop_database", name="prod")[0],
                   c.call(f"{u}__get_status", service="Billing")[0],
                   c.call(f"{u}__get_status", service="x", extra=1)[0],
                   c.call(f"{u}__rotate_keys")[0],
                   ledger_rows(env.led[u]) - before)
    case("gates: denied tool over HTTP (json) is refused FORGE_TOOL_BLOCKED and never reaches the upstream",
         rows["http"][0] == "FORGE_TOOL_BLOCKED" and rows["http"][4] == 0, rows["http"])
    case("gates: denied tool over HTTP (sse) is refused FORGE_TOOL_BLOCKED and never reaches the upstream",
         rows["sse"][0] == "FORGE_TOOL_BLOCKED" and rows["sse"][4] == 0, rows["sse"])
    case("gates: deny / arg rule (not_in, NFKC+casefold) / strict args / unlisted give the SAME codes over stdio, "
         "http json and http sse", rows["local"] == rows["http"] == rows["sse"]
         == ("FORGE_TOOL_BLOCKED", "FORGE_ARG_REFUSED", "FORGE_ARG_REFUSED", "FORGE_TOOL_BLOCKED", 0), rows)
    ok = [c.call(f"{u}__get_status", service="api") for u in ("local", "http", "sse")]
    case("gates: an allowed call passes on all 3 transports (sse: notification event first, multi-line data)",
         all(x[0] is None and "get_status done" in text(x[1]) for x in ok), ok)
    # gated over HTTP: value cap, duplicate id
    got = [c.call("http__charge", amount=400, invoice_id=f"INV-{i}")[0] for i in range(3)]
    dup = c.call("http__charge", amount=1, invoice_id="INV-0")[0]
    case("gates: value cap 1000 over HTTP: 400 + 400 accepted, the 3rd 400 REFUSED by the verifier",
         got == [None, None, "FORGE_GATE_REFUSED"] and ledger_rows(env.led["http"], "charge") == 2, got)
    case("gates: replayed invoice id over HTTP is refused", dup == "FORGE_GATE_REFUSED")
    got = [c.call("sse__deploy", env="prod", branch="main")[0] for _ in range(4)]
    case("gates: count cap 3 over HTTP sse: 3 accepted, the 4th refused; upstream ran exactly 3",
         got == [None] * 3 + ["FORGE_GATE_REFUSED"] and ledger_rows(env.led["sse"], "deploy") == 3, got)
    # planted protocol faults on a gated HTTP call: each refused, each step spent (ABORTED)
    mcp_client.MAX_RESPONSE = 64 << 10
    faults = {}
    for mode in ("malformed_sse", "oversize", "wrong_session", "wrong_id", "bad_ctype"):
        faults[mode] = c.call("http__job", mode=mode)[0]
    mcp_client.MAX_RESPONSE = 8 << 20
    for mode, code in faults.items():
        case(f"http upstream: planted fault {mode} is refused FORGE_UPSTREAM_FAILED", code == "FORGE_UPSTREAM_FAILED",
             code)
    st = A.logs["http__job"].audit()
    case("http upstream: each refused fault left its gate step spent (5 ABORTED, gross 5, verifier ACCEPT)",
         st["journal"].get("ABORTED") == 5 and st["gross"] == 5 and st["proved_verdict"] == "ACCEPT", st)
    # session expiry: the call that hits 404 fails (not retried), the next call opens a new session
    old = A.upstreams["http"].session
    r1 = c.call("http__job", mode="expire_session")[0]
    r2 = c.call("http__job", mode="ok")
    case("http upstream: a 404 for our session fails that call only; the next call runs in a new session",
         r1 == "FORGE_UPSTREAM_FAILED" and r2[0] is None and A.upstreams["http"].session not in (None, old), (r1, r2[0]))
    # crash isolation
    r = c.call("crashy__crashy_job", mode="crash")[0]
    o1 = c.call("http__get_status", service="api")[0]
    o2 = c.call("sse__get_status", service="api")[0]
    o3 = c.call("crashy__crashy_job", mode="ok")[0]
    case("compose: a stdio upstream that dies mid-call fails that call; http and sse upstreams keep serving; "
         "the dead one restarts for its next call", r == "FORGE_UPSTREAM_FAILED" and o1 is None and o2 is None
         and o3 is None, (r, o1, o2, o3))
    env.http_proc.kill()
    env.http_proc.wait()
    d1 = c.call("http__get_status", service="api")[0]
    d2 = c.call("local__get_status", service="api")[0]
    d3 = c.call("sse__get_status", service="api")[0]
    case("compose: the http upstream down: its calls fail closed, stdio and sse keep serving",
         d1 == "FORGE_UPSTREAM_FAILED" and d2 is None and d3 is None, (d1, d2, d3))
    restarted = env.restart_http()
    e1 = c.call("http__get_status", service="api")[0]  # new server does not know the old session: 404
    e2 = c.call("http__get_status", service="api")[0]
    case("compose: the http upstream back: one call fails on the stale session, then it serves again",
         restarted and e1 == "FORGE_UPSTREAM_FAILED" and e2 is None, (restarted, e1, e2))
    # listen side: sessions, origin, version, delete, oversized body
    st, _, b = c.post({"jsonrpc": "2.0", "id": 9, "method": "tools/list"}, headers={"mcp-session-id": "forged-123"})
    case("http listen: a wrong Mcp-Session-Id is refused 404", st == 404, st)
    st, _, b = Client(port, session=False).post({"jsonrpc": "2.0", "id": 9, "method": "tools/list"})
    case("http listen: a sessionless client is still served (sessions carry no authority)", st == 200, st)
    st, _, b = c.post({"jsonrpc": "2.0", "id": 9, "method": "tools/list"}, headers={"origin": "http://evil.example"})
    case("http listen: a request with a foreign Origin is refused 403 (DNS rebinding)", st == 403, st)
    st, _, b = c.post({"jsonrpc": "2.0", "id": 9, "method": "tools/list"}, headers={"mcp-protocol-version": "1999-01-01"})
    case("http listen: an unsupported MCP-Protocol-Version is refused 400", st == 400, st)
    sk = socket.create_connection(("127.0.0.1", port), timeout=10)
    sk.sendall(f"POST /mcp HTTP/1.1\r\nhost: 127.0.0.1:{port}\r\ncontent-type: application/json\r\nmcp-session-id: {c.sid}\r\n"
               f"content-length: {2 << 20}\r\n\r\n{{".encode())
    head = sk.recv(200)
    sk.close()
    case("http listen: a declared body over 1 MiB is refused 413 before it is read", b" 413 " in head, head[:40])
    st, _, b = c.post({"jsonrpc": "2.0", "id": 9, "result": {}})
    case("http listen: a client JSON-RPC response (no method) gets 202", st == 202, st)
    st, _, b = c.post(None, raw=b'{"jsonrpc":"2.0","id":1,"method":"tools/list"')
    case("http listen: malformed JSON is refused 400 with a JSON-RPC parse error",
         st == 400 and isinstance(b, dict) and b.get("error", {}).get("code") == -32700, (st, b))
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("DELETE", "/mcp", headers={"mcp-session-id": c.sid})
    st_del = conn.getresponse().status
    conn.close()
    st2, _, _ = c.post({"jsonrpc": "2.0", "id": 9, "method": "tools/list"})
    case("http listen: DELETE ends the session (200), and the ended session is then refused 404",
         st_del == 200 and st2 == 404, (st_del, st2))
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("GET", "/mcp")
    case("http listen: GET /mcp is 405 (no server stream; allowed by the spec)", conn.getresponse().status == 405)
    conn.close()
    plan = A.canary_plan()
    case("canary: the unlisted tools it probes carry namespaced names (local__charge, not charge)",
         "http__drop_database" not in A.route and "local__charge" in plan["unlisted"]
         and "charge" not in plan["unlisted"], plan["unlisted"][:6])
    case("canary: http upstream host:ports are egress targets the agent must not reach",
         f"127.0.0.1:{env.http_port}" in plan["egress_targets"] and f"127.0.0.1:{env.sse_port}" in plan["egress_targets"])
    srv.shutdown()
    srv.server_close()
    for u in A.upstreams.values():
        if getattr(u, "proc", None) is not None:
            u.kill()
        elif hasattr(u, "close"):
            u.close()

    # roles e2e
    J = live(base_doc(env), role="junior")
    srv, port = serve(J)
    c = Client(port)
    names = c.tools()
    case("role e2e: junior sees only its 3 tools", names == ["http__get_status", "local__get_status", "sse__deploy"],
         names)
    before = ledger_rows(env.led["sse"], "deploy")
    got = [c.call("sse__deploy", env="dev", branch="x")[0] for _ in range(2)]
    case("role e2e: junior's cap 1 refuses the 2nd deploy (role log), the base log counts 1",
         got == [None, "FORGE_GATE_REFUSED"] and ledger_rows(env.led["sse"], "deploy") - before == 1
         and J.logs["sse__deploy"].gross() == 1 and J.logs["sse__deploy.role-junior"].gross() == 1, got)
    case("role e2e: a tool of the base policy outside the role is refused FORGE_TOOL_BLOCKED",
         c.call("http__charge", amount=1, invoice_id="J-1")[0] == "FORGE_TOOL_BLOCKED")
    srv.shutdown()
    srv.server_close()
    for u in J.upstreams.values():
        if getattr(u, "proc", None) is not None:
            u.kill()

    # collisions at the airlock start (fail closed: the airlock does not start)
    dd = base_doc(env, namespace=False)
    dd["tools"] = {"allow": [{"tool": "get_status"}]}
    dd.pop("roles")
    try:
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            X = live(dd)
        started = True
        for u in X.upstreams.values():
            if getattr(u, "proc", None) is not None:
                u.kill()
    except SystemExit:
        started = False
    case("compose: without namespace, get_status offered by 4 upstreams and not pinned stops the start (collision)",
         not started and "collision" in buf.getvalue(), buf.getvalue()[-200:])
    real_connect = mcp_client.connect
    mcp_client.connect = lambda spec: (object(), [{"name": "a"}, {"name": "a"}])
    try:
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            live({"version": 1, "upstreams": [{"name": "x", "command": ["true"]}], "tools": {"allow": [{"tool": "a"}]}})
        started = True
    except SystemExit:
        started = False
    finally:
        mcp_client.connect = real_connect
    case("compose: an upstream that lists one tool name twice stops the start (collision)",
         not started and "twice" in buf.getvalue(), buf.getvalue()[-200:])


def run(case):
    env = Env()
    try:
        for sect in (unit_policy, unit_roles, e2e):
            try:
                sect(case, env)
            except Exception as e:  # noqa: BLE001 - a crash is a failed case, not a skipped section
                case(f"{sect.__name__} crashed: {type(e).__name__}: {str(e)[:200]}", False)
    finally:
        for p in CLEAN + [env.http_proc]:
            try:
                p.kill()
                p.wait(5)
            except Exception:  # noqa: BLE001
                pass


if __name__ == "__main__":
    res = []

    def case(name, ok, detail=""):
        if ONLY and ONLY not in name:
            return
        res.append(bool(ok))
        print(("ok    " if ok else "FAIL  ") + name + ("" if ok else f"   {str(detail)[:300]}"), flush=True)
    run(case)
    print(f"{len(res)} cases, {res.count(False)} failed")
    sys.exit(1 if (res.count(False) or not res) else 0)
