# llm_probe.py - runs INSIDE the agent container against the airlock's model endpoint
# (ANTHROPIC_BASE_URL) and a FAKE provider behind it. One phase per run:
#   basic         no key in env, /proc or files; no direct route to the provider; JSON and SSE
#                 work; settlement uses reported usage; provider error and cut stream keep the
#                 reservation; model, per-request and path refusals; free paths are not charged
#   slow          one request that the provider holds for a long time (the harness kills the
#                 airlock while it is PENDING); prints the reservation first
#   check         EXPECT_GROSS (and EXPECT_ABORTED) hold after a restart; a request larger than
#                 the room left is refused
#   concurrency   20 requests at once; exactly floor(room / R) are forwarded, the rest refused
#   upcheck       for `forge up`: env as expected, one streamed call through the airlock
# The probe never holds the key: it gets only LLM_KEY_SHA256 and LLM_KEY_LEN to look for it.
# Exit 0 = every check passed. Label: FINITE TEST.
import hashlib
import http.client
import json
import os
import re
import socket
import sys
import threading
import time

BASE = os.environ.get("ANTHROPIC_BASE_URL", "")
DUMMY = "forge-airlock-no-key"
MODEL = os.environ.get("LLM_MODEL", "fake-model-1")
FAILS = []


def ok(name, cond, detail=""):
    print(("ok    " if cond else "FAIL  ") + name + ("" if cond else f"   {detail}"), flush=True)
    if not cond:
        FAILS.append(name)


def conn():
    hp = BASE.split("//", 1)[1].rstrip("/")
    h, p = hp.rsplit(":", 1)
    return http.client.HTTPConnection(h, int(p), timeout=120)


def body_of(text, max_tokens=16, stream=False, model=None):
    return json.dumps({"model": model or MODEL, "max_tokens": max_tokens, "stream": stream,
                       "messages": [{"role": "user", "content": text}]}).encode()


def reserved_for(body, max_tokens):  # the airlock's estimate with bytes_per_input_token = 1
    return len(body) + 256 + max_tokens


def post(path, body, auth=True):
    c = conn()
    h = {"content-type": "application/json", "anthropic-version": "2023-06-01"}
    if auth:  # what a client that insists on a key sends: the dummy; the airlock must drop it
        h.update({"x-api-key": DUMMY, "authorization": "Bearer " + DUMMY})
    t0 = time.time()
    c.request("POST", path, body=body, headers=h)
    r = c.getresponse()
    times, data = [], b""
    while True:
        try:
            chunk = r.read1(65536)
        except (http.client.HTTPException, OSError):
            break
        if not chunk:
            break
        times.append(time.time() - t0)
        data += chunk
    c.close()
    return r.status, {k.lower(): v for k, v in r.getheaders()}, data, times


def get(path):
    c = conn()
    c.request("GET", path)
    r = c.getresponse()
    d = r.read()
    c.close()
    return r.status, d


def status():
    return json.loads(get("/forge/llm/status")[1])


def gross():
    return status()["session"]["gross"]


def sse_events(data):
    evs = []
    for block in data.split(b"\n\n"):
        for line in block.split(b"\n"):
            if line.startswith(b"data:"):
                try:
                    evs.append(json.loads(line[5:]))
                except ValueError:
                    pass
    return evs


