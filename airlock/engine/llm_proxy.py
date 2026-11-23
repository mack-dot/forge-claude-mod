# llm_proxy.py - the airlock's model endpoint (forge.yaml `llm`) and its spending gate.
#
# The agent gets ANTHROPIC_BASE_URL=http://airlock:8701 and NO key. This process takes each
# request, removes every credential header the agent sent (a dummy value is ignored), adds
# the real key (held only here), and forwards it to llm.provider_url through the egress proxy
# (CONNECT with a per-run token that the agent does not have). Responses go back unchanged,
# including Server-Sent Events streams (chunk by chunk, no buffering).
#
# Spending gate (two phases, fail closed):
#   1. RESERVE before forwarding: amount = max_tokens + input tokens estimated from the
#      request size (in tokens, or in estimated cost units). The compiled #91 verifier checks
#      the log PLUS every open reservation PLUS this one as tentative steps; only on ACCEPT is
#      the reservation journaled PENDING (fsync) and the request forwarded. Else: refused.
#   2. SETTLE after the response: the usage that the provider reported is appended to the
#      log as one step (COMMITTED), again only after the verifier ACCEPTs the new log. When the
#      usage is unknown (provider error, a cut stream, a crash or restart), the FULL
#      reservation is appended instead (ABORTED: the reservation is kept, counted as spent).
#
# Labels:
#   PROVED (about the Dafny model only): the verifier's verdict on each log it is given:
#     gross never exceeds cap, chain, dense seq, no duplicate step id, expiry.
#   NATIVE CHECK (this file, not proved): the request estimate (an upper bound only under the
#     stated assumptions), the reading of usage from the provider's reply, the settlement and
#     the PENDING/COMMITTED/ABORTED journal, restart recovery, the header filter, the proxy.
#   The provider's own count of tokens is TRUSTED (it is what you are billed for).
import hashlib
import http.client
import json
import os
import re
import ssl
import sys
import threading
import time
from fractions import Fraction
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import records as R  # noqa: E402
from gatelog import GateLog, GateError  # noqa: E402

MAX_BODY = 32 << 20
FWD_HEADERS = ("content-type", "accept", "anthropic-version", "anthropic-beta", "user-agent")
DROP_RESP = {"connection", "keep-alive", "transfer-encoding", "content-length", "content-encoding",
             "proxy-authenticate", "proxy-authorization", "te", "trailer", "upgrade", "set-cookie"}
TOKEN_FIELDS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")


def _fsync_append(path, entry):
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(dict(entry, ts=time.time()), sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())


