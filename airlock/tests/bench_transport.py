#!/usr/bin/env python3
"""Latency of one gated call through the airlock, per upstream transport (no Docker).

Path measured: an MCP client (Streamable HTTP, with a session) -> the airlock's HTTP listen side
(gateway handler, policy check, gate log reserve + finish through the compiled #91 verifier)
-> the upstream -> back. The upstream is mocks/mock_streamable_http.py in three forms with the
same tools: stdio, HTTP with JSON answers, HTTP with SSE answers. Calls are interleaved (one of
each transport per round) so drift hits all three alike. Also an ungated call per transport.
Usage: bench_transport.py [N]   (default 300). Prints one JSON line. Label: FINITE TEST (one
machine, localhost, mock upstreams; not a claim about real servers or networks)."""
import contextlib
import io
import json
import os
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import m22_regress as M  # noqa: E402


def stats(xs):
    xs = sorted(xs)
    return {"n": len(xs), "median_ms": round(statistics.median(xs) * 1000, 3),
            "p95_ms": round(xs[int(round(0.95 * len(xs))) - 1] * 1000, 3)}


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    env = M.Env()
    try:
        doc = M.base_doc(env)
        doc.pop("roles")
        doc["tools"]["allow"] = [{"tool": f"{u}__get_status"} for u in ("local", "http", "sse")]
        doc["tools"]["deny"] = []
        doc["tools"]["gate"] = [{"tool": f"{u}__job", "unit": "count", "cap": 10 ** 9, "expires_in_s": 3600}
                                for u in ("local", "http", "sse")]
        for u in doc["upstreams"]:
            if u["name"] != "crashy":
                u.pop("timeout_s", None)
        for led in env.led.values():
            open(led, "w").close()
        with contextlib.redirect_stdout(io.StringIO()):
            A = M.live(doc)
        srv, port = M.serve(A)
        c = M.Client(port)
        t = {("gated", u): [] for u in ("local", "http", "sse")}
        t.update({("ungated", u): [] for u in ("local", "http", "sse")})
        for i in range(n + 10):
            for u in ("local", "http", "sse"):
                for kind, tool, args in (("gated", f"{u}__job", {"mode": "ok"}), ("ungated", f"{u}__get_status", {})):
                    t0 = time.perf_counter()
                    code, res = c.call(tool, **args)
                    dt = time.perf_counter() - t0
                    if code is not None:
                        raise SystemExit(f"{tool}: {code} {res}")
                    if i >= 10:
                        t[(kind, u)].append(dt)
        names = {"local": "stdio", "http": "http_json", "sse": "http_sse"}
        out = {f"{k}_{names[u]}": stats(v) for (k, u), v in t.items()}
        out["gate_steps"] = {u: A.logs[f"{u}__job"].gross() for u in ("local", "http", "sse")}
        print(json.dumps(out))
        srv.shutdown()
    finally:
        for p in M.CLEAN:
            p.kill()


if __name__ == "__main__":
    main()
