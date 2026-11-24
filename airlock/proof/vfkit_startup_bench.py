#!/usr/bin/env python3
"""vfkit_startup_bench.py - start overhead of the macOS microvm (vfkit VM) vs a Docker container, THIS Mac.

  python3 airlock/proof/vfkit_startup_bench.py [warm_n] [image] > startup-vfkit.json

Same command in both: `python3 -I -c pass`, wall time from the host until the box is gone.
  docker  `docker run --rm --network none <image> python3 -I -c pass` (default image forge-airlock-proof:local)
  vfkit   vfkit_vm.run(["python3", "-I", "-c", "pass"]): vfkit start, guest kernel boot, boxinit,
          the command, power off (no share, no forward)
first = the first start of that backend in this invocation (Docker's daemon and its VM are already
running; the vfkit image files are in the page cache if they were used before); warm = the next
warm_n (default 10): median, p95, min. No sudo, so no page-cache drop: a true cold start after a
reboot is NOT measured. Exit 1 if any start fails. Label: FINITE TEST (one host)."""
import json
import os
import statistics
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backends"))
import vfkit_vm  # noqa: E402


def docker_once(image):
    t = time.monotonic()
    r = subprocess.run(["docker", "run", "--rm", "--network", "none", image, "python3", "-I", "-c", "pass"],
                       capture_output=True)
    if r.returncode:
        raise SystemExit(f"docker run failed: {r.stderr[-300:]}")
    return (time.monotonic() - t) * 1000


def vfkit_once():
    t = time.monotonic()
    with open(os.devnull, "wb") as null:
        rc = vfkit_vm.run(["python3", "-I", "-c", "pass"], stdout=null)
    if rc != 0:
        raise SystemExit(f"vfkit run failed: exit {rc}")
    return (time.monotonic() - t) * 1000


def stats(first, warm):
    warm = sorted(warm)
    n = len(warm)
    return {"first_ms": round(first, 1), "warm_n": n, "warm_median_ms": round(statistics.median(warm), 1),
            "warm_p95_ms": round(warm[min(n - 1, int(round(0.95 * (n - 1))))], 1), "warm_min_ms": round(warm[0], 1)}


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    image = sys.argv[2] if len(sys.argv) > 2 else "forge-airlock-proof:local"
    res = {}
    for name, fn in (("vfkit", vfkit_once), ("docker", lambda: docker_once(image))):
        first = fn()
        res[name] = stats(first, [fn() for _ in range(n)])
    d, v = res["docker"], res["vfkit"]
    v["warm_median_over_docker_ms"] = round(v["warm_median_ms"] - d["warm_median_ms"], 1)
    v["first_over_docker_ms"] = round(v["first_ms"] - d["first_ms"], 1)
    for k, r in res.items():
        print(f"start {k:7s} first {r['first_ms']:8.1f} ms  warm median {r['warm_median_ms']:8.1f} ms  "
              f"p95 {r['warm_p95_ms']:8.1f} ms  min {r['warm_min_ms']:8.1f} ms (n={n})", file=sys.stderr)
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
