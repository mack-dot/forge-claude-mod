# gateway.py - the Forge MCP airlock.
#
# The agent talks MCP (JSON-RPC 2.0 over HTTP POST /mcp, JSON responses) to this
# process only. This process holds every upstream credential, applies forge.yaml
# (default deny), runs bounded-state gates through the compiled #91 verifier, and
# refuses every forwardable call until the startup breach canary has reported PASS.
#
# Labels: PROVED = only the verifier's cap/replay/expiry verdict on one log (about the
# Dafny model). Everything in this file is NATIVE CHECK (hand-written, not proved).
import fcntl
import hashlib
import hmac
import json
import os
import re
import secrets
import signal
import subprocess
import sys
import threading
import time
import unicodedata
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mcp_client  # noqa: E402
import workspace  # noqa: E402
from gatelog import GateLog, GateError  # noqa: E402
from policy import SEP, Policy, PolicyError, Refusal, canonical_json, clip, strict_loads, MONEY_MAX  # noqa: E402

POLICY_FILE = os.environ.get("FORGE_POLICY", "/forge/forge.yaml")
DATA = os.environ.get("FORGE_DATA", "/data")
STATE = os.environ.get("FORGE_STATE", "/state")
SOURCE = os.environ.get("FORGE_SOURCE", "/state/source")
PORT = int(os.environ.get("FORGE_PORT", "8700"))
ERR = -32001  # JSON-RPC server error code used for every FORGE_* refusal
MAX_BODY = 1 << 20
READ_TIMEOUT_S = float(os.environ.get("FORGE_READ_TIMEOUT_S", "30"))  # F1: per socket read
MAX_CONN = int(os.environ.get("FORGE_MAX_CONN", "256"))  # F1: concurrent connections (threads)
TOKEN_HEADER = "x-forge-canary-token"  # F7
MAX_SESSIONS = int(os.environ.get("FORGE_MAX_SESSIONS", "256"))  # listen side, least recently used idle one dropped
SESSION_IDLE_S = float(os.environ.get("FORGE_SESSION_IDLE_S", "1800"))  # S5: idle sessions end (404) after this
SESSIONS_LOCK = threading.Lock()
ALLOWED_ORIGINS = [x for x in os.environ.get("FORGE_ALLOWED_ORIGINS", "").split(",") if x]


def die(msg):
    print(f"[airlock] FATAL (fail closed): {msg}", flush=True)
    sys.exit(2)


def secure_ws_mount(ws):
    """Gap 1 (symlink swap / TOCTOU). The workspace volume is mounted at FORGE_WS_RAW; this
    bind-mounts it at mcp_root with 'nosymfollow', so the kernel follows NO symlink under
    mcp_root for the airlock and for every upstream it starts (an open through a symlink gives
    ELOOP). An agent that swaps a symlink between the airlock's realpath() check and the
    upstream's open() therefore cannot redirect the open: not to a path outside the workspace,
    and hidden paths do not exist on disk at all. Needs CAP_SYS_ADMIN in the airlock container
    (not in the agent). Fail closed: if the mount or its self-test fails, the airlock does not
    start. Label: NATIVE CHECK (relies on the Linux nosymfollow mount flag, kernel 5.10+)."""
    raw = os.environ.get("FORGE_WS_RAW")
    if not raw:
        return "off"
    os.makedirs(ws, exist_ok=True)
    for cmd in (["mount", "--bind", raw, ws], ["mount", "-o", "remount,bind,nosymfollow", ws]):
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            die(f"workspace mount {' '.join(cmd)} failed: {r.stderr.strip()[:200]} (needs CAP_SYS_ADMIN, kernel 5.10+)")
    probe_t, probe_l = os.path.join(ws, ".forge-nsf-target"), os.path.join(ws, ".forge-nsf-link")
    try:
        with open(probe_t, "w") as f:
            f.write("x")
        os.symlink(".forge-nsf-target", probe_l)
        try:
            open(probe_l).close()
            followed = True
        except OSError:
            followed = False
    finally:
        for x in (probe_l, probe_t):
            if os.path.lexists(x):
                os.remove(x)
    if followed:
        die("workspace mount: a symlink was followed under mcp_root; nosymfollow is not in effect")
    print(f"[airlock] workspace: {ws} mounted nosymfollow (symlinks are not followed under it)", flush=True)
    return "nosymfollow"


