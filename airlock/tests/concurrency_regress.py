"""Regression tests for concurrency and connection-lifecycle bugs found in review (after R1/R2).

  C1  HttpUpstream read the session id twice per request (once for the 404 check, once for the header). A
      parallel 404 between the two reads sent a request with NO session id (the R1 symptom, in a smaller window).
  C2  HttpUpstream let a cut or stalled answer body escape as IncompleteRead / TimeoutError instead of
      UpstreamError (the gateway then answered FORGE_INTERNAL, not FORGE_UPSTREAM_FAILED).
  C3  The model endpoint (llm_proxy) answered a Transfer-Encoding request with 411 but kept the HTTP/1.1
      connection open with the body unread: the body bytes were parsed as the next request.
  C4  The model endpoint had no socket read timeout and no connection cap (the MCP endpoint has both, F1):
      idle or slow connections held one thread each without bound, in the process that also runs the gates.
  S1  stdio reader: no line-length limit (HTTP has MAX_RESPONSE). S2  stdio: a failed stdin write left its
      waiting slot. S3  HTTP: a failed notifications/initialized left the new session open upstream.
  S4  LLMGate.settle: an OSError on the session log skipped the day log. S5  listen-side sessions: no idle
      expiry, and LRU eviction could drop a session with a request in flight.

In-process servers only (stdlib). Run: python3 airlock/tests/concurrency_regress.py"""
import http.server
import json
import os
import socket
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "engine"))
import mcp_client as M  # noqa: E402
import llm_proxy as L  # noqa: E402

INIT = {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "x"}}


class Up:
    """MCP upstream with one session. mode: 'ok', 'cut' (content-length larger than the body, then close),
    'stall' (SSE headers, then silence), 'jstall' (JSON headers and part of the body, then silence)."""

    def __init__(self):
        self.mode, self.no_sid, self.lock = "ok", [], threading.Lock()
        up = self

        class H(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _send(self, code, obj, headers=None):
                body = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                msg = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))))
                method, mid, sid = msg.get("method"), msg.get("id"), self.headers.get("mcp-session-id")
                if method == "initialize":
                    return self._send(200, {"jsonrpc": "2.0", "id": mid, "result": {
                        "protocolVersion": "2025-06-18", "capabilities": {}}}, {"mcp-session-id": "s1"})
                if sid is None:
                    with up.lock:
                        up.no_sid.append(method)
                    return self._send(400, {"jsonrpc": "2.0", "id": mid, "error": {"code": -1, "message": "no sid"}})
                if mid is None:
                    self.send_response(202)
                    self.send_header("content-length", "0")
                    self.end_headers()
                    return
                if up.mode == "cut":
                    self.send_response(200)
                    self.send_header("content-type", "application/json")
                    self.send_header("content-length", "1000")
                    self.end_headers()
                    self.wfile.write(b'{"jsonrpc": "2.0", "id": ')
                    self.wfile.flush()
                    self.close_connection = True
                    return
                if up.mode == "jstall":
                    self.send_response(200)
                    self.send_header("content-type", "application/json")
                    self.send_header("content-length", "1000")
                    self.end_headers()
                    self.wfile.write(b'{"jsonrpc": "2.0", "id": ')
                    self.wfile.flush()
                    time.sleep(2.0)
                    self.close_connection = True
                    return
                if up.mode == "stall":
                    self.send_response(200)
                    self.send_header("content-type", "text/event-stream")
                    self.end_headers()
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    time.sleep(2.0)
                    self.close_connection = True
                    return
                return self._send(200, {"jsonrpc": "2.0", "id": mid, "result": {"content": []}})

        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.srv.daemon_threads = True
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = "http://127.0.0.1:%d/mcp" % self.srv.server_address[1]

    def stop(self):
        self.srv.shutdown()
        self.srv.server_close()


def _err(fn):
    try:
        fn()
        return "no error"
    except M.UpstreamError as e:
        return "UpstreamError: " + str(e)[:100]
    except Exception as e:  # noqa: BLE001
        return "ESCAPED %s: %s" % (type(e).__name__, str(e)[:100])