def key_scan():
    """Look for the key by fingerprint only: environment, every /proc/<pid>/environ and
    cmdline we can read, and every file up to 4 MiB outside /proc, /sys and /dev."""
    want, n = os.environ.get("LLM_KEY_SHA256", ""), int(os.environ.get("LLM_KEY_LEN", "0"))
    if not want or n < 8:
        return ["no fingerprint given"], 0
    hits, scanned = [], [0]

    def scan(blob, where):
        scanned[0] += 1
        for tok in re.split(rb"[^A-Za-z0-9_.-]+", blob):
            if n <= len(tok) <= 256 or len(tok) == n:  # every window of short tokens; exact for long ones
                for i in range(len(tok) - n + 1 if len(tok) <= 256 else 1):
                    if hashlib.sha256(tok[i:i + n]).hexdigest() == want:
                        hits.append(where)
                        return
    scan("\0".join(f"{k}={v}" for k, v in os.environ.items()).encode(), "own environment")
    for pid in os.listdir("/proc"):
        if pid.isdigit():
            for f in ("environ", "cmdline"):
                try:
                    scan(open(f"/proc/{pid}/{f}", "rb").read(), f"/proc/{pid}/{f}")
                except OSError:
                    pass
    for d, dirs, files in os.walk("/", followlinks=False):
        if d == "/":
            dirs[:] = [x for x in dirs if x not in ("proc", "sys", "dev")]
        for f in files:
            p = os.path.join(d, f)
            try:
                if os.path.islink(p) or os.path.getsize(p) > (4 << 20):
                    continue
                scan(open(p, "rb").read(), p)
            except OSError:
                pass
    return hits, scanned[0]


