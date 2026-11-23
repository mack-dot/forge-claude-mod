#!/usr/bin/env python3
"""startup_bench.py - container start overhead of each isolation runtime, measured on THIS host.

  python3 airlock/proof/startup_bench.py <image> <runtime>[,<runtime>...] [warm_n] > startup.json

For each runtime: `docker run --rm --runtime <rt> --network none <image> python3 -I -c pass`,
wall time from the host (create + start + the guest's python start + exit + remove).
cold = the FIRST run of that runtime after this script starts (the image is already pulled or
built; for runsc and kata the first sandbox / VM of the runtime); warm = the next warm_n runs
(default 10): median, p95, min. Prints one summary line per runtime on stderr. Exit 1 if any
run fails (no number is reported for a runtime that did not start). Label: FINITE TEST (one host)."""
import json
import statistics
import subprocess
import sys
import time


def once(image, rt):
    t = time.monotonic()
    r = subprocess.run(["docker", "run", "--rm", "--runtime", rt, "--network", "none", image,
                        "python3", "-I", "-c", "pass"], capture_output=True, text=True)
    dt = (time.monotonic() - t) * 1000
    if r.returncode != 0:
        raise SystemExit(f"startup_bench: runtime {rt} failed (exit {r.returncode}): {r.stderr[-400:]}")
    return dt


def main():
    image, rts = sys.argv[1], sys.argv[2].split(",")
    n = int(sys.argv[3]) if len(sys.argv) > 3 else 10
    res = {}
    for rt in rts:
        cold = once(image, rt)
        warm = sorted(once(image, rt) for _ in range(n))
        res[rt] = {"cold_ms": round(cold, 1), "warm_n": n, "warm_median_ms": round(statistics.median(warm), 1),
                   "warm_p95_ms": round(warm[min(n - 1, int(round(0.95 * (n - 1))))], 1), "warm_min_ms": round(warm[0], 1)}
    base = res.get("runc")  # deltas only when runc ran in the same invocation
    for rt, r in res.items():
        extra = ""
        if base and rt != "runc":
            r["warm_median_over_runc_ms"] = round(r["warm_median_ms"] - base["warm_median_ms"], 1)
            r["cold_over_runc_ms"] = round(r["cold_ms"] - base["cold_ms"], 1)
            extra = f"; over runc: cold {r['cold_over_runc_ms']:+.1f} ms, warm median {r['warm_median_over_runc_ms']:+.1f} ms"
        print(f"startup {rt:24s} cold {r['cold_ms']:8.1f} ms  warm median {r['warm_median_ms']:8.1f} ms  "
              f"p95 {r['warm_p95_ms']:8.1f} ms  (n={n}{extra})", file=sys.stderr)
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