def case_c1():
    """Force the interleaving: a parallel thread's 404 handling runs while this request opens its connection."""
    up = Up()
    try:
        c = M.HttpUpstream("t", up.url, timeout_s=3)
        c.request("initialize", INIT)
        orig = c._conn
        fired = []

        def racing_conn(timeout):
            if not fired:
                fired.append(1)
                with c._lock:  # what _post does in another thread on a 404 for the same session
                    if c.session == "s1":
                        c.session, c._need_init = None, True
            return orig(timeout)

        c._conn = racing_conn
        got = _err(lambda: c.request("tools/call", {"name": "do", "arguments": {}}))
        c._conn = orig
        after = _err(lambda: c.request("tools/call", {"name": "do", "arguments": {}}))
        return [("C1 http client: a request is never sent without the session id it was checked against "
                 "(parallel 404 during the send)", up.no_sid == [], (up.no_sid, got)),
                ("C1 http client: the next call re-initializes and succeeds", after == "no error", after)]
    finally:
        up.stop()


def case_c2():
    out = []
    up = Up()
    try:
        c = M.HttpUpstream("t", up.url, timeout_s=0.5)
        c.request("initialize", INIT)
        up.mode = "cut"
        got = _err(lambda: c.request("tools/call", {"name": "do", "arguments": {}}))
        out.append(("C2 http client: an answer body cut short (connection closed) is an UpstreamError",
                    got.startswith("UpstreamError"), got))
        up.mode = "jstall"
        t0 = time.monotonic()
        got = _err(lambda: c.request("tools/call", {"name": "do", "arguments": {}}))
        out.append(("C2 http client: a JSON answer body that stalls past the timeout is an UpstreamError",
                    got.startswith("UpstreamError") and time.monotonic() - t0 < 1.8, got))
        up.mode = "stall"
        t0 = time.monotonic()
        got = _err(lambda: c.request("tools/call", {"name": "do", "arguments": {}}))
        out.append(("C2 http client: an SSE answer that stalls past the timeout is an UpstreamError",
                    got.startswith("UpstreamError") and time.monotonic() - t0 < 1.8, got))
    finally:
        up.stop()
    return out


class _A:
    canary = "PENDING"


def _llm_server(**kw):
    L.LLMHandler.A, L.LLMHandler.G, L.LLMHandler.CFG, L.LLMHandler.KEY = _A(), None, {"free_paths": []}, "k"
    srv = L.LLMServer(("127.0.0.1", 0), L.LLMHandler, **kw)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _recv_all(s, wait):
    s.settimeout(wait)
    data = b""
    try:
        while True:
            b = s.recv(65536)
            if not b:
                break
            data += b
    except socket.timeout:
        pass
    return data


def case_c3():
    srv = _llm_server()
    try:
        s = socket.create_connection(srv.server_address, timeout=3)
        smuggled = b"GET /v1/models HTTP/1.1\r\nhost: x\r\n\r\n"  # the agent's bytes, sent as a "body"
        s.sendall(b"POST /v1/messages HTTP/1.1\r\nhost: x\r\ntransfer-encoding: identity\r\n\r\n" + smuggled)
        data = _recv_all(s, 1.0)
        s.close()
        n = data.count(b"HTTP/1.")
        return [("C3 llm endpoint: a refused Transfer-Encoding request closes the connection (its body is not "
                 "parsed as further requests)", n == 1 and b"FORGE_LLM_BODY" in data, data[:300])]
    finally:
        srv.shutdown()
        srv.server_close()


