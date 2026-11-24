#!/usr/bin/env python3
"""In-box leak probes through the REAL `forge up` (red team M2.3; needs Docker with Compose v2).

The boxed command is airlock/agent/inbox_probe.py, which runs in the real agent container after
the breach canary and `unset FORGE_CANARY_TOKEN`, exactly as `forge up -- claude` would run.
The harness knows the needles (hidden content of every history version, hidden names, the
commit-message marker, the per-run canary token, the upstream key); the box does not. A family
PASSES only if the probe exits 0 (no structural failure) AND no needle appears in any output.

  1. baseline: families proc, git, fd, env, files on the current code -> each must PASS;
  2. negative controls, one planted breach per family -> that family MUST FAIL:
       proc  - a process with a hidden line in its argv;
       git   - a dangling hidden blob written into .git/objects;
       fd    - an inherited fd 7 to a deleted file holding a hidden line;
       env   - a hidden line and FORGE_CANARY_TOKEN exported into the agent's environment;
       files - a hidden line left in /tmp and a symlink to a hidden path (canary leftovers).
Prints one line per case; exit 0 only if all hold. Label: FINITE TEST (this machine, these inputs).
"""
import os
import secrets
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness as H  # noqa: E402

FAMILIES = ("proc", "git", "fd", "env", "files")
PROBE = "python3 -I /forge/agent/inbox_probe.py"
RESULTS = []

# Each control gets the planted hidden line in $PLANT (via --pass-env, which the canary does not
# treat as a breach: it is not a credential) and moves it where the family must find it.
CONTROLS = {
    "proc": 'P="$PLANT"; unset PLANT; python3 -c "import time; time.sleep(120)" "$P" & sleep 1; exec ' + PROBE + " proc",
    "git": 'printf "%s\\n" "$PLANT" | git -c \"safe.directory=*\" hash-object -w --stdin >/dev/null || exit 9; unset PLANT; exec ' + PROBE + " git",
    "fd": 'printf "%s\\n" "$PLANT" >/tmp/.p; exec 7</tmp/.p; rm -f /tmp/.p; unset PLANT; exec ' + PROBE + " fd",
    "env": 'export FORGE_CANARY_TOKEN="$PLANT"; exec ' + PROBE + " env",
    "files": 'printf "%s\\n" "$PLANT" >/tmp/notes.txt; ln -s /workspace/src/proprietary/core.ts /tmp/l; unset PLANT; exec '
             + PROBE + " files",
}


def case(name, ok, detail=""):
    RESULTS.append(bool(ok))
    print(("ok    " if ok else "FAIL  ") + name + ("" if ok else f"   {detail}"), flush=True)


def verdicts(code, out, token, fams):
    """{family: (passed, reasons)} from the probe's exit code, its FAIL lines and the needle search."""
    text = out.decode("utf-8", "replace")
    res = {f: [] for f in fams}
    for ln in text.splitlines():
        if ln.startswith("[inbox] FAIL "):
            f = ln[13:].split(":", 1)[0]
            res.setdefault(f, []).append("structural: " + ln[13:][:200])
    for fam, label, line in H.find_needles(out, token):
        res.setdefault(fam, []).append(f"needle {label} (output line {line})")
    ran = "[inbox] done:" in text
    if not ran:
        for f in fams:
            res[f].append(f"the probe did not finish (forge up exit {code})")
    if code not in (0, 20):
        res.setdefault("other", []).append(f"forge up exit {code}")
    return {f: (not r, r) for f, r in res.items()}


def main():
    tmp = tempfile.mkdtemp(prefix="forge-inbox-")
    repo = H.make_repo(tmp)
    plant = H.hidden_texts()[1][1].splitlines()[1].strip().decode()  # the v2 marker line of the hidden core
    try:
        token = secrets.token_hex(24)
        code, out = H.forge_up(repo, ["sh", "-c", PROBE], token)
        open(os.path.join(tmp, "baseline.log"), "wb").write(out)
        text = out.decode("utf-8", "replace")
        case("inbox baseline: the canary passed and the probe ran in the agent box",
             "[canary] PASS" in text and "[inbox] done:" in text, f"exit {code}: {text[-1200:]}")
        v = verdicts(code, out, token, FAMILIES)
        for f in FAMILIES + tuple(k for k in v if k not in FAMILIES):
            ok, why = v.get(f, (False, ["no verdict"]))
            n = next((ln for ln in text.splitlines() if ln.startswith(f"[inbox] {f}:")), "")
            case(f"inbox baseline {f}: no needle in any output and no structural failure ({n[8:].strip()})",
                 ok, "; ".join(why[:6]))
        for f in FAMILIES:
            token = secrets.token_hex(24)
            code, out = H.forge_up(repo, ["sh", "-c", CONTROLS[f]], token, pass_env={"PLANT": plant})
            open(os.path.join(tmp, f"control-{f}.log"), "wb").write(out)
            text = out.decode("utf-8", "replace")
            v = verdicts(code, out, token, (f,))
            ok, why = v.get(f, (True, []))
            case(f"inbox negative control {f}: the planted breach is DETECTED ({len(why)} findings: "
                 f"{'; '.join(sorted(set(w.split(' (output')[0] for w in why))[:3])})",
                 "[canary] PASS" in text and not ok, f"exit {code}: {text[-800:]}")
        case("inbox: no container, network or run volume left behind", H.leftovers(repo) == 0, H.leftovers(repo))
    finally:
        H.cleanup(repo, logs=True)
        print(f"logs: {tmp}")
    failed = RESULTS.count(False)
    print(f"inbox probes: {len(RESULTS)} cases, {failed} failed")
    if not failed and not os.environ.get("FORGE_KEEP_LOGS"):
        shutil.rmtree(tmp, ignore_errors=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
