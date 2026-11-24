# mcp_client.py - minimal MCP clients for upstream servers on the airlock side.
#
# Label: NATIVE CHECK (hand-written, not proved, not a full MCP implementation).
# Built: JSON-RPC 2.0 over stdio (newline-delimited) and MCP Streamable HTTP (spec
# 2025-03-26 / 2025-06-18: POST per message, JSON or SSE answer, Mcp-Session-Id,
# MCP-Protocol-Version, DELETE at exit). Not built: the optional GET server stream,
# server-to-client requests, resumable SSE (Last-Event-ID), notifications other than
# notifications/initialized, resources, prompts, sampling, cancellation, OAuth.
import atexit
import http.client
import itertools
import json
import os
import re
import subprocess
import threading
from urllib.parse import urlsplit

from policy import strict_loads

PROTOCOL = "2025-06-18"


class UpstreamError(Exception):
    pass


class StdioUpstream:
    def __init__(self, name, command, env=None, timeout_s=10, user=None):
        self.name, self.timeout = name, timeout_s
        penv = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
        penv.update({k: str(v) for k, v in (env or {}).items()})  # credentials live only here
        self.proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=None, env=penv, text=True, bufsize=1, user=user)
        self._ids = itertools.count(1)
        self._wlock = threading.Lock()
        self._pending = {}
        self._plock = threading.Lock()
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        # F6: a line that is not a JSON object with a usable id (a log line, a batch, a bare
        # number, NaN) is skipped; the reader never dies while the process lives.
        for line in self.proc.stdout:
            try:
                msg = strict_loads(line)
            except (ValueError, RecursionError):
                continue
            if not isinstance(msg, dict) or "method" in msg:
                continue  # not a response (server-to-client requests are not supported)
            mid = msg.get("id")
            if type(mid) is not int:
                continue
            with self._plock:
                slot = self._pending.pop(mid, None)
            if slot is not None:
                slot[1] = msg
                slot[0].set()
        with self._plock:  # upstream exited: wake every waiter with an error
            for slot in self._pending.values():
                slot[1] = {"error": {"code": -32000, "message": "upstream exited"}}
                slot[0].set()
            self._pending.clear()

    def notify(self, method, params=None):
        with self._wlock:
            self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method, "params": params or {}},
                                             allow_nan=False) + "\n")
            self.proc.stdin.flush()

    def request(self, method, params=None, timeout=None):
        mid = next(self._ids)
        slot = [threading.Event(), None]
        with self._plock:
            self._pending[mid] = slot
        try:
            with self._wlock:
                self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": mid, "method": method,
                                                  "params": params or {}}, allow_nan=False) + "\n")
                self.proc.stdin.flush()
        except (OSError, ValueError) as e:
            raise UpstreamError(f"{self.name}: write failed: {e}") from None
        if not slot[0].wait(timeout or self.timeout):
            with self._plock:
                self._pending.pop(mid, None)
            # F6: a hung upstream would answer later (after we said ABORTED) and block every
            # later call. Kill it first; the next call starts a fresh one (_live_upstream).
            self.kill()
            raise UpstreamError(f"{self.name}: timeout after {timeout or self.timeout}s; upstream killed")
        return _result(self.name, slot[1])

    def kill(self):
        try:
            self.proc.kill()
            self.proc.wait(5)
        except Exception:  # noqa: BLE001
            pass


def _result(name, msg):
    """F8: an error member that is not an object, or a missing result, is an UpstreamError."""
    if "error" in msg:
        err = msg["error"]
        why = err.get("message") if isinstance(err, dict) else err
        raise UpstreamError(f"{name}: {str(why)[:200]}")
    return msg.get("result")


SUPPORTED = ("2025-06-18", "2025-03-26")  # MCP versions with the Streamable HTTP transport
MAX_RESPONSE = int(os.environ.get("FORGE_UPSTREAM_MAX_BODY", str(8 << 20)))  # bytes per upstream answer
MAX_LINE = 1 << 20  # one SSE line
SESSION_RE = re.compile(r"[\x21-\x7e]{1,256}")  # MCP: visible ASCII only