def case_c4():
    out = []
    t = getattr(L.LLMHandler, "timeout", None)
    out.append(("C4 llm endpoint: socket reads have a timeout (slow-loris)", t is not None, t))
    if t is not None:
        old = L.LLMHandler.timeout
        L.LLMHandler.timeout = 0.4
        srv = _llm_server()
        try:
            s = socket.create_connection(srv.server_address, timeout=3)
            s.sendall(b"POST /v1/messages HTTP/1.1\r\nhost: x\r\ncontent-length: 10\r\n\r\nab")  # 2 of 10 bytes
            t0 = time.monotonic()
            data = _recv_all(s, 2.0)
            closed = time.monotonic() - t0 < 1.9
            s.close()
            out.append(("C4 llm endpoint: a connection that stops sending is closed after the timeout",
                        closed, data[:120]))
        finally:
            L.LLMHandler.timeout = old
            srv.shutdown()
            srv.server_close()
    try:
        srv = _llm_server(max_conn=2)
    except TypeError as e:
        out.append(("C4 llm endpoint: connections are capped (one more gets 503)", False, str(e)))
        return out
    try:
        held = [socket.create_connection(srv.server_address, timeout=3) for _ in range(2)]
        time.sleep(0.2)
        extra = socket.create_connection(srv.server_address, timeout=3)
        data = _recv_all(extra, 1.0)
        out.append(("C4 llm endpoint: connections are capped (one more gets 503)", data.startswith(b"HTTP/1.1 503"),
                    data[:80]))
        for s in held + [extra]:
            s.close()
    finally:
        srv.shutdown()
        srv.server_close()
    return out


# ---------------------------------------------------------------- S1 to S5 (the review's suspicions, now tested)
STDIO_UP = r"""
import json, sys
big = int(sys.argv[1])
for line in sys.stdin:
    m = json.loads(line)
    if "id" not in m:
        continue
    if m["method"] == "initialize":
        r = {"protocolVersion": "2025-06-18"}
    elif m["method"] == "tools/list":
        r = {"tools": []}
    elif m["params"]["arguments"].get("big"):
        r = {"content": [{"type": "text", "text": "x" * big}]}
    else:
        r = {"content": []}
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": m["id"], "result": r}) + "\n")
    sys.stdout.flush()
"""


def _stdio(tmp, big):
    path = os.path.join(tmp, "stdio_up.py")
    with open(path, "w") as f:
        f.write(STDIO_UP)
    up, _ = M.connect({"transport": "stdio", "name": "s", "command": [sys.executable, "-I", path, str(big)],
                       "timeout_s": 5})
    return up


def case_s1():
    import tempfile
    tmp = tempfile.mkdtemp()
    old = M.MAX_RESPONSE
    M.MAX_RESPONSE = 1 << 16  # 64 KiB for the test; the default is 8 MiB
    try:
        up = _stdio(tmp, 1 << 18)  # a 256 KiB answer line
        got = _err(lambda: up.request("tools/call", {"name": "x", "arguments": {"big": True}}))
        time.sleep(0.3)
        dead = up.proc.poll() is not None
        up.kill()
        return [("S1 stdio client: an answer line over MAX_RESPONSE is an UpstreamError and the upstream is "
                 "stopped (restarted by the next call, like a crash)", got.startswith("UpstreamError") and dead,
                 (got, dead))]
    finally:
        M.MAX_RESPONSE = old


def case_s2():
    import tempfile
    up = _stdio(tempfile.mkdtemp(), 10)
    up.kill()
    time.sleep(0.2)
    got = [_err(lambda: up.request("tools/call", {"name": "x", "arguments": {}})) for _ in range(20)]
    with up._plock:
        left = len(up._pending)
    return [("S2 stdio client: 20 failed writes to a dead upstream are UpstreamErrors and leave no waiting slot",
             left == 0 and all(g.startswith("UpstreamError") for g in got), (left, got[0]))]


class InitUp:
    """MCP upstream that answers initialize with a session, rejects notifications/initialized (500) and records
    every DELETE."""

    def __init__(self):
        self.deletes, self.inits = [], 0
        up = self

        class H(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _send(self, code, obj=None, headers=None):
                body = b"" if obj is None else json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)

            def do_DELETE(self):
                up.deletes.append(self.headers.get("mcp-session-id"))
                self._send(200)

            def do_POST(self):
                msg = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))))
                if msg.get("method") == "initialize":
                    up.inits += 1
                    return self._send(200, {"jsonrpc": "2.0", "id": msg["id"], "result": {
                        "protocolVersion": "2025-06-18"}}, {"mcp-session-id": "s%d" % up.inits})
                return self._send(500, {"error": "no"})

        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.srv.daemon_threads = True
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = "http://127.0.0.1:%d/mcp" % self.srv.server_address[1]


