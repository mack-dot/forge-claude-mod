# mcp_client.py - minimal MCP clients for upstream servers on the airlock side.
#
# Label: NATIVE CHECK (hand-written, not proved, not a full MCP implementation).
# Built: JSON-RPC 2.0 over stdio (newline-delimited) and over HTTP POST (a subset of
# MCP "Streamable HTTP": a JSON response, or an SSE body whose first matching 'data:'
# line is read). Not built: sessions (Mcp-Session-Id), server-to-client requests,
# resumable SSE streams, notifications other than notifications/initialized,
# resources, prompts, sampling, cancellation.
import itertools
import json
import os
import subprocess
import threading
import urllib.error
import urllib.request

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


class HttpUpstream:
    def __init__(self, name, url, headers=None, timeout_s=10):
        self.name, self.url, self.timeout = name, url, timeout_s
        self.headers = {k: str(v) for k, v in (headers or {}).items()}  # credentials live only here
        self._ids = itertools.count(1)

    def _post(self, body, timeout):
        h = {"content-type": "application/json", "accept": "application/json, text/event-stream",
             "mcp-protocol-version": PROTOCOL}
        h.update(self.headers)
        req = urllib.request.Request(self.url, data=json.dumps(body, allow_nan=False).encode(), headers=h, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                ctype = r.headers.get("content-type", "")
                raw = r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            raise UpstreamError(f"{self.name}: HTTP {e.code}") from None
        except Exception as e:  # noqa: BLE001 - timeouts, resets, DNS
            raise UpstreamError(f"{self.name}: {type(e).__name__}: {str(e)[:120]}") from None
        if not raw:
            return None
        try:  # F8: a body that does not parse is an UpstreamError (the gate step is finished)
            if ctype.startswith("text/event-stream"):
                for line in raw.split("\n"):
                    line = line.rstrip("\r")
                    if line.startswith("data:"):
                        msg = strict_loads(line[5:])
                        if isinstance(msg, dict) and msg.get("id") == body.get("id"):
                            return msg
                raise UpstreamError(f"{self.name}: SSE body without a matching response")
            msg = strict_loads(raw)
        except (ValueError, RecursionError):
            raise UpstreamError(f"{self.name}: response does not parse") from None
        if not isinstance(msg, dict):
            raise UpstreamError(f"{self.name}: response is not an object")
        return msg

    def notify(self, method, params=None):
        self._post({"jsonrpc": "2.0", "method": method, "params": params or {}}, self.timeout)

    def request(self, method, params=None, timeout=None):
        body = {"jsonrpc": "2.0", "id": next(self._ids), "method": method, "params": params or {}}
        msg = self._post(body, timeout or self.timeout)
        if msg is None:
            raise UpstreamError(f"{self.name}: empty response")
        return _result(self.name, msg)


def connect(spec):
    if spec["transport"] == "stdio":
        up = StdioUpstream(spec["name"], spec["command"], spec.get("env"), spec.get("timeout_s", 10), spec.get("user"))
    else:
        up = HttpUpstream(spec["name"], spec["url"], spec.get("headers"), spec.get("timeout_s", 10))
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
