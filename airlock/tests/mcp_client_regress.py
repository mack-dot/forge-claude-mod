"""Regression tests for the MCP Streamable HTTP client (engine/mcp_client.py, HttpUpstream).

Two reported bugs:
  R1  after a 404 (the upstream ended our session) only the first thread re-initialized; threads that
      called while it ran sent no session id and failed.
  R2  a pooled keep-alive connection that the upstream closed while it was idle made the next call fail.

A small in-process HTTP/1.1 server plays the upstream. It counts how often each tools/call executed, so
the tests also show that no call ran twice (a gated call must never be repeated by a retry).
Run: python3 airlock/tests/mcp_client_regress.py   (or through airlock/tests/run_tests.py)"""
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


class Upstream:
    """An MCP upstream with sessions. end_sessions() makes every known session unknown (404).
    idle_close_s: the server closes a kept-alive connection that stays idle this long."""

    def __init__(self, idle_close_s=None, init_delay_s=0.0):
        self.sessions, self.calls, self.inits = set(), {}, 0
        self.lock = threading.Lock()
        self.idle_close_s, self.init_delay_s = idle_close_s, init_delay_s
        up = self

        class H(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def setup(self):
                super().setup()
                if up.idle_close_s is not None:
                    self.connection.settimeout(up.idle_close_s)  # idle socket -> handler ends -> close

            def handle(self):
                try:
                    super().handle()
                except (socket.timeout, ConnectionError):
                    pass

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
                with up.lock:
                    up.sessions.discard(self.headers.get("mcp-session-id"))
                self._send(200)

            def do_POST(self):
                msg = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))))
                method, mid, sid = msg.get("method"), msg.get("id"), self.headers.get("mcp-session-id")
                if method == "initialize":
                    time.sleep(up.init_delay_s)
                    with up.lock:
                        up.inits += 1
                        new = "s%d" % up.inits
                        up.sessions.add(new)
                    return self._send(200, {"jsonrpc": "2.0", "id": mid, "result": {
                        "protocolVersion": "2025-06-18", "capabilities": {}, "serverInfo": {"name": "t"}}},
                        {"mcp-session-id": new})
                if sid is None:
                    return self._send(400, {"jsonrpc": "2.0", "id": mid, "error": {"code": -32600,
                                                                                    "message": "no session"}})
                with up.lock:
                    known = sid in up.sessions
                if not known:
                    return self._send(404, {"jsonrpc": "2.0", "id": mid, "error": {"code": -32600,
                                                                                    "message": "unknown session"}})
                if mid is None:
                    return self._send(202)
                if method == "tools/call":
                    key = msg["params"]["arguments"]["k"]
                    with up.lock:
                        up.calls[key] = up.calls.get(key, 0) + 1
                    return self._send(200, {"jsonrpc": "2.0", "id": mid, "result": {
                        "content": [{"type": "text", "text": "ok %s" % key}]}})
                return self._send(200, {"jsonrpc": "2.0", "id": mid, "result": {"tools": []}})

        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.srv.daemon_threads = True
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = "http://127.0.0.1:%d/mcp" % self.srv.server_address[1]

    def end_sessions(self):
        with self.lock:
            self.sessions.clear()

    def stop(self):
        self.srv.shutdown()
        self.srv.server_close()


def _conn_of(item):  # the pool holds a connection, or (connection, release time)
    return item[0] if isinstance(item, tuple) else item


def client(up):
    c = M.HttpUpstream("t", up.url, timeout_s=5)
    c.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "x"}})
    return c


def call(c, k):
    try:
        c.request("tools/call", {"name": "do", "arguments": {"k": k}})
        return "ok"
    except M.UpstreamError as e:
        return str(e)


def cases():
    out = []

    # R1: the session ends; the first call after it fails (by design: not retried), then 8 threads at once.
    up = Upstream(init_delay_s=0.3)  # a slow initialize widens the window in which the bug showed
    try:
        c = client(up)
        up.end_sessions()
        first = call(c, "first")
        res, ths = {}, []
        for i in range(8):
            t = threading.Thread(target=lambda i=i: res.__setitem__(i, call(c, "t%d" % i)))
            ths.append(t)
            t.start()
        for t in ths:
            t.join(20)
        oks = sum(1 for v in res.values() if v == "ok")
        out.append(("mcp client R1: after the upstream ends the session, the call that met the 404 fails (not "
                    "retried)", "404" in first and up.calls.get("first", 0) == 0, first))
        out.append(("mcp client R1: then 8 parallel calls all succeed in ONE new session (got %d/8 ok, %d "
                    "initialize)" % (oks, up.inits), oks == 8 and up.inits == 2,
                    {k: v[:80] for k, v in res.items() if v != "ok"}))
        out.append(("mcp client R1: each of the 8 calls ran exactly once upstream",
                    all(up.calls.get("t%d" % i) == 1 for i in range(8)), up.calls))
    finally:
        up.stop()

    # R1 control: an initialize that fails leaves the client asking for a new one on the next call.
    up = Upstream()
    try:
        c = client(up)
        up.end_sessions()
        call(c, "x")                    # 404, the session is gone
        real = up.url
        c._host, c._port = "127.0.0.1", 9  # nothing listens: the re-initialize fails
        with c._plock:
            c._pool.clear()
        failed = call(c, "y")
        c._host, c._port = "127.0.0.1", int(real.rsplit(":", 1)[1].split("/")[0])
        again = call(c, "z")
        out.append(("mcp client R1: a failed re-initialize is tried again by the next call",
                    failed != "ok" and again == "ok" and up.calls.get("z") == 1, (failed[:80], again[:80])))
    finally:
        up.stop()

    # R2: the upstream closes an idle kept-alive connection; the next call must still work.
    up = Upstream(idle_close_s=0.3)
    try:
        c = client(up)
        before = call(c, "a")
        time.sleep(0.8)                 # the server has closed the pooled connection by now
        after = call(c, "b")
        out.append(("mcp client R2: a call after the upstream closed the idle pooled connection succeeds",
                    before == "ok" and after == "ok", after[:120]))
        out.append(("mcp client R2: and ran exactly once upstream (no blind retry of tools/call)",
                    up.calls.get("b") == 1, up.calls))
        call(c, "c")                    # a live connection back in the pool
        with c._plock:
            pooled_before = len(c._pool)
        time.sleep(0.8)
        listed = "ok"
        try:
            c.request("tools/list", {})
        except M.UpstreamError as e:
            listed = str(e)
        out.append(("mcp client R2: tools/list after an idle close of a pooled connection succeeds too",
                    pooled_before == 1 and listed == "ok", (pooled_before, listed[:120])))
    finally:
        up.stop()

    # R2 control: a connection that is alive is reused (the fix must not drop healthy keep-alive).
    up = Upstream()
    try:
        c = client(up)
        call(c, "p")
        with c._plock:
            pooled = list(c._pool)
        call(c, "q")
        with c._plock:
            again = list(c._pool)
        out.append(("mcp client R2: a healthy pooled connection is reused",
                    len(pooled) == 1 and len(again) == 1 and _conn_of(pooled[0]) is _conn_of(again[0])))
    finally:
        up.stop()
    return out


if __name__ == "__main__":
    bad = 0
    for name, ok, *detail in cases():
        bad += not ok
        print("%s  %s%s" % ("ok  " if ok else "FAIL", name, "" if ok or not detail else "   %s" % (detail[0],)))
    print("%d failed" % bad)
    sys.exit(1 if bad else 0)
