#!/usr/bin/env python3
# mock_streamable_http.py - MOCK upstream MCP server for the Streamable HTTP transport (MCP spec
# 2025-03-26 / 2025-06-18), Python standard library only. No real service, no real key.
#
#   mock_streamable_http.py --port 0 [--sse] [--key K] [--ledger F]   HTTP server; prints "PORT <n>"
#   mock_streamable_http.py --stdio [--ledger F]                       the same tools over stdio
#
# HTTP behaviour: one endpoint /mcp. POST carries one JSON-RPC message. initialize answers with
# an Mcp-Session-Id header; every later request must carry it (400 without, 404 unknown) and the
# negotiated MCP-Protocol-Version (400 otherwise). DELETE /mcp ends the session. GET /mcp gives
# 405 (no server stream). --sse answers requests as text/event-stream (a notification event first,
# then the response pretty-printed over several data: lines); without it, application/json.
# --key K requires "authorization: Bearer K".
#
# Tools: get_status, deploy, drop_database, charge(amount, invoice_id), and job(mode, seconds) whose
# mode plants a protocol fault: ok | slow | crash (the process exits mid-call) | malformed_sse |
# oversize | wrong_session | wrong_id | bad_ctype | expire_session (the server forgets every
# session and answers 404). Every executed tools/call is appended to the ledger (one JSON line)
# BEFORE the fault, so a test can count real side effects. Label: NATIVE CHECK, test quality.
import argparse
import json
import os
import secrets
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VERSIONS = ("2025-06-18", "2025-03-26")


def S(props, req=()):
    return {"type": "object", "properties": {k: {"type": t} for k, t in props.items()}, "required": list(req)}


TOOLS = [
    {"name": "get_status", "description": "Read service status", "inputSchema": S({"service": "string"})},
    {"name": "deploy", "description": "Deploy a branch", "inputSchema": S({"env": "string", "branch": "string"},
                                                                          ["env", "branch"])},
    {"name": "drop_database", "description": "Destroy a database", "inputSchema": S({"name": "string"})},
    {"name": "charge", "description": "Charge an amount in cents",
     "inputSchema": S({"amount": "integer", "invoice_id": "string"}, ["amount", "invoice_id"])},
    {"name": "job", "description": "A job; mode plants a protocol fault",
     "inputSchema": S({"mode": "string", "seconds": "integer"}, ["mode"])},
]
NAMES = {t["name"] for t in TOOLS}
LOCK = threading.Lock()
A = None  # parsed arguments


def record(name, args):
    if not A.ledger:
        return
    with LOCK, open(A.ledger, "a") as f:
        f.write(json.dumps({"tool": name, "args": args, "ts": time.time()}) + "\n")