class SpendLog(GateLog):
    """A gate log whose steps are SETTLED model spending. Open reservations live in
    <name>.llm.jsonl (fsynced) and are checked by the verifier as tentative steps."""

    def __init__(self, data_dir, name, cap, expires_in_s):
        self.rjournal = os.path.join(data_dir, f"{name}.llm.jsonl")
        self.open = {}
        super().__init__(data_dir, name, cap, expires_in_s, None)
        with self.locked():
            self._recover()

    # ---------------------------------------------------------- helpers
    def _tentative(self, items):
        recs, gross = list(self.records), self.gross()
        ts = max(int(time.time()), self.last_ts())
        for sid, amt in items:
            gross += amt
            recs.append(R.step(sid, amt, gross, ts, recs[-1], len(recs)))
        return recs, gross

    def _verify(self, recs):
        check = self.wal + ".check"
        with open(check, "wb") as f:
            f.write(b"".join(recs))
        return R.verify_file(check, self.man, int(time.time())).get("verdict", "UNVERIFIABLE_INTERNAL")

    def _append(self, sid, amt, state, note):
        """Append one settled step after the verifier ACCEPTs the new log. If the step would
        pass the cap (actual usage above the reservation), append only the room left, so the
        log stays at the cap, and journal OVERRUN with the true amount. Returns the state."""
        recs, _ = self._tentative([(sid, amt)])
        v = self._verify(recs)
        if v != "ACCEPT":
            room = self.cap - self.gross()
            if amt <= room or room <= 0:
                return None, v
            recs, _ = self._tentative([(sid, room)])
            if self._verify(recs) != "ACCEPT":
                return None, v
            note = f"{note}; OVERRUN: actual {amt} > room {room}; log clamped at the cap; gate now full"
            state, amt = "OVERRUN", room
        self._atomic_write(self.wal, b"".join(recs))
        self.records = recs
        self._journal({"seq": len(recs) - 1, "step_id": sid.hex(), "amount": amt, "state": "COMMITTED",
                       "note": f"llm {state}"})
        return state, note

    def _recover(self):
        last = {}
        if os.path.exists(self.rjournal):
            for line in open(self.rjournal, encoding="utf-8"):
                if line.strip():
                    try:
                        e = json.loads(line)
                    except ValueError:
                        raise GateError(f"{self.name}: llm journal line does not parse; refusing to start") from None
                    last[e["rid"]] = e if e["state"] == "PENDING" else dict(last.get(e["rid"], {}), **e)
        in_log = {rec[48:64].hex() for rec in self.records[1:]}
        n = 0
        for rid, e in last.items():
            if e["state"] not in ("PENDING", "SETTLE_FAILED"):
                continue
            n += 1
            if rid in in_log:
                _fsync_append(self.rjournal, {"rid": rid, "state": "COMMITTED", "amount": e["amount"],
                                              "note": "recovered: step already in the log (crash after append)"})
                continue
            st, note = self._append(bytes.fromhex(rid), e["amount"], "ABORTED",
                                    "PENDING at restart; full reservation kept (usage unknown)")
            if st is None:
                raise GateError(f"{self.name}: cannot settle open reservation {rid} at restart ({note})")
            _fsync_append(self.rjournal, {"rid": rid, "state": st if st == "OVERRUN" else "ABORTED",
                                          "amount": e["amount"], "note": note})
        if n:
            print(f"[airlock] llm log {self.name}: {n} open reservation(s) at restart settled as spent; "
                  f"gross={self.gross()} cap={self.cap}", flush=True)

    # ---------------------------------------------------------- the gate
    def reserve(self, rid, amount, meta):
        with self.locked():
            items = [(bytes.fromhex(r), o["amount"]) for r, o in self.open.items()] + [(rid, amount)]
            recs, total = self._tentative(items)
            v = self._verify(recs)
            if v == "ACCEPT":
                self.open[rid.hex()] = {"amount": amount}
                _fsync_append(self.rjournal, dict(meta, rid=rid.hex(), state="PENDING", amount=amount))
            return v, self.gross(), total - amount

    def release(self, rid, note):
        """Nothing was sent to the provider (another log refused): drop the reservation."""
        with self.locked():
            o = self.open.pop(rid.hex(), None)
            if o is not None:
                _fsync_append(self.rjournal, {"rid": rid.hex(), "state": "RELEASED", "amount": o["amount"],
                                              "note": note[:200]})

    def settle(self, rid, actual, note):
        """actual=None: usage unknown, keep the whole reservation (ABORTED)."""
        with self.locked():
            o = self.open.get(rid.hex())
            if o is None:
                return "UNKNOWN"
            state, amt = ("COMMITTED", actual) if actual is not None else ("ABORTED", o["amount"])
            st, why = self._append(rid, amt, state, note)
            if st is None:  # fail closed: the reservation stays open and counted
                _fsync_append(self.rjournal, {"rid": rid.hex(), "state": "SETTLE_FAILED", "amount": o["amount"],
                                              "note": f"verifier {why}; reservation stays counted"})
                return "SETTLE_FAILED"
            self.open.pop(rid.hex())
            _fsync_append(self.rjournal, {"rid": rid.hex(), "state": st, "amount": o["amount"], "settled": amt
                                          if st != "OVERRUN" else actual, "note": why[:300]})
            return st

    def status(self):
        with self.locked():
            states = {}
            if os.path.exists(self.rjournal):
                last = {}
                for line in open(self.rjournal, encoding="utf-8"):
                    if line.strip():
                        e = json.loads(line)
                        last[e["rid"]] = e["state"]
                for s in last.values():
                    states[s] = states.get(s, 0) + 1
            v = R.verify_file(self.wal, self.man, max(self.last_ts(), 1)).get("verdict")
            return {"log": self.name, "cap": self.cap, "gross": self.gross(), "steps": self.steps(),
                    "open": len(self.open), "open_amount": sum(o["amount"] for o in self.open.values()),
                    "proved_verdict": v, "reservations": states,
                    "wal_sha256": hashlib.sha256(b"".join(self.records)).hexdigest()}


