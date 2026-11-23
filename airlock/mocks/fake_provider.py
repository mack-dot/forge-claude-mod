# fake_provider.py - a FAKE model provider for tests (no real credentials, no real model).
# It speaks the shape of the Messages API: POST /v1/messages (JSON or Server-Sent Events with
# message_start / content_block_* / message_delta / message_stop and usage fields), POST
# /v1/messages/count_tokens and GET /v1/models. It checks the x-api-key header against
# EXPECTED_KEY and records, per request, whether the key was right and whether any header
# still carried the agent's dummy value. GET /fake/log returns that record (test harness only;
# the fake runs on the airlock's outside network, which the agent cannot reach).
#
# The LAST user text selects a behaviour (all optional, space separated):
#   FAKE:usage=<in>,<out>[,<cache_write>,<cache_read>]   report this usage
#   FAKE:delay=<seconds>      wait before answering        FAKE:error=<status>  HTTP error
#   FAKE:cut                  stream: close the connection after a few events (no message_stop)
#   FAKE:gap=<seconds>        stream: pause between events (default 0.15)
# Default usage: input = request bytes // 8, output = min(max_tokens, 5).
import json
import os
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

KEY = os.environ.get("EXPECTED_KEY", "")
DUMMY = os.environ.get("DUMMY_KEY", "forge-airlock-no-key")
LOG, LOCK = [], threading.Lock()


def last_text(req):
    for m in reversed(req.get("messages") or []):
        if m.get("role") == "user":
            c = m.get("content")
            if isinstance(c, str):
                return c
            return " ".join(b.get("text", "") for b in c if isinstance(b, dict))
    return ""


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _record(self, path, extra=None):
        hv = [v for _, v in self.headers.items()]
        e = {"t": time.time(), "path": path, "key_ok": bool(KEY) and self.headers.get("x-api-key") == KEY,
             "dummy_seen": any(DUMMY in v for v in hv), "authorization": self.headers.get("authorization") is not None,
             "proxy_auth_seen": self.headers.get("proxy-authorization") is not None}
        e.update(extra or {})
        with LOCK:
            LOG.append(e)
        return e

    def do_GET(self):
        if self.path == "/fake/log":
            with LOCK:
                return self._send(200, LOG)
        if self.path.startswith("/v1/models"):
            e = self._record("/v1/models")
            if not e["key_ok"]:
                return self._send(401, {"type": "error", "error": {"type": "authentication_error", "message": "bad key"}})
            return self._send(200, {"data": [{"id": "fake-model-1", "type": "model"}], "has_more": False})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        n = int(self.headers.get("content-length") or 0)
        raw = self.rfile.read(n)
        try:
            req = json.loads(raw)
        except ValueError:
            req = {}
        path = self.path.split("?")[0]
        text = last_text(req)
        opts = dict(m.groups() for m in re.finditer(r"FAKE:(\w+)(?:=([\w.,]+))?", text))
        e = self._record(path, {"bytes": n, "model": req.get("model"), "max_tokens": req.get("max_tokens"),
                                "stream": bool(req.get("stream")), "opts": opts})
        if not e["key_ok"]:
            return self._send(401, {"type": "error", "error": {"type": "authentication_error",
                                                               "message": "invalid x-api-key"}})
        if path == "/v1/messages/count_tokens":
            return self._send(200, {"input_tokens": n // 8})
        if path != "/v1/messages":
            return self._send(404, {"type": "error", "error": {"type": "not_found_error", "message": path}})
        if "delay" in opts:
            time.sleep(float(opts["delay"]))
        if "error" in opts:
            return self._send(int(opts["error"]), {"type": "error", "error": {"type": "api_error",
                                                                              "message": "fake provider error"}})
        mt = req.get("max_tokens") or 1
        if opts.get("usage"):
            u = [int(x) for x in opts["usage"].split(",")]
            usage = {"input_tokens": u[0], "output_tokens": u[1], "cache_creation_input_tokens": u[2] if len(u) > 2 else 0,
                     "cache_read_input_tokens": u[3] if len(u) > 3 else 0}
        else:
            usage = {"input_tokens": n // 8, "output_tokens": min(mt, 5), "cache_creation_input_tokens": 0,
                     "cache_read_input_tokens": 0}
        msg = {"id": "msg_fake_%d" % int(time.time() * 1e6), "type": "message", "role": "assistant",
               "model": req.get("model"), "content": [{"type": "text", "text": "fake answer"}],
               "stop_reason": "end_turn", "stop_sequence": None, "usage": usage}
        if not req.get("stream"):
            return self._send(200, msg)
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("cache-control", "no-cache")
        self.send_header("transfer-encoding", "chunked")
        self.end_headers()
        gap = float(opts.get("gap", "0.15"))

        def ev(name, data):
            b = f"event: {name}\ndata: {json.dumps(data)}\n\n".encode()
            self.wfile.write(b"%x\r\n%s\r\n" % (len(b), b))
            self.wfile.flush()
            time.sleep(gap)
        start = dict(msg, content=[], stop_reason=None,
                     usage=dict(usage, output_tokens=1))
        ev("message_start", {"type": "message_start", "message": start})
        ev("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}})
        for w in ("fake ", "streamed ", "answer"):
            ev("content_block_delta", {"type": "content_block_delta", "index": 0,
                                       "delta": {"type": "text_delta", "text": w}})
            if "cut" in opts:
                self.close_connection = True
                self.connection.shutdown(2)
                return
        ev("ping", {"type": "ping"})
        ev("content_block_stop", {"type": "content_block_stop", "index": 0})
        ev("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                             "usage": {"output_tokens": usage["output_tokens"]}})
        ev("message_stop", {"type": "message_stop"})
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()


class S(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 256


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9000
    print(f"[fake-provider] up on :{port}; key set: {bool(KEY)}", flush=True)
    S(("0.0.0.0", port), H).serve_forever()