def connect_via_egress(target, token=None):
    try:
        s = socket.create_connection(("egress", 3128), timeout=5)
        extra = f"Proxy-Authorization: Bearer {token}\r\n" if token else ""
        s.sendall(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n{extra}\r\n".encode())
        line = s.recv(64).split(b"\r\n", 1)[0]
        s.close()
        return line.decode(errors="replace")
    except OSError as e:
        return f"error {e}"


# ---------------------------------------------------------------- phases
def phase_basic():
    prov = os.environ.get("LLM_PROVIDER_HOSTPORT", "fakeprov:9000")
    ok("env: ANTHROPIC_BASE_URL points at the airlock", BASE.startswith("http://airlock:"), BASE)
    ok("env: no ANTHROPIC_API_KEY variable", "ANTHROPIC_API_KEY" not in os.environ)
    ok("env: ANTHROPIC_AUTH_TOKEN is only the dummy", os.environ.get("ANTHROPIC_AUTH_TOKEN") == DUMMY)
    t = time.time()
    hits, n = key_scan()
    ok(f"key absent: env, /proc/*/environ+cmdline, {n} files ({time.time() - t:.1f} s)", not hits, hits[:5])
    h, p = prov.rsplit(":", 1)
    try:
        socket.create_connection((h, int(p)), timeout=3).close()
        direct = "connected"
    except OSError as e:
        direct = f"refused ({type(e).__name__})"
    ok(f"no direct route from the agent to the provider {prov}: {direct}", direct != "connected")
    r1, r2 = connect_via_egress(prov), connect_via_egress(prov, "guessed-token-guessed-token-guessed-token-00")
    ok(f"egress proxy refuses the agent's CONNECT to the provider: {r1!r} / with a guessed token {r2!r}",
       " 200 " not in r1 + " " and " 200 " not in r2 + " ")

    g0 = gross()
    b = body_of("hello FAKE:usage=100,7,20,30")
    st, hd, data, _ = post("/v1/messages", b)
    d = json.loads(data)
    g1 = gross()
    ok(f"JSON: 200 through the airlock, dummy key replaced (status {st})", st == 200 and d.get("type") == "message",
       data[:200])
    ok(f"settlement = reported usage 100+7+20+30 = 157 (reserved {hd.get('x-forge-llm-reserved')}, "
       f"charged {g1 - g0})", g1 - g0 == 157)

    b = body_of("stream please FAKE:gap=0.2", max_tokens=50, stream=True)
    st, hd, data, times = post("/v1/messages", b)
    evs = sse_events(data)
    types = [e.get("type") for e in evs]
    u = {}
    for e in evs:
        if e.get("type") == "message_start":
            u.update(e["message"]["usage"])
        if e.get("type") == "message_delta":
            u.update(e["usage"])
    g2 = gross()
    ok(f"SSE: {len(evs)} events, content-type {hd.get('content-type')}, ends with message_stop",
       st == 200 and hd.get("content-type", "").startswith("text/event-stream") and types[-1:] == ["message_stop"],
       types)
    ok(f"SSE is streamed, not buffered: first bytes at {times[0]:.2f} s, last at {times[-1]:.2f} s "
       f"({len(times)} reads)", len(times) >= 4 and times[-1] - times[0] > 1.0)
    rep = sum(u.get(k) or 0 for k in ("input_tokens", "output_tokens", "cache_creation_input_tokens",
                                      "cache_read_input_tokens"))
    ok(f"SSE settlement = reported usage {rep} (charged {g2 - g1}, reserved {hd.get('x-forge-llm-reserved')})",
       g2 - g1 == rep and rep > 0)

    b = body_of("fail FAKE:error=500")
    st, hd, data, _ = post("/v1/messages", b)
    g3 = gross()
    res = int(hd.get("x-forge-llm-reserved", "-1"))
    ok(f"provider error 500 is passed on and the FULL reservation is kept ({res} == {g3 - g2})",
       st == 500 and res == reserved_for(b, 16) and g3 - g2 == res)

    b = body_of("cut FAKE:cut FAKE:gap=0.1", max_tokens=40, stream=True)
    st, hd, data, _ = post("/v1/messages", b)
    g4 = gross()
    res = int(hd.get("x-forge-llm-reserved", "-1"))
    ok(f"cut stream keeps the FULL reservation ({res} == {g4 - g3}) and the agent gets an error event",
       st == 200 and res == reserved_for(b, 40) and g4 - g3 == res and b"FORGE_LLM_STREAM_INCOMPLETE" in data)

    st, _, data, _ = post("/v1/messages", body_of("x", model="claude-not-allowed"))
    ok(f"model not in allowed_models refused ({st}), nothing charged",
       st == 400 and b"FORGE_LLM_MODEL" in data and gross() == g4)
    st, _, data, _ = post("/v1/messages", body_of("x", max_tokens=10 ** 6))
    ok(f"request above per_request_max refused ({st}), nothing charged",
       st == 400 and b"FORGE_LLM_PER_REQUEST_MAX" in data and gross() == g4)
    st, _, data, _ = post("/v1/messages/count_tokens", body_of("count me"))
    st2, d2 = get("/v1/models")
    st3, _, data3, _ = post("/v1/messages/batches", body_of("x"))
    st4, _, data4, _ = post("/v1/complete", body_of("x"))
    st5, _, _, _ = post("/v1/models/../messages", body_of("x"))
    st6, _, _, _ = post("/v1/messages/count_tokens/%2e%2e", body_of("x"))
    st4 = st4 if st5 == 404 and st6 == 404 else -1  # traversal to the gated path must be refused
    ok(f"free paths forwarded uncharged (count_tokens {st}, models {st2}); batches {st3} and other paths "
       f"{st4} refused (also ../ and %2e%2e forms)", st == 200 and st2 == 200 and st3 == 404 and st4 == 404 and gross() == g4)
    s = status()["session"]
    ok(f"session log: proved verdict {s['proved_verdict']}, gross {s['gross']} <= cap {s['cap']}, open {s['open']}",
       s["proved_verdict"] == "ACCEPT" and s["gross"] <= s["cap"] and s["open"] == 0)
    print("RESULT " + json.dumps({"gross": gross()}), flush=True)


def phase_slow():
    b = body_of("hold FAKE:delay=60", max_tokens=100)
    g0 = gross()
    print("SLOW " + json.dumps({"gross_before": g0, "reserved": reserved_for(b, 100)}), flush=True)
    try:
        st, _, _, _ = post("/v1/messages", b)
        print(f"slow request ended with {st}", flush=True)
    except OSError as e:
        print(f"slow request broke: {type(e).__name__}", flush=True)


def phase_check():
    exp = int(os.environ["EXPECT_GROSS"])
    s = status()["session"]
    ok(f"after restart: gross {s['gross']} == expected {exp}, open {s['open']}, verdict {s['proved_verdict']}",
       s["gross"] == exp and s["open"] == 0 and s["proved_verdict"] == "ACCEPT")
    if os.environ.get("EXPECT_ABORTED"):
        n = s["reservations"].get("ABORTED", 0)
        ok(f"after restart: {n} reservations ABORTED (expected {os.environ['EXPECT_ABORTED']})",
           n == int(os.environ["EXPECT_ABORTED"]))
    room = s["cap"] - s["gross"]
    mt = max(room, 1)
    b = body_of("too big for the room left", max_tokens=mt)
    st, _, data, _ = post("/v1/messages", b)
    ok(f"after restart: a request reserving {reserved_for(b, mt)} > room {room} is refused ({st})",
       st == 400 and b"FORGE_LLM_CAP_REACHED" in data and gross() == s["gross"])
    print("RESULT " + json.dumps({"gross": gross()}), flush=True)


def phase_concurrency():
    N, mt = 20, 1000
    tmpl = "concurrent FAKE:usage={:08d},00000010 FAKE:delay=1.5"
    b0 = body_of(tmpl.format(0), max_tokens=mt, stream=True)
    R = reserved_for(b0, mt)
    body = body_of(tmpl.format(R - 10), max_tokens=mt, stream=True)
    assert len(body) == len(b0)
    s = status()["session"]
    room = s["cap"] - s["gross"]
    expect = min(N, room // R)
    results = [None] * N

    def one(i):
        try:
            st, _, data, _ = post("/v1/messages", body)
            results[i] = (st, b"message_stop" in data, b"FORGE_LLM_CAP_REACHED" in data)
        except Exception as e:  # noqa: BLE001
            results[i] = (0, False, False, str(e))
    ts = [threading.Thread(target=one, args=(i,)) for i in range(N)]
    t0 = time.time()
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    took = time.time() - t0
    passed = sum(1 for r in results if r[0] == 200 and r[1])
    refused = sum(1 for r in results if r[0] == 400 and r[2])
    s2 = status()["session"]
    ok(f"{N} concurrent streams: {passed} forwarded, {refused} refused FORGE_LLM_CAP_REACHED; room {room}, "
       f"R {R}, expected {expect} ({took:.1f} s)", passed == expect and refused == N - expect and expect < N, results)
    ok(f"after the burst: gross {s2['gross']} == {s['gross']} + {passed} x {R}, <= cap {s2['cap']}, "
       f"verdict {s2['proved_verdict']}", s2["gross"] == s["gross"] + passed * R and s2["gross"] <= s2["cap"]
       and s2["proved_verdict"] == "ACCEPT" and s2["open"] == 0)
    print("RESULT " + json.dumps({"gross": s2["gross"], "forwarded": passed}), flush=True)


def phase_upcheck():
    ok("up: ANTHROPIC_BASE_URL points at the airlock", BASE.startswith("http://airlock:"), BASE)
    ok("up: no ANTHROPIC_API_KEY, only the dummy ANTHROPIC_AUTH_TOKEN",
       "ANTHROPIC_API_KEY" not in os.environ and os.environ.get("ANTHROPIC_AUTH_TOKEN") == DUMMY)
    hits = [k for k, v in os.environ.items()
            if hashlib.sha256(v.encode()).hexdigest() == os.environ.get("LLM_KEY_SHA256")]
    ok("up: the key is in no environment variable", not hits, hits)
    g0 = gross()
    st, _, data, _ = post("/v1/messages", body_of("from forge up FAKE:usage=11,2", stream=True))
    ok(f"up: streamed call through the airlock ({st}), charged {gross() - g0} == 13",
       st == 200 and b"message_stop" in data and gross() - g0 == 13)


if __name__ == "__main__":
    ph = sys.argv[1] if len(sys.argv) > 1 else "basic"
    {"basic": phase_basic, "slow": phase_slow, "check": phase_check, "concurrency": phase_concurrency,
     "upcheck": phase_upcheck}[ph]()
    print(f"[llm-probe] {ph}: {'PASS' if not FAILS else 'FAIL ' + ', '.join(FAILS)}", flush=True)
    sys.exit(1 if FAILS else 0)