class HttpUpstream:
    """MCP Streamable HTTP client (spec 2025-03-26 / 2025-06-18), on http.client (no
    redirects are followed and no proxy variable is read, so a credential header goes only to
    the configured host). One POST per JSON-RPC message; the answer is application/json or a
    text/event-stream whose events are read until the response with our id. Mcp-Session-Id
    from the initialize answer is sent on every later request; a 404 for a request with a
    session id means the session ended: the call fails (it is NOT retried, a gate step stays
    spent) and the next call starts a new session. The session is ended with DELETE at exit.
    Fail closed: wrong content type, a body over MAX_RESPONSE, an SSE event that is not JSON,
    a response with another id, a changed session id are UpstreamErrors."""
    proc = None

    def __init__(self, name, url, headers=None, timeout_s=10):
        self.name, self.url, self.timeout = name, url, timeout_s
        self.headers = {k: str(v) for k, v in (headers or {}).items()}  # credentials live only here
        u = urlsplit(url)
        self._https = u.scheme == "https"
        self._host, self._port = u.hostname, u.port or (443 if self._https else 80)
        self._path = (u.path or "/") + (f"?{u.query}" if u.query else "")
        self._ids = itertools.count(1)
        self._lock = threading.Lock()  # session state
        self._pool, self._plock = [], threading.Lock()
        self.session, self.version = None, PROTOCOL
        self._need_init = False
        self._init_params = None

    # -- connections
    def _conn(self, timeout):
        with self._plock:
            c = self._pool.pop() if self._pool else None
        if c is None:
            cls = http.client.HTTPSConnection if self._https else http.client.HTTPConnection
            c = cls(self._host, self._port, timeout=timeout)
        else:
            c.timeout = timeout
            if c.sock is not None:
                c.sock.settimeout(timeout)
        return c

    def _release(self, c, reuse):
        if reuse:
            with self._plock:
                if len(self._pool) < 16:
                    self._pool.append(c)
                    return
        c.close()

    def _headers(self, extra=None):
        h = {"content-type": "application/json", "accept": "application/json, text/event-stream",
             "mcp-protocol-version": self.version}
        if self.session is not None:
            h["mcp-session-id"] = self.session
        h.update(self.headers)
        h.update(extra or {})
        return h

    # -- one exchange
    def _post(self, body, timeout):
        data = json.dumps(body, allow_nan=False).encode()
        method = body.get("method")
        sent_session = self.session
        c = self._conn(timeout)
        reuse = False
        try:
            try:
                c.request("POST", self._path, body=data, headers=self._headers())
                r = c.getresponse()
            except (OSError, http.client.HTTPException) as e:  # timeouts, resets, DNS
                raise UpstreamError(f"{self.name}: {type(e).__name__}: {str(e)[:120]}") from None
            sid = r.getheader("mcp-session-id")
            if r.status == 404 and sent_session is not None:
                _drain(r)
                with self._lock:
                    if self.session == sent_session:
                        self.session, self._need_init = None, True
                raise UpstreamError(f"{self.name}: HTTP 404: the upstream ended session; a new session starts "
                                    "with the next call (this call is not retried)")
            if r.status == 202 and "id" not in body:
                _drain(r)
                reuse = not r.will_close
                return None
            if r.status != 200:
                _drain(r)
                raise UpstreamError(f"{self.name}: HTTP {r.status}")
            if method == "initialize":
                if sid is not None and not SESSION_RE.fullmatch(sid):
                    raise UpstreamError(f"{self.name}: Mcp-Session-Id is not visible ASCII (1..256)")
                new_session = sid
            elif sid is not None and sid != sent_session:
                raise UpstreamError(f"{self.name}: the upstream answered with another Mcp-Session-Id")
            ctype = (r.getheader("content-type") or "").split(";")[0].strip().lower()
            if ctype == "application/json":
                raw = r.read(MAX_RESPONSE + 1)
                if len(raw) > MAX_RESPONSE:
                    raise UpstreamError(f"{self.name}: response body over {MAX_RESPONSE} bytes")
                reuse = not r.will_close
                try:  # F8: a body that does not parse is an UpstreamError (the gate step is finished)
                    msg = strict_loads(raw)
                except (ValueError, RecursionError):
                    raise UpstreamError(f"{self.name}: response does not parse") from None
                if not isinstance(msg, dict):
                    raise UpstreamError(f"{self.name}: response is not an object")
                if msg.get("id") != body.get("id") or type(msg.get("id")) is not type(body.get("id")):
                    raise UpstreamError(f"{self.name}: response id does not match the request id")
            elif ctype == "text/event-stream":
                msg = self._sse(r, body.get("id"))  # the stream may stay open: never reused
            else:
                raise UpstreamError(f"{self.name}: unsupported content-type {ctype[:60]!r}")
            if method == "initialize":
                with self._lock:
                    self.session = new_session
            return msg
        finally:
            self._release(c, reuse)

    def _sse(self, r, want_id):
        """Read SSE events until the JSON-RPC response with want_id. Server notifications and
        requests in the stream are skipped (server-to-client requests are not supported)."""
        total, data = 0, []
        while True:
            line = r.readline(MAX_LINE + 1)
            total += len(line)
            if total > MAX_RESPONSE:
                raise UpstreamError(f"{self.name}: SSE stream over {MAX_RESPONSE} bytes without our response")
            if len(line) > MAX_LINE:
                raise UpstreamError(f"{self.name}: SSE line over {MAX_LINE} bytes")
            if not line:
                raise UpstreamError(f"{self.name}: SSE stream ended without a response")
            text = line.decode("utf-8", "replace").rstrip("\r\n")
            if text == "":
                if not data:
                    continue
                payload, data = "\n".join(data), []
                try:
                    msg = strict_loads(payload)
                except (ValueError, RecursionError):
                    raise UpstreamError(f"{self.name}: SSE event data is not JSON") from None
                if not isinstance(msg, dict):
                    raise UpstreamError(f"{self.name}: SSE event is not a JSON-RPC object")
                if "method" in msg:
                    continue  # notification or server request
                if msg.get("id") == want_id and type(msg.get("id")) is type(want_id):
                    return msg
                continue  # a response to another request (not ours)
            if text.startswith(":"):
                continue
            field, _, value = text.partition(":")
            if field == "data":
                data.append(value[1:] if value.startswith(" ") else value)
            elif field not in ("event", "id", "retry"):
                raise UpstreamError(f"{self.name}: malformed SSE line (field {field[:20]!r})")

    # -- MCP
    def _initialize(self, params, timeout):
        msg = self._post({"jsonrpc": "2.0", "id": next(self._ids), "method": "initialize", "params": params}, timeout)
        res = _result(self.name, msg or {"error": {"message": "empty initialize response"}})
        v = res.get("protocolVersion") if isinstance(res, dict) else None
        if v not in SUPPORTED:
            raise UpstreamError(f"{self.name}: upstream protocol version {str(v)[:30]!r} is not one of {SUPPORTED}")
        self.version = v
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}, timeout)
        return res

    def notify(self, method, params=None):
        if method == "notifications/initialized" and self._init_params is not None:
            return  # sent by _initialize
        self._post({"jsonrpc": "2.0", "method": method, "params": params or {}}, self.timeout)

    def request(self, method, params=None, timeout=None):
        timeout = timeout or self.timeout
        if method == "initialize":
            self._init_params = params or {}
            return self._initialize(self._init_params, timeout)
        if self._need_init:
            with self._lock:
                need, self._need_init = self._need_init, False
            if need:
                try:
                    self._initialize(self._init_params or {}, timeout)
                except UpstreamError:
                    self._need_init = True
                    raise
        body = {"jsonrpc": "2.0", "id": next(self._ids), "method": method, "params": params or {}}
        msg = self._post(body, timeout)
        if msg is None:
            raise UpstreamError(f"{self.name}: empty response")
        return _result(self.name, msg)

    def close(self):
        """End the session (DELETE). Errors are ignored (405 = the server keeps no sessions)."""
        if self.session is None:
            return
        try:
            c = self._conn(2)
            c.request("DELETE", self._path, headers=self._headers())
            _drain(c.getresponse())
            c.close()
        except (OSError, http.client.HTTPException):
            pass
        self.session = None


def _drain(r):
    try:
        r.read(1 << 16)
    except (OSError, http.client.HTTPException):
        pass


def connect(spec):
    if spec["transport"] == "stdio":
        up = StdioUpstream(spec["name"], spec["command"], spec.get("env"), spec.get("timeout_s", 10), spec.get("user"))
    else:
        up = HttpUpstream(spec["name"], spec["url"], spec.get("headers"), spec.get("timeout_s", 10))
        atexit.register(up.close)
    up.request("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                              "clientInfo": {"name": "forge-airlock", "version": "0.1"}})
    up.notify("notifications/initialized")
    tools, cursor = [], None
    while True:
        res = up.request("tools/list", {"cursor": cursor} if cursor else {})
        tools.extend(res.get("tools", []))
        cursor = res.get("nextCursor")
        if not cursor:
            break
    return up, tools
