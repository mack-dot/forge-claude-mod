#!/usr/bin/env python3
"""Host micro-benchmark of one gated call (GateLog.reserve + finish), no Docker.
Usage: FORGE_VERIFIER=subprocess|persistent [FORGE_VERIFIER_MEMO=0|1] bench_gate.py [N]
Prints one JSON line: mode, n, median_ms, p95_ms (after 5 warm-up calls; the log grows to N+6
records). Label: FINITE TEST (one machine)."""
import json
import os
import statistics
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "engine"))
import gatelog  # noqa: E402

n = int(sys.argv[1]) if len(sys.argv) > 1 else 120
d = tempfile.mkdtemp()
log = gatelog.GateLog(d, "charge", 10 ** 9, 3600, None)
xs = []
for i in range(n + 5):
    t = time.perf_counter()
    v, seq, _ = log.reserve(1, os.urandom(16), {"tool": "charge"})
    log.finish(seq, True)
    if v != "ACCEPT":
        raise SystemExit(f"verdict {v}")
    if i >= 5:
        xs.append(time.perf_counter() - t)
xs.sort()
print(json.dumps({"mode": os.environ.get("FORGE_VERIFIER", "persistent"),
                  "memo": os.environ.get("FORGE_VERIFIER_MEMO", "1"), "n": len(xs),
                  "median_ms": round(statistics.median(xs) * 1000, 2),
                  "p95_ms": round(xs[int(round(0.95 * len(xs))) - 1] * 1000, 2)}))