def answer(mid, method, p):
    """(result | None, error | None, fault | None) for one request."""
    if method == "initialize":
        want = p.get("protocolVersion")
        return {"protocolVersion": want if want in VERSIONS else VERSIONS[0], "capabilities": {"tools": {}},
                "serverInfo": {"name": "mock-streamable-http", "version": "0"}}, None, None
    if method == "ping":
        return {}, None, None
    if method == "tools/list":
        return {"tools": TOOLS}, None, None
    if method == "tools/call" and p.get("name") in NAMES:
        a = p.get("arguments") or {}
        record(p["name"], a)
        mode = a.get("mode", "ok") if p["name"] == "job" else "ok"
        if mode == "crash":
            os._exit(9)
        if mode == "slow":
            time.sleep(int(a.get("seconds", 1)))
        res = {"content": [{"type": "text", "text": f"(mock) {p['name']} done: {json.dumps(a, sort_keys=True)}"}],
               "isError": False}
        return res, None, (mode if mode not in ("ok", "slow") else None)
    return None, {"code": -32601, "message": "unknown method or tool"}, None


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    sessions = {}  # sid -> negotiated version

    def _raw(self, code, body=b"", ctype="application/json", headers=None):
        self.send_response(code)
        self.send_header("content-type", ctype)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, obj, headers=None):
        self._raw(code, json.dumps(obj).encode(), headers=headers)

    def _authed(self):
        return not A.key or self.headers.get("authorization") == f"Bearer {A.key}"

    def do_GET(self):
        self._raw(405, headers={"allow": "POST, DELETE"})

    def do_DELETE(self):
        sid = self.headers.get("mcp-session-id")
        if not self._authed():
            return self._json(401, {"error": "key"})
        if sid is None:
            return self._json(400, {"error": "Mcp-Session-Id required"})
        with LOCK:
            known = H.sessions.pop(sid, None) is not None
        self._raw(200 if known else 404)

    def do_POST(self):
        if not self._authed():
            return self._json(401, {"error": "missing or wrong key"})
        n = int(self.headers.get("content-length") or 0)
        try:
            msg = json.loads(self.rfile.read(n) or b"null")
        except ValueError:
            return self._json(400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse"}})
        if not isinstance(msg, dict):
            return self._json(400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "object"}})
        mid, method, p = msg.get("id"), msg.get("method"), msg.get("params") or {}
        sid = self.headers.get("mcp-session-id")
        hdr = {}
        if method == "initialize":
            sid = secrets.token_hex(16)
            res, err, _ = answer(mid, method, p)
            with LOCK:
                H.sessions[sid] = res["protocolVersion"]
            hdr["mcp-session-id"] = sid
        else:
            if sid is None:
                return self._json(400, {"jsonrpc": "2.0", "id": mid, "error": {"code": -32600,
                                                                               "message": "session required"}})
            with LOCK:
                ver = H.sessions.get(sid)
            if ver is None:
                return self._json(404, {"jsonrpc": "2.0", "id": mid, "error": {"code": -32600,
                                                                               "message": "unknown session"}})
            if self.headers.get("mcp-protocol-version") != ver:
                return self._json(400, {"jsonrpc": "2.0", "id": mid, "error": {"code": -32600,
                                                                               "message": "protocol version"}})
            if mid is None or "method" not in msg:
                return self._raw(202)
            res, err, fault = answer(mid, method, p)
            if fault == "expire_session":
                with LOCK:
                    H.sessions.clear()
                return self._json(404, {"jsonrpc": "2.0", "id": mid, "error": {"code": -32600,
                                                                               "message": "session expired"}})
            if fault == "wrong_session":
                hdr["mcp-session-id"] = "not-" + sid
            if fault == "wrong_id":
                mid = (mid + 1000) if isinstance(mid, int) else "other"
            if fault == "bad_ctype":
                return self._raw(200, json.dumps({"jsonrpc": "2.0", "id": mid, "result": res}).encode(),
                                 ctype="text/html", headers=hdr)
            if fault == "oversize":
                res = dict(res, content=[{"type": "text", "text": "x" * (A.oversize << 10)}])
            if fault == "malformed_sse":
                body = b"event: message\ndata: {\"jsonrpc\": \"2.0\", \"id\": " + str(mid).encode() + b", \"res\n\n"
                return self._raw(200, body, ctype="text/event-stream", headers=hdr)
        out = {"jsonrpc": "2.0", "id": mid}
        out.update({"result": res} if err is None else {"error": err})
        if A.sse:
            note = {"jsonrpc": "2.0", "method": "notifications/message", "params": {"level": "info", "data": "x"}}
            ev = "event: message\ndata: " + json.dumps(note) + "\n\n"
            ev += ": a comment line\nevent: message\nid: 1\n" + "".join(
                "data: " + ln + "\n" for ln in json.dumps(out, indent=1).split("\n")) + "\n"
            return self._raw(200, ev.encode(), ctype="text/event-stream", headers=hdr)
        self._json(200, out, headers=hdr)

    def log_message(self, *a):
        pass


class Srv(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def stdio():
    for line in sys.stdin:
        try:
            m = json.loads(line)
        except ValueError:
            continue
        mid, method, p = m.get("id"), m.get("method"), m.get("params") or {}
        if mid is None:
            continue
        res, err, _fault = answer(mid, method, p)
        out = {"jsonrpc": "2.0", "id": mid}
        out.update({"result": res} if err is None else {"error": err})
        sys.stdout.write(json.dumps(out) + "\n")
        sys.stdout.flush()


def main():
    global A
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--sse", action="store_true")
    ap.add_argument("--stdio", action="store_true")
    ap.add_argument("--key", default=os.environ.get("MOCK_KEY", ""))
    ap.add_argument("--ledger", default=os.environ.get("MOCK_LEDGER", ""))
    ap.add_argument("--oversize", type=int, default=256, help="KiB of text for mode=oversize")
    A = ap.parse_args()
    if A.stdio:
        return stdio()
    srv = Srv(("127.0.0.1", A.port), H)
    print(f"PORT {srv.server_address[1]}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