def _day_expiry(now):
    return int(now // 86400 * 86400 + 2 * 86400 - now)  # end of the next UTC day (late settlements)


class LLMGate:
    """The session log (cap per `forge up` run, survives airlock restarts) and, optionally,
    the daily log (UTC day, shared by every run of this repository)."""

    def __init__(self, cfg, data_dir, session):
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,40}", session):
            raise GateError(f"bad llm session id {session!r}")
        self.cfg, self.data = cfg, data_dir
        self.dlock = threading.Lock()
        self.session = SpendLog(data_dir, f"llm-session-{session}", cfg["session_cap"], 30 * 86400)
        self.days = {}
        if cfg["daily_cap"] is not None:
            self.day_log()
        self._recover_others()

    def _recover_others(self):
        """Open reservations of earlier sessions or days (a crash) are settled as spent too."""
        for f in sorted(os.listdir(self.data)):
            if not (f.startswith("llm-") and f.endswith(".llm.jsonl")):
                continue
            name = f[:-len(".llm.jsonl")]
            if name == self.session.name or name in self.days:
                continue
            states = {}
            for line in open(os.path.join(self.data, f), encoding="utf-8"):
                if line.strip():
                    e = json.loads(line)
                    states[e["rid"]] = e["state"]
            if any(s in ("PENDING", "SETTLE_FAILED") for s in states.values()):
                man = open(os.path.join(self.data, name + ".manifest.bin"), "rb").read()
                SpendLog(self.data, name, R.field(man, 160), 86400)

    def day_log(self):
        name = "llm-day-" + time.strftime("%Y%m%d", time.gmtime())
        with self.dlock:
            if name not in self.days:
                self.days[name] = SpendLog(self.data, name, self.cfg["daily_cap"], _day_expiry(time.time()))
            return self.days[name]

    # ---------------------------------------------------------- amounts
    def _price(self, model):
        p = self.cfg["prices"][model]
        return Fraction(str(p["input_per_mtok"])), Fraction(str(p["output_per_mtok"]))

    def estimate(self, req, nbytes):
        """Upper bound of the request's cost IF input tokens <= request bytes / bytes_per_input_token
        and output tokens <= max_tokens (the provider enforces max_tokens). NATIVE CHECK."""
        est_in = -(-nbytes // self.cfg["bytes_per_input_token"]) + 256  # + framing overhead
        if self.cfg["unit"] == "tokens":
            return est_in + req["max_tokens"], est_in
        pin, pout = self._price(req["model"])
        cost = est_in * pin * 2 + req["max_tokens"] * pout  # x2: the worst cache-write price (1 h)
        return -(-cost.numerator // cost.denominator), est_in

    def actual(self, model, usage):
        """Settled amount from the usage the provider reported, or None if it is incomplete."""
        if not isinstance(usage, dict) or type(usage.get("input_tokens")) is not int \
                or type(usage.get("output_tokens")) is not int:
            return None
        u = {k: usage.get(k) or 0 for k in TOKEN_FIELDS}
        if any(type(v) is not int or v < 0 for v in u.values()):
            return None
        if self.cfg["unit"] == "tokens":
            return sum(u.values())
        pin, pout = self._price(model)
        cc = usage.get("cache_creation") if isinstance(usage.get("cache_creation"), dict) else {}
        c5, c1 = cc.get("ephemeral_5m_input_tokens"), cc.get("ephemeral_1h_input_tokens")
        if type(c5) is int and type(c1) is int and c5 + c1 == u["cache_creation_input_tokens"]:
            write = c5 * pin * Fraction(5, 4) + c1 * pin * 2
        else:
            write = u["cache_creation_input_tokens"] * pin * 2
        cost = u["input_tokens"] * pin + write + u["cache_read_input_tokens"] * pin / 10 + u["output_tokens"] * pout
        return -(-cost.numerator // cost.denominator)

    # ---------------------------------------------------------- reserve / settle
    def reserve(self, rid, amount, meta):
        logs = [self.session] + ([self.day_log()] if self.cfg["daily_cap"] is not None else [])
        done = []
        for lg in logs:
            v, gross, open_amt = lg.reserve(rid, amount, meta)
            if v != "ACCEPT":
                for d in done:
                    d.release(rid, f"refused by {lg.name}")
                return None, {"log": lg.name, "verdict": v, "gross": gross, "open": open_amt, "cap": lg.cap}
            done.append(lg)
        return done, None

    def settle(self, logs, rid, actual, note):
        return [lg.settle(rid, actual, note) for lg in logs]

    def status(self):
        out = {"unit": self.cfg["unit"], "session": self.session.status()}
        if self.cfg["daily_cap"] is not None:
            out["day"] = self.day_log().status()
        return out


# ---------------------------------------------------------------- SSE usage reader
class SSEUsage:
    """Reads the Messages API event stream as it passes and keeps the reported usage.
    complete only after message_stop with usage from message_start and message_delta."""

    def __init__(self):
        self.buf, self.event, self.data = b"", None, []
        self.usage, self.started, self.delta, self.stopped, self.error = {}, False, False, False, None

    def feed(self, chunk):
        self.buf += chunk
        while b"\n" in self.buf:
            line, self.buf = self.buf.split(b"\n", 1)
            line = line.rstrip(b"\r")
            if not line:
                self._dispatch()
            elif line.startswith(b"event:"):
                self.event = line[6:].strip().decode(errors="replace")
            elif line.startswith(b"data:"):
                self.data.append(line[5:].strip())

    def _dispatch(self):
        if not self.data:
            self.event = None
            return
        try:
            d = json.loads(b"\n".join(self.data))
        except ValueError:
            d = {}
        t = d.get("type") if isinstance(d, dict) else None
        if t == "message_start":
            u = (d.get("message") or {}).get("usage")
            if isinstance(u, dict):
                self.usage.update({k: v for k, v in u.items() if v is not None})
                self.started = True
        elif t == "message_delta":
            u = d.get("usage")
            if isinstance(u, dict):
                self.usage.update({k: v for k, v in u.items() if v is not None})
                self.delta = True
        elif t == "message_stop":
            self.stopped = True
        elif t == "error" or self.event == "error":
            self.error = d
        self.event, self.data = None, []

    def complete(self):
        return self.started and self.delta and self.stopped and self.error is None


# ---------------------------------------------------------------- HTTP endpoint
def refusal(code, message):
    return {"type": "error", "error": {"type": "invalid_request_error", "message": f"{code}: {message}"},
            "forge_code": code}


class LLMHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    A = None      # the Airlock (canary state)
    G = None      # the LLMGate
    CFG = None
    KEY = None

    def log_message(self, *a):
        pass

    def _json(self, code, obj, extra=None):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _refuse(self, status, code, message):
        print(f"[airlock] llm refuse: {code} {message}", flush=True)
        return self._json(status, refusal(code, message))

    def _path_ok(self, path, free):
        """Only plain paths: no dot segments, no percent-encoding, no empty segments (so that
        /v1/models/../messages cannot reach the gated path ungated)."""
        if not re.fullmatch(r"/v1(?:/[A-Za-z0-9_-][A-Za-z0-9_.-]*)+", path) or "/." in path:
            return False
        return any(path == p or path.startswith(p + "/") for p in free)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/forge/llm/status":
            return self._json(200, self.G.status())
        if self.A.canary != "PASSED":
            return self._refuse(503, "FORGE_CANARY_PENDING", "startup canary has not passed")
        if self._path_ok(path, self.CFG["free_paths"]):
            return self._forward("GET", None, None)
        return self._refuse(404, "FORGE_LLM_PATH", f"path {path!r} is not offered by the airlock")

    def do_POST(self):
        path = self.path.split("?")[0]
        if self.headers.get("transfer-encoding"):
            return self._refuse(411, "FORGE_LLM_BODY", "a content-length body is required")
        try:
            n = int(self.headers.get("content-length") or 0)
        except ValueError:
            n = -1
        if not 0 <= n <= MAX_BODY:
            self.close_connection = True
            return self._refuse(413, "FORGE_LLM_BODY", f"body must be 0..{MAX_BODY} bytes")
        body = self.rfile.read(n)
        if self.A.canary != "PASSED":
            return self._refuse(503, "FORGE_CANARY_PENDING", "startup canary has not passed")
        if path == "/v1/messages":
            return self._messages(body)
        if not self._path_ok(path, ["/v1"]):
            return self._refuse(404, "FORGE_LLM_PATH", "path refused (dot segments or encoding)")
        if self._path_ok(path, self.CFG["free_paths"]) and not path.startswith("/v1/messages/batches"):
            return self._forward("POST", body, None)
        return self._refuse(404, "FORGE_LLM_PATH", f"path {path!r} is not offered by the airlock "
                            "(only /v1/messages is forwarded, and the free paths of llm.free_paths)")

    # ------------------------------------------------------------ gated messages
    def _messages(self, body):
        cfg = self.CFG
        try:
            req = json.loads(body)
        except ValueError:
            return self._refuse(400, "FORGE_LLM_BAD_REQUEST", "body is not JSON")
        if not isinstance(req, dict):
            return self._refuse(400, "FORGE_LLM_BAD_REQUEST", "body must be a JSON object")
        model, mt = req.get("model"), req.get("max_tokens")
        if model not in cfg["allowed_models"]:
            return self._refuse(400, "FORGE_LLM_MODEL", f"model {model!r} is not in llm.allowed_models "
                                f"{cfg['allowed_models']}")
        if type(mt) is not int or mt < 1:
            return self._refuse(400, "FORGE_LLM_BAD_REQUEST", "max_tokens must be a positive integer")
        if "stream" in req and not isinstance(req["stream"], bool):
            return self._refuse(400, "FORGE_LLM_BAD_REQUEST", "stream must be a boolean")
        amount, est_in = self.G.estimate(req, len(body))
        if amount > cfg["per_request_max"]:
            return self._refuse(400, "FORGE_LLM_PER_REQUEST_MAX",
                                f"this request reserves {amount} {cfg['unit']} (max_tokens {mt} + about {est_in} "
                                f"input tokens) > llm.per_request_max {cfg['per_request_max']}; lower max_tokens "
                                "or send less context")
        rid = os.urandom(16)
        meta = {"model": model, "max_tokens": mt, "est_input_tokens": est_in, "stream": bool(req.get("stream")),
                "body_sha256": hashlib.sha256(body).hexdigest()}
        logs, why = self.G.reserve(rid, amount, meta)
        if logs is None:
            return self._refuse(400, "FORGE_LLM_CAP_REACHED",
                                f"spending cap: {why['log']} has {why['gross']} {cfg['unit']} settled + {why['open']} "
                                f"reserved in flight of cap {why['cap']}; this request needs {amount} (verifier verdict "
                                f"{why['verdict']}). Nothing was sent to the model provider.")
        settled = {"done": False}

        def settle(actual, note):
            if not settled["done"]:
                settled["done"] = True
                st = self.G.settle(logs, rid, actual, note)
                print(f"[airlock] llm {rid.hex()[:8]} reserved={amount} actual={actual} -> {st} ({note[:80]})",
                      flush=True)
        try:
            self._forward("POST", body, (model, settle), reserved=amount)
        finally:
            settle(None, "the proxy ended without a usage report")

    # ------------------------------------------------------------ forwarding
    def _conn(self):
        cfg = self.CFG
        egress = os.environ.get("FORGE_LLM_EGRESS", "")
        timeout = float(os.environ.get("FORGE_LLM_TIMEOUT_S", "600"))
        https = cfg["scheme"] == "https"
        ctx = ssl.create_default_context() if https else None
        if egress:
            eh, ep = egress.rsplit(":", 1)
            c = (http.client.HTTPSConnection(eh, int(ep), timeout=timeout, context=ctx) if https
                 else http.client.HTTPConnection(eh, int(ep), timeout=timeout))
            c.set_tunnel(cfg["host"], cfg["port"],
                         headers={"Proxy-Authorization": "Bearer " + os.environ.get("FORGE_EGRESS_TOKEN", "")})
            return c
        if os.environ.get("FORGE_LLM_DIRECT") != "1":
            raise OSError("no egress proxy configured (FORGE_LLM_EGRESS); fail closed")
        return (http.client.HTTPSConnection(cfg["host"], cfg["port"], timeout=timeout, context=ctx) if https
                else http.client.HTTPConnection(cfg["host"], cfg["port"], timeout=timeout))

    def _headers(self, body):
        h = {k: self.headers[k] for k in FWD_HEADERS if self.headers.get(k)}
        h.setdefault("anthropic-version", "2023-06-01")
        h["accept-encoding"] = "identity"  # the airlock reads usage from the reply
        # every credential header from the agent is dropped (a dummy value is ignored); the real key:
        if self.CFG["auth_header"] == "bearer":
            h["authorization"] = "Bearer " + self.KEY
        else:
            h["x-api-key"] = self.KEY
        if body is not None:
            h["content-length"] = str(len(body))
        return h

    def _scrub(self, data):
        k = self.KEY.encode()
        return data.replace(k, b"[forge: key removed]") if k and k in data else data

    def _forward(self, method, body, gated, reserved=None):
        """gated = (model, settle) for /v1/messages; None for a free path."""
        try:
            conn = self._conn()
            conn.request(method, self.path, body=body, headers=self._headers(body))
            resp = conn.getresponse()
        except (OSError, http.client.HTTPException) as e:
            if gated:
                gated[1](None, f"provider unreachable: {type(e).__name__}")
            return self._json(502, {"type": "error", "error": {"type": "api_error", "message":
                              f"FORGE_LLM_UPSTREAM: provider unreachable ({type(e).__name__}); the reservation is kept"
                              if gated else f"FORGE_LLM_UPSTREAM: provider unreachable ({type(e).__name__})"}})
        try:
            ctype = resp.getheader("content-type", "")
            if resp.status == 200 and ctype.startswith("text/event-stream"):
                return self._stream(resp, gated, reserved)
            data = self._scrub(resp.read(MAX_BODY + 1))
            if gated:
                model, settle = gated
                if resp.status == 200:
                    try:
                        usage = json.loads(data).get("usage")
                    except (ValueError, AttributeError):
                        usage = None
                    act = self.G.actual(model, usage)
                    settle(act, "reported usage" if act is not None else "200 without readable usage")
                else:
                    settle(None, f"provider HTTP {resp.status}")
            self.send_response(resp.status)
            for k, v in resp.getheaders():
                if k.lower() not in DROP_RESP:
                    self.send_header(k, v)
            self.send_header("content-length", str(len(data)))
            if reserved is not None:
                self.send_header("x-forge-llm-reserved", str(reserved))
            self.end_headers()
            self.wfile.write(data)
        finally:
            conn.close()

    def _stream(self, resp, gated, reserved=None):
        sse = SSEUsage()
        self.send_response(200)
        for k, v in resp.getheaders():
            if k.lower() not in DROP_RESP:
                self.send_header(k, v)
        self.send_header("transfer-encoding", "chunked")
        if reserved is not None:
            self.send_header("x-forge-llm-reserved", str(reserved))
        self.end_headers()
        client_ok, cut = True, None
        while True:
            try:
                chunk = resp.read1(65536)
            except (OSError, http.client.HTTPException) as e:
                cut = f"stream cut: {type(e).__name__}"
                break
            if not chunk:
                break
            sse.feed(chunk)
            try:
                self.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk))
                self.wfile.flush()
            except OSError:
                client_ok, cut = False, "the agent closed the stream"
                break
        if gated:
            model, settle = gated
            if cut is None and sse.complete():
                act = self.G.actual(model, sse.usage)
                settle(act, "reported usage (stream)" if act is not None else "stream without readable usage")
            else:
                settle(None, cut or ("stream error event" if sse.error else "stream ended before message_stop"))
        if client_ok:
            try:
                if cut is not None or not sse.complete():
                    ev = (b"event: error\ndata: " + json.dumps({"type": "error", "error": {
                        "type": "api_error", "message": "FORGE_LLM_STREAM_INCOMPLETE: the provider stream ended "
                        "without a usage report; the full reservation is kept"}}).encode() + b"\n\n")
                    if sse.error is None:
                        self.wfile.write(b"%x\r\n%s\r\n" % (len(ev), ev))
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
            except OSError:
                pass
        self.close_connection = True


class LLMServer(ThreadingHTTPServer):
    request_queue_size = 256
    daemon_threads = True


def serve(airlock, gate, cfg, key, port):
    LLMHandler.A, LLMHandler.G, LLMHandler.CFG, LLMHandler.KEY = airlock, gate, cfg, key
    srv = LLMServer(("0.0.0.0", port), LLMHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    print(f"[airlock] llm endpoint up on :{port}; provider {cfg['scheme']}://{cfg['host']}:{cfg['port']}; "
          f"unit={cfg['unit']} session_cap={cfg['session_cap']} daily_cap={cfg['daily_cap']} "
          f"per_request_max={cfg['per_request_max']}", flush=True)
    return srv