def case_s3():
    up = InitUp()
    try:
        c = M.HttpUpstream("t", up.url, timeout_s=2)
        first = _err(lambda: c.request("initialize", INIT))
        return [("S3 http client: a failed notifications/initialized ends the new session (DELETE) and leaves "
                 "no session id set", first.startswith("UpstreamError") and up.deletes == ["s1"]
                 and c.session is None, (first, up.deletes, c.session))]
    finally:
        up.srv.shutdown()
        up.srv.server_close()


def case_s4():
    import tempfile
    d = tempfile.mkdtemp()
    g = L.LLMGate({"session_cap": 1000, "daily_cap": 1000, "unit": "tokens"}, d, "s4")
    rid = os.urandom(16)
    logs, why = g.reserve(rid, 100, {})
    day = logs[1]

    def broken(*a, **k):
        raise OSError("injected: disk full")

    g.session.settle = broken
    try:
        st = g.settle(logs, rid, 10, "usage")
        raised = None
    except OSError as e:
        st, raised = None, str(e)
    del g.session.settle
    day_open, sess_open = rid.hex() in day.open, rid.hex() in g.session.open
    g2 = L.LLMGate({"session_cap": 1000, "daily_cap": 1000, "unit": "tokens"}, d, "s4")  # restart
    return [("S4 llm gate: an OSError while settling the session log still settles the day log; the session "
             "reservation stays counted", not day_open and sess_open and day.gross() == 10, (st, raised, day_open,
                                                                                            day.gross())),
            ("S4 llm gate: after a restart the session reservation is settled as spent (never undercounted)",
             g2.session.gross() == 100 and not g2.session.open, g2.session.gross())]


def case_s5():
    import gateway as G
    out = []
    old = (G.MAX_SESSIONS, getattr(G, "SESSION_IDLE_S", None))
    try:
        A = G.Airlock.__new__(G.Airlock)
        G.SESSION_IDLE_S = 0.3
        s = A.new_session()
        time.sleep(0.5)
        out.append(("S5 listen sessions: a session idle past FORGE_SESSION_IDLE_S is ended (404)",
                    not A.touch_session(s)))
        G.SESSION_IDLE_S = 3600
        rel = getattr(A, "release_session", lambda sid: None)
        A = G.Airlock.__new__(G.Airlock)
        G.MAX_SESSIONS = 2
        s1 = A.new_session()
        s2 = A.new_session()
        A.touch_session(s1)              # a long request on s1 starts (in flight) ...
        time.sleep(0.01)
        A.touch_session(s2)
        rel(s2)                          # ... s2 is used and done, so s1 is the least recently used
        A.new_session()                  # the table is full: one must go
        alive = A.touch_session(s1)
        out.append(("S5 listen sessions: eviction never drops a session with a request in flight", alive))
        rel(s1)
        rel(s1)
        A = G.Airlock.__new__(G.Airlock)
        a, b = A.new_session(), A.new_session()
        A.touch_session(a)
        A.touch_session(b)
        out.append(("S5 listen sessions: when every session is in flight, a new session is refused (None)",
                    A.new_session() is None))
    finally:
        G.MAX_SESSIONS = old[0]
        if old[1] is not None:
            G.SESSION_IDLE_S = old[1]
    return out


def cases():
    out = []
    for f in (case_c1, case_c2, case_c3, case_c4, case_s1, case_s2, case_s3, case_s4, case_s5):
        try:
            out.extend(f())
        except Exception as e:  # noqa: BLE001
            out.append(("%s crashed: %s: %s" % (f.__name__, type(e).__name__, e), False, ""))
    return out


if __name__ == "__main__":
    bad = 0
    for name, ok, *detail in cases():
        bad += not ok
        print("%s  %s%s" % ("ok  " if ok else "FAIL", name, "" if ok or not detail else "   %s" % (detail[0],)))
    print("%d failed" % bad)
    sys.exit(1 if bad else 0)