class Airlock:
    def __init__(self):
        try:
            self.policy = Policy.load(POLICY_FILE, role=os.environ.get("FORGE_ROLE") or None)
        except (PolicyError, OSError) as e:
            die(f"policy: {e}")
        os.makedirs(DATA, exist_ok=True)
        os.chmod(DATA, 0o700)
        # one airlock per data volume: a second writer refuses to start
        self._owner = os.open(os.path.join(DATA, ".airlock.lock"), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(self._owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            die("another airlock holds the data volume lock")
        if os.environ.get("FORGE_DEMO_FIXTURES"):
            import make_source_repo
            make_source_repo.build(os.environ["FORGE_DEMO_FIXTURES"], SOURCE)
        self.ws_mount = secure_ws_mount(self.policy.mcp_root)
        try:
            self.ws = workspace.prepare(self.policy, SOURCE, STATE, self.policy.mcp_root)
        except (workspace.WorkspaceError, OSError) as e:
            die(f"workspace: {e}")
        self.logs = {}
        try:
            for name, (cap, ein, eat) in self.policy.logs.items():
                self.logs[name] = GateLog(DATA, name, cap, ein, eat)
        except (GateError, OSError) as e:
            die(f"gate log: {e}")
        self.connect_upstreams()
        self.llm = None
        if self.policy.llm:
            import llm_proxy
            try:
                self.llm = llm_proxy.LLMGate(self.policy.llm, DATA, os.environ.get("FORGE_LLM_SESSION", "default"))
            except (GateError, OSError, ValueError) as e:
                die(f"llm gate log: {e}")
        self.desk = None
        if self.policy.approval_signers:
            import human_approval
            self.desk = human_approval.Desk(self.policy.approval_signers, self.policy.approval_timeout_s,
                                            os.path.join(STATE, "approvals"))
        self.restart_lock = threading.Lock()
        self.sessions = {}
        self.canary = "PENDING"  # PENDING -> PASSED | BREACH ; one report per airlock lifetime
        self.canary_lock = threading.Lock()
        self.canary_report = None
        self.canary_token = canary_token()
        # F7: fingerprints are salted per workspace build (workspace.json, airlock side only)
        self.fp_salt = bytes.fromhex(self.ws.get("fp_salt", ""))
        print(f"[airlock] up on :{PORT}; role {self.policy.role or '(base policy)'}; "
              f"{len(self.upstreams)} upstreams; {len(self.route)} tools exposed; {len(self.logs)} gate logs; "
              f"{sum(len(t) for t in self.upstream_tools.values())} upstream tools seen; canary PENDING", flush=True)

    def connect_upstreams(self):
        """Start (stdio) or open (Streamable HTTP) every upstream, list its tools and route each
        policy tool to exactly one of them. Fail closed at start: an upstream that does not come
        up, a tool without a name, a name listed twice, or a namespacing collision stops the airlock.
        After the start each upstream fails alone (see _live_upstream)."""
        self.upstreams, self.route, self.upstream_tools = {}, {}, {}
        for name, spec in self.policy.upstreams.items():
            try:
                up, tools = mcp_client.connect(spec)
            except Exception as e:  # noqa: BLE001
                die(f"upstream {name}: {e}")
            self.upstreams[name] = up
            names = [t.get("name") for t in tools if isinstance(t, dict)]
            if len(names) != len(tools) or not all(isinstance(n, str) and n for n in names):
                die(f"upstream {name}: a tool without a string name")
            if len(set(names)) != len(names):
                die(f"upstream {name}: lists one tool name twice (collision)")
            self.upstream_tools[name] = {t["name"]: t for t in tools}
        try:
            self.route = route_tools(self.policy, self.upstream_tools)
        except PolicyError as e:
            die(str(e))

    # ------------------------------------------------------------ MCP methods
    def tools_list(self):
        out = []
        for name in sorted(self.route):
            tp = self.policy.tools[name]
            t = dict(self.upstream_tools[self.route[name]][tp.raw])
            t["name"] = name  # the agent sees the namespaced name (upstream__tool) when namespace: true
            if tp.gated:
                cap = tp.cap if tp.role_cap is None else f"{tp.role_cap} (role {self.policy.role}; base {tp.cap})"
                t["description"] = (t.get("description", "") + f" [forge gate: log={tp.log} unit={tp.unit} "
                                    f"cap={cap}]").strip()
            out.append(t)
        return {"tools": out}

    def tools_call(self, params):
        name, args = params.get("name"), params.get("arguments") or {}
        if self.canary == "BREACH":
            raise Refusal("FORGE_BREACH_LOCKDOWN", "startup canary found a breach; airlock refuses every call")
        tp, fwd = self.policy.check_call(name, args, self._declared(name))  # deny / unlisted / args / paths
        if tp.gated:
            amount = self._amount(tp, args)
            step_id, idv = self._step_id(tp, args)
            if idv is not None:
                fwd[tp.id_field] = args[tp.id_field] = idv  # forward the normalized id (the replay key)
            self._run_checker(tp, name, args)
        if self.canary != "PASSED":
            raise Refusal("FORGE_CANARY_PENDING", "startup canary has not passed; nothing is forwarded yet")
        if tp.human_approval:  # held until a person signs exactly this call (tool, arguments, step id)
            if self.desk is None:
                raise Refusal("FORGE_HUMAN_APPROVAL_REFUSED", "no human_approval.allowed_signers (fail closed)")
            self.desk.hold(name, fwd, step_id.hex() if tp.gated else None)
        up = self._live_upstream(self.route[name])
        call = {"name": tp.raw, "arguments": fwd}  # the upstream's own tool name
        if not tp.gated:
            try:
                return up.request("tools/call", call)
            except mcp_client.UpstreamError as e:
                raise Refusal("FORGE_UPSTREAM_FAILED", str(e)) from None
        meta = {"tool": name, "args_sha256": hashlib.sha256(canonical_json(args).encode()).hexdigest()}
        # A role cap is one more, smaller log in front of the base log: the step must be ACCEPTed
        # by both (the role log first). A refusal by the base log finishes the role step as
        # ABORTED (spent): the error is on the safe side (counted, never lost).
        chain = ([(tp.role_log, tp.role_cap)] if tp.role_log else []) + [(tp.log, tp.cap)]
        held = []  # (log, seq)
        try:
            for ln, cap in chain:
                lg = self.logs[ln]
                verdict, seq, gross = lg.reserve(amount, step_id, meta)
                if verdict != "ACCEPT":
                    raise Refusal("FORGE_GATE_REFUSED", f"gate {ln}: verifier verdict {verdict}",
                                  verdict=verdict, log=ln, gross=gross, cap=cap)
                held.append((lg, seq))
        except BaseException as e:
            for lg, sq in held:
                lg.finish(sq, False, f"refused after reserve: {clip(str(e), 150)}")
            raise
        # F8: every exit from here finishes the step: COMMITTED only for a dict result without
        # isError, ABORTED (still spent) for an error, a timeout or ANY exception.
        ok, note = False, "internal error before the upstream answered"
        try:
            res = up.request("tools/call", call)
            if not isinstance(res, dict):
                raise mcp_client.UpstreamError(f"{name}: upstream result is not an object")
            ok, note = not res.get("isError"), ""
        except mcp_client.UpstreamError as e:
            note = str(e)
            raise Refusal("FORGE_UPSTREAM_FAILED", f"{clip(note, 300)}; the gate step stays spent (seq {seq})",
                          log=tp.log, seq=seq) from None
        except BaseException as e:
            note = f"{type(e).__name__}: {clip(str(e), 150)}"
            raise
        finally:
            for lg, sq in held:
                lg.finish(sq, ok, note)
        res = dict(res)
        res["_meta"] = {"forge": {"log": tp.log, "seq": seq, "gross": gross, "cap": tp.cap, "verdict": verdict}}
        if tp.role_log:
            res["_meta"]["forge"]["role"] = {"role": self.policy.role, "log": tp.role_log, "cap": tp.role_cap}
        return res

    def _declared(self, name):
        """Argument names the upstream's inputSchema lists for this tool (for strict_args)."""
        route = self.route.get(name) if isinstance(name, str) else None
        tp = self.policy.tools.get(name) if route else None
        raw = tp.raw if tp is not None else name
        schema = (self.upstream_tools.get(route, {}).get(raw) or {}).get("inputSchema") if route else None
        props = schema.get("properties") if isinstance(schema, dict) else None
        return set(props) if isinstance(props, dict) else set()

    def _live_upstream(self, name):
        """Gap 2 (crash). A stdio upstream that died (for example mid-call) is started again
        for the NEXT call. The call that was in flight is not retried: its gate step stays
        spent (ABORTED). Fail closed: if the restart fails, this call is refused."""
        up = self.upstreams[name]
        proc = getattr(up, "proc", None)
        if proc is not None and proc.poll() is not None:
            with self.restart_lock:
                up = self.upstreams[name]
                if getattr(up, "proc", None) is not None and up.proc.poll() is not None:
                    print(f"[airlock] upstream {name} exited (code {up.proc.returncode}); restarting", flush=True)
                    try:
                        up, _ = mcp_client.connect(self.policy.upstreams[name])
                    except Exception as e:  # noqa: BLE001
                        raise Refusal("FORGE_UPSTREAM_FAILED", f"upstream {name} restart failed: {e}") from None
                    self.upstreams[name] = up
        return up

    def _amount(self, tp, args):
        if tp.unit == "count":
            amount = 1
        else:
            amount = args.get(tp.value_field)
            if type(amount) is not int or not 0 <= amount <= MONEY_MAX:
                raise Refusal("FORGE_ARG_REFUSED", f"{tp.value_field!r} must be an integer in [0, 2^63-1]")
        if tp.per_call_max is not None and amount > tp.per_call_max:
            raise Refusal("FORGE_GATE_REFUSED", f"{tp.value_field or 'units'}={amount} > per_call_max "
                          f"{tp.per_call_max}", verdict="PER_CALL_MAX", log=tp.log)
        return amount

    def _step_id(self, tp, args):
        """Returns (step_id, normalized id or None). F4: the id must be a string; it is
        NFC-normalized and stripped, then must fully match id_regex; the replay key is the
        normalized form, so '1', ' 1' and 1 cannot be three different steps."""
        if tp.id_field:
            v = args.get(tp.id_field)
            if not isinstance(v, str):
                raise Refusal("FORGE_ARG_REFUSED", f"id field {tp.id_field!r} must be a non-empty string")
            v = unicodedata.normalize("NFC", v).strip()
            if not v or not tp.id_regex.fullmatch(v):
                raise Refusal("FORGE_ARG_REFUSED", f"id field {tp.id_field!r} must fully match "
                              f"/{tp.id_regex.pattern}/ (after NFC and strip)")
            return hashlib.sha256(b"forge-step:" + tp.log.encode() + b":" +
                                  canonical_json(v).encode()).digest()[:16], v
        return hashlib.sha256(b"forge-step-rand:" + os.urandom(32)).digest()[:16], None

    def _run_checker(self, tp, name, args):
        if not tp.checker:
            return
        payload = canonical_json({"tool": name, "arguments": args}).encode()  # allow_nan=False
        try:
            r = subprocess.run(tp.checker["command"], input=payload, capture_output=True,
                               timeout=tp.checker.get("timeout_s", 10), env={"PATH": "/usr/bin:/bin:/usr/local/bin"})
            code = r.returncode
            why = r.stderr.decode(errors="replace")[-200:]
        except (OSError, subprocess.TimeoutExpired) as e:
            code, why = -1, str(e)[:200]
        if code != 0:
            raise Refusal("FORGE_CHECKER_REFUSED", f"checker exit {code}: {why.strip()}", exit_code=code)

    # ------------------------------------------------------------ canary
    def canary_plan(self):
        """Probe plan for the startup canary. Served only while the canary is PENDING."""
        unlisted = sorted({exposed_name(self.policy, u, t) for u, ts in self.upstream_tools.items() for t in ts}
                          - set(self.route))
        reads = [n for n, tp in self.policy.tools.items() if "read" in tp.path_args.values()]
        writes = [n for n, tp in self.policy.tools.items() if "write" in tp.path_args.values()]
        revs = [n for n, tp in self.policy.tools.items() if "git_revpath" in tp.path_args.values()]
        return {
            "agent_root": self.policy.agent_root,
            "fp_salt": self.fp_salt.hex(),
            "fingerprints": self.ws["fingerprints"],
            "secret_fps": [workspace.fp(s.encode(), self.fp_salt) for s in self.policy.secrets() + self._run_tokens()],
            "hidden_names": self.ws["hidden_names"],
            "readonly_files": self.ws.get("readonly_files", [])[:8],
            "closed_dirs": self.ws.get("closed_dirs", [])[:8],
            "airlock_paths": [STATE, SOURCE, DATA, POLICY_FILE, self.policy.mcp_root, "/source", "/ws-raw",
                              os.environ.get("FORGE_DEMO_FIXTURES", "/forge-fixtures")],
            "egress_targets": self.policy.egress_targets + self._upstream_hostports(),
            "egress_proxy_refuse": ["example.com:443", "1.1.1.1:443"] + self._llm_hostport(),
            "egress_proxy": "http://egress:3128" if self.policy.llm else None,
            "deny": self.policy.deny,
            "unlisted": unlisted + ["forge_canary_no_such_tool"],
            "mcp_read": [(n, [k for k, m in self.policy.tools[n].path_args.items() if m == "read"][0]) for n in reads],
            "mcp_write": [(n, [k for k, m in self.policy.tools[n].path_args.items() if m == "write"][0])
                          for n in writes],
            "mcp_rev": [(n, [k for k, m in self.policy.tools[n].path_args.items() if m == "git_revpath"][0])
                        for n in revs],
        }

    def _llm_hostport(self):
        """The model provider is airlock-only: the agent's own CONNECT to it must be refused."""
        c = self.policy.llm
        hp = f"{c['host']}:{c['port']}" if c else None
        return [hp] if hp and hp not in [x.lower() for x in self.policy.egress_allow] else []

    def _run_tokens(self):
        t = os.environ.get("FORGE_EGRESS_TOKEN", "")
        return [t] if len(t) >= 8 else []

    def _upstream_hostports(self):
        out = []
        for u in self.policy.upstreams.values():
            if u["transport"] == "http":
                from urllib.parse import urlsplit
                x = urlsplit(u["url"])
                out.append(f"{x.hostname}:{x.port or (443 if x.scheme == 'https' else 80)}")
        return out

    # ------------------------------------------------------------ listen-side sessions
    def _sessions(self):
        with SESSIONS_LOCK:
            return self.__dict__.setdefault("sessions", {})

    # S5: sid -> [last use (monotonic), requests in flight]. A session idle for SESSION_IDLE_S ends (the
    # client gets 404 and initializes again). When the table is full, the least recently used session with
    # no request in flight is dropped; if every session has a request in flight, no new session is made.
    def _expire(self, sess, now):
        for k in [k for k, (t, n) in sess.items() if n == 0 and now - t > SESSION_IDLE_S]:
            del sess[k]

    def new_session(self):
        sid = secrets.token_urlsafe(24)
        sess = self._sessions()
        with SESSIONS_LOCK:
            now = time.monotonic()
            self._expire(sess, now)
            if len(sess) >= MAX_SESSIONS:
                idle = [k for k, (t, n) in sess.items() if n == 0]
                if not idle:
                    return None
                del sess[min(idle, key=lambda k: sess[k][0])]
            sess[sid] = [now, 0]
        return sid

    def touch_session(self, sid):
        """True if sid is a live session; then one request is in flight on it until release_session(sid)."""
        sess = self._sessions()
        with SESSIONS_LOCK:
            now = time.monotonic()
            e = sess.get(sid)
            if e is None:
                return False
            if e[1] == 0 and now - e[0] > SESSION_IDLE_S:
                del sess[sid]
                return False
            e[0], e[1] = now, e[1] + 1
            return True

    def release_session(self, sid):
        sess = self._sessions()
        with SESSIONS_LOCK:
            e = sess.get(sid)
            if e is not None:
                e[0], e[1] = time.monotonic(), max(0, e[1] - 1)

    def end_session(self, sid):
        sess = self._sessions()
        with SESSIONS_LOCK:
            return sess.pop(sid, None) is not None


def exposed_name(policy, upstream, raw):
    return f"{upstream}{SEP}{raw}" if policy.namespace else raw


def route_tools(policy, upstream_tools):
    """Exposed tool name -> upstream name, for every policy tool. Fail closed (PolicyError):
    a policy tool that no upstream (or more than one) offers, or two upstream tools that would
    be exposed under one name."""
    seen = {}
    for u, ts in upstream_tools.items():
        for t in ts:
            n = exposed_name(policy, u, t)
            if n in seen and policy.namespace:  # without namespace a pinned `upstream:` decides (below)
                raise PolicyError(f"tool name collision: {n!r} is offered by upstreams {seen[n]!r} and {u!r}")
            seen.setdefault(n, u)
    route = {}
    for tp in policy.tools.values():
        owners = [u for u, ts in upstream_tools.items() if tp.raw in ts and (tp.upstream is None or tp.upstream == u)]
        if len(owners) != 1:
            raise PolicyError(f"policy tool {tp.name!r}: offered by {len(owners)} matching upstreams (need exactly 1"
                              + ("" if len(owners) < 2 else "; collision: set namespace: true or pin upstream:") + ")")
        route[tp.name] = owners[0]
    return route


def canary_token():
    """F7: the canary endpoints need this token. forge_airlock.py (or the test harness) sets
    FORGE_CANARY_TOKEN for the airlock and passes it ONLY to the canary process (the agent's
    command runs after `unset`). Without it, a random token is written to STATE/canary.token
    (root, 0600; never mounted in the agent), so nobody in the box can report or read the plan."""
    tok = os.environ.get("FORGE_CANARY_TOKEN", "")
    if len(tok) >= 16:
        return tok
    tok = secrets.token_hex(16)
    try:
        path = os.path.join(STATE, "canary.token")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(tok)
    except OSError:
        pass
    return tok


class BodyError(Exception):
    def __init__(self, status, why):
        super().__init__(why)
        self.status, self.why = status, why


class Handler(BaseHTTPRequestHandler):
    A = None
    timeout = READ_TIMEOUT_S  # F1: every socket read times out (slow-loris)

    def _send(self, code, obj, headers=None):
        body = json.dumps(obj, allow_nan=False).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        """F1: Content-Length is required, decimal and at most 1 MiB; Transfer-Encoding is
        refused. F5: NaN / Infinity / overflowing numbers do not parse."""
        if self.headers.get("transfer-encoding") is not None:
            raise BodyError(400, "transfer-encoding is not supported; send content-length")
        cl = self.headers.get_all("content-length") or []
        if not cl:
            raise BodyError(411, "content-length is required")
        if len(cl) != 1 or not re.fullmatch(r"[0-9]{1,9}", cl[0].strip()):
            raise BodyError(400, "content-length must be one decimal number")
        n = int(cl[0])
        if n > MAX_BODY:
            raise BodyError(413, f"body larger than {MAX_BODY} bytes")
        raw = self.rfile.read(n)
        if len(raw) != n:
            raise BodyError(400, "body shorter than content-length")
        try:
            return strict_loads(raw or b"null")
        except (ValueError, RecursionError):
            raise BodyError(400, "parse error") from None

    def _token_ok(self):
        got = self.headers.get(TOKEN_HEADER) or ""
        return hmac.compare_digest(got.encode("utf-8", "replace"), self.A.canary_token.encode())

    def _read_or_reply(self, rpc):
        try:
            return True, self._body()
        except BodyError as e:
            self.close_connection = True
            if rpc:
                code = -32700 if e.why == "parse error" else -32600
                self._send(e.status, {"jsonrpc": "2.0", "id": None, "error": {"code": code, "message": e.why}})
            else:
                self._send(e.status, {"error": e.why})
        except OSError:  # read timeout or reset: drop the connection
            self.close_connection = True
        return False, None

    def do_GET(self):
        A = self.A
        if self.path == "/healthz":
            return self._send(200, {"ok": True, "canary": A.canary, "ws_mount": A.ws_mount,
                                    "role": A.policy.role})
        if self.path == "/forge/canary/plan":
            if not self._token_ok():
                return self._send(403, {"error": "canary token required"})
            with A.canary_lock:
                if A.canary != "PENDING":
                    return self._send(404, {"error": "canary window closed"})
                return self._send(200, A.canary_plan())
        if self.path == "/mcp":  # no server-to-client SSE stream (MCP Streamable HTTP allows 405)
            self.send_response(405)
            self.send_header("allow", "POST")
            self.send_header("content-length", "0")
            self.end_headers()
            return
        return self._send(404, {"error": "not found"})

    def _empty(self, code, headers=None):
        self.send_response(code)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("content-length", "0")
        self.end_headers()

    def _mcp_refusal(self, rpc_id=None):
        """MCP Streamable HTTP checks before a POST or DELETE /mcp is handled. Returns None
        when the request may go on, else sends the refusal and returns True.
        - Origin (DNS rebinding): a request that carries Origin is refused (403) unless the
          origin is listed in FORGE_ALLOWED_ORIGINS. Programs in the box send no Origin.
        - MCP-Protocol-Version: when present it must be a supported version (400).
        - Mcp-Session-Id: when present it must be a live session of this airlock (404, the
          client then starts a new session with initialize). A request without it is served
          (sessionless clients); sessions carry no authority here: every gate is global."""
        origin = self.headers.get("origin")
        if origin is not None and origin not in ALLOWED_ORIGINS:
            self.close_connection = True
            self._send(403, {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": -32600,
                                                                       "message": "origin not allowed"}})
            return True
        ver = self.headers.get("mcp-protocol-version")
        if ver is not None and ver.strip() not in mcp_client.SUPPORTED:
            self._send(400, {"jsonrpc": "2.0", "id": rpc_id, "error": {
                "code": -32600, "message": f"unsupported MCP-Protocol-Version {clip(ver, 40)}"}})
            return True
        return None

    def do_DELETE(self):  # MCP Streamable HTTP: end a session
        if self.path.split("?")[0] != "/mcp":
            return self._send(404, {"error": "not found"})
        if self._mcp_refusal():
            return
        sid = self.headers.get("mcp-session-id")
        if sid is None:
            return self._send(400, {"error": "Mcp-Session-Id is required to end a session"})
        if not self.A.end_session(sid):
            return self._send(404, {"error": "unknown session"})
        return self._empty(200)

    def do_POST(self):
        A = self.A
        if self.path == "/forge/canary/report":
            if not self._token_ok():
                return self._send(403, {"error": "canary token required"})
            ok_body, rep = self._read_or_reply(False)
            if not ok_body:
                return
            with A.canary_lock:
                if A.canary != "PENDING":
                    return self._send(409, {"error": "canary already reported", "state": A.canary})
                ok = isinstance(rep, dict) and rep.get("result") == "PASS" and not rep.get("breaches")
                A.canary = "PASSED" if ok else "BREACH"
                A.canary_report = rep
            print(f"[airlock] canary report: {A.canary}; breaches={json.dumps((rep or {}).get('breaches'))[:600]}",
                  flush=True)
            return self._send(200, {"state": A.canary})
        if self.path.split("?")[0] != "/mcp":
            return self._send(404, {"error": "not found"})
        if self._mcp_refusal():
            return
        ok_body, msg = self._read_or_reply(True)
        if not ok_body:
            return
        if isinstance(msg, list) or not isinstance(msg, dict):
            return self._send(400, {"jsonrpc": "2.0", "id": None,
                                    "error": {"code": -32600, "message": "batches are not supported"}})
        mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
        pname = clip(params.get("name")) if isinstance(params, dict) else clip(type(params).__name__)
        sid, hdrs, held = self.headers.get("mcp-session-id"), {}, None
        if method == "initialize" and mid is not None:
            new = A.new_session()
            if new is None:
                return self._send(503, {"jsonrpc": "2.0", "id": mid if isinstance(mid, (int, str)) else None,
                                        "error": {"code": -32001, "message": "every session has a request in "
                                                  "flight; try again"}})
            hdrs["mcp-session-id"] = new
        elif sid is not None and not A.touch_session(sid):
            return self._send(404, {"jsonrpc": "2.0", "id": mid if isinstance(mid, (int, str)) else None,
                                    "error": {"code": -32001, "message": "unknown or ended Mcp-Session-Id; "
                                              "send initialize for a new session"}})
        else:
            held = sid
        try:
            return self._mcp(A, msg, mid, method, params, pname, hdrs)
        finally:
            if held is not None:
                A.release_session(held)

    def _mcp(self, A, msg, mid, method, params, pname, hdrs):
        if mid is None or "method" not in msg:  # notification, or a client response: accepted
            return self._empty(202)
        try:
            if method == "initialize":
                want = params.get("protocolVersion") if isinstance(params, dict) else None
                res = {"protocolVersion": want if want in mcp_client.SUPPORTED else mcp_client.PROTOCOL,
                       "capabilities": {"tools": {"listChanged": False}},
                       "serverInfo": {"name": "forge-airlock", "version": "0.2"}}
            elif method == "ping":
                res = {}
            elif method == "tools/list":
                res = A.tools_list()
            elif method == "tools/call":
                res = A.tools_call(params)
            else:
                return self._send(200, {"jsonrpc": "2.0", "id": mid,
                                        "error": {"code": -32601, "message": f"method {clip(method)} not supported"}})
        except Refusal as r:
            print(f"[airlock] refuse {pname}: {r.code} {clip(r.message, 300)}", flush=True)
            return self._send(200, {"jsonrpc": "2.0", "id": mid, "error": {
                "code": ERR, "message": f"{r.code}: {r.message}", "data": dict(r.data, forge_code=r.code)}}, hdrs)
        except Exception as e:  # noqa: BLE001 - fail closed on any internal error
            print(f"[airlock] internal error: {type(e).__name__}: {clip(str(e), 300)}", flush=True)
            return self._send(200, {"jsonrpc": "2.0", "id": mid, "error": {
                "code": ERR, "message": "FORGE_INTERNAL: refused", "data": {"forge_code": "FORGE_INTERNAL"}}}, hdrs)
        return self._send(200, {"jsonrpc": "2.0", "id": mid, "result": res}, hdrs)

    def log_message(self, *a):
        pass


class Server(ThreadingHTTPServer):
    """F1: at most MAX_CONN connections are served at once (one thread each); one more gets
    503 at once and is closed, so held connections cannot grow threads without bound."""
    request_queue_size = 256
    daemon_threads = True

    def __init__(self, *a, max_conn=None, **kw):
        self._slots = threading.BoundedSemaphore(max_conn or MAX_CONN)
        super().__init__(*a, **kw)

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            try:
                request.sendall(b"HTTP/1.1 503 Service Unavailable\r\ncontent-length: 0\r\nconnection: close\r\n\r\n")
            except OSError:
                pass
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


def main():
    signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))  # logs are fsynced per step; exit at once
    Handler.A = Airlock()
    if Handler.A.llm is not None:
        import llm_proxy
        llm_proxy.serve(Handler.A, Handler.A.llm, Handler.A.policy.llm, Handler.A.policy.llm_key,
                        int(os.environ.get("FORGE_LLM_PORT", "8701")))
    Server(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
