#!/usr/bin/env bash
# prove_release.sh - the release battery of the forge plugin: 8 gates, exit 0 only if all pass.
#   ./prove_release.sh [out_dir]          (default: .forge/cache/release/<time>)
# Needs: python3, git, Dafny 4.11 or the pinned download, node 22.18+, go + goimports, cargo,
# java (the checker tests), Claude Code (`claude plugin validate`), Docker with Compose v2.
#   G1 checker tests (tests/run_tests.py), airlock unit tests, `claude plugin validate .`,
#      scans for secrets and home-directory paths (0 findings)
#   G2 hidden_paths / visible_paths: shell cat, ../, symlinks, concurrent symlink swap, git show
#   G3 real server-filesystem + gated MCP: default deny, arg rules, 20 parallel calls stop at the
#      cap, duplicate ids, mid-call crash, timeout, kill of the airlock mid-call, restarts
#   G4 every planted breach caught by the canary; fail-closed boot (invalid policy, tampered log)
#   G5 diff sync-back: allowed edits applied on y, none on n, hidden and read-only edits rejected
#   G6 gated-call latency before (subprocess per call) and after (persistent verifier), >= 100 calls
#   G7 README: the unified workflow and its honest labels
#   G8 llm model proxy (fake provider, fake key): no key in the box, SSE, settlement = reported usage,
#      error / cut stream / kill keep the reservation, 20 concurrent stop at the cap, restart, forge up
# Label: FINITE TEST (one machine, the listed inputs). Container tier: shares the host kernel.
set -u
cd "$(dirname "$0")"
export PATH="$PATH:$HOME/go/bin"   # goimports, if installed with go install
ROOT=$PWD
OUT="${1:-.forge/cache/release/$(date +%Y%m%d-%H%M%S)}"
mkdir -p "$OUT"
OUT=$(cd "$OUT" && pwd)
T0=$SECONDS
: > "$OUT/scoreboard.txt"
declare -a G_NAME G_EXIT G_SEC

gate() {  # gate <id> <name> <exit> <seconds>
  G_NAME+=("$1 $2"); G_EXIT+=("$3"); G_SEC+=("$4")
  printf '%-3s %-74s exit=%-3s %5ss\n' "$1" "$2" "$3" "$4" | tee -a "$OUT/scoreboard.txt"
}

echo "prove_release: commit $(git rev-parse --short HEAD 2>/dev/null) dirty=$(git status --porcelain | wc -l | tr -d ' ') docker $(docker version --format '{{.Server.Version}}' 2>/dev/null) $(date -u +%FT%TZ)" | tee "$OUT/header.txt"

# ---------------------------------------------------------------- G1
t=$SECONDS; g=0
python3 tests/run_tests.py >"$OUT/g1-checker-tests.txt" 2>&1 || g=1
tail -1 "$OUT/g1-checker-tests.txt" | sed 's/^/     checker: /'
python3 airlock/tests/run_tests.py >"$OUT/g1-airlock-unit.txt" 2>&1 || g=1
tail -1 "$OUT/g1-airlock-unit.txt" | sed 's/^/     airlock unit: /'
claude plugin validate . >"$OUT/g1-validate.txt" 2>&1 || g=1
grep -E "Validation (passed|failed)" "$OUT/g1-validate.txt" | sed 's/^/     /'
# files that would be committed: tracked + untracked, not ignored
git ls-files -co --exclude-standard -z > "$OUT/files.z"
python3 - "$OUT" <<'PY' || g=1
import os, re, sys
out = sys.argv[1]
files = [f for f in open(f"{out}/files.z", "rb").read().decode().split("\0") if f and os.path.isfile(f)]
SECRET = re.compile(r"sk-ant-[A-Za-z0-9_-]{8,}|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16}|"
                    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|xox[abpr]-[A-Za-z0-9-]{10,}|sk_live_[A-Za-z0-9]{10,}|"
                    r"AIza[0-9A-Za-z_-]{35}")
HOME = re.compile("/" + "Users/[A-Za-z0-9._-]+|/" + "home/[a-z][a-z0-9_-]*/")
sec, home = [], []
for f in files:
    try:
        t = open(f, encoding="utf-8").read()
    except (UnicodeDecodeError, OSError):
        continue
    sec += [f"{f}: {m.group(0)[:12]}..." for m in SECRET.finditer(t)]
    home += [f"{f}: {m.group(0)}" for m in HOME.finditer(t)]
fake = sum(1 for f in files for _ in [0] if "FAKE_" in open(f, errors="ignore").read())
print(f"     scan: {len(files)} files; secret-like strings: {len(sec)}; home-directory paths: {len(home)}; "
      f"files with FAKE_ demo keys (named as fake): {fake}")
for x in (sec + home)[:10]:
    print("     FINDING " + x)
sys.exit(1 if sec or home else 0)
PY
gate G1 "checker tests + airlock unit tests + plugin validate + secret/home-path scans" $g $((SECONDS-t))

# ---------------------------------------------------------------- Docker battery (G2, G3, G4)
t=$SECONDS
airlock/proof/validate.sh "$OUT/validate" >"$OUT/validate-console.txt" 2>&1; v=$?
VS=$SECONDS
python3 - "$OUT/validate/summary.txt" "$OUT" <<'PY'
import re, sys
lines = [l.rstrip("\n") for l in open(sys.argv[1]) if l[:4] in ("ok  ", "FAIL")]
G4 = re.compile(r"planted breach|lockdown|invalid policy|flipped log byte|second airlock|re-verified on each")
groups = {"G2": [], "G3": [], "G4": []}
for l in lines:
    body = l[5:]
    if G4.search(body):
        groups["G4"].append(l)
    elif re.match(r"(codebase|visible|image build)", body.strip()):
        groups["G2"].append(l)
    else:
        groups["G3"].append(l)
for g, ls in groups.items():
    open(f"{sys.argv[2]}/{g}.lines", "w").write("\n".join(ls) + "\n")
    bad = sum(1 for l in ls if l.startswith("FAIL"))
    open(f"{sys.argv[2]}/{g}.exit", "w").write("1" if bad or not ls else "0")
PY
for G in G2 G3 G4; do sed 's/^/     /' "$OUT/$G.lines" | cut -c1-120; done > "$OUT/validate-by-gate.txt"
grep -E "^\s+\[swap\]|^info" "$OUT/validate/summary.txt" "$OUT/validate-console.txt" 2>/dev/null | head -4 | cut -c1-200 | sed 's/^/     /'
n2=$(grep -c . "$OUT/G2.lines"); n3=$(grep -c . "$OUT/G3.lines"); n4=$(grep -c . "$OUT/G4.lines")
gate G2 "hidden/visible paths: cat, ../, symlinks, concurrent swap, git show ($n2 checks)" "$(cat "$OUT/G2.exit")" $((VS-t))
gate G3 "real fs server + gates: deny, args, 20 parallel, dup ids, crash, timeout, restart ($n3)" "$(cat "$OUT/G3.exit")" $((VS-t))
gate G4 "planted breaches caught; fail-closed boot ($n4 checks)" "$(cat "$OUT/G4.exit")" $((VS-t))
[ "$v" = 0 ] || echo "     validate.sh exit=$v (see $OUT/validate/summary.txt)"

# ---------------------------------------------------------------- G5
t=$SECONDS; g=0
python3 airlock/tests/run_up_e2e.py >"$OUT/g5-up-e2e.txt" 2>&1 || g=1
tail -1 "$OUT/g5-up-e2e.txt" | sed 's/^/     up e2e: /'
s_ok=$(grep -c '^ok    sync:' "$OUT/g1-airlock-unit.txt"); s_bad=$(grep -c '^FAIL  sync:' "$OUT/g1-airlock-unit.txt")
echo "     sync filter unit cases: $s_ok ok, $s_bad failed"
[ "$s_bad" = 0 ] && [ "$s_ok" -ge 10 ] || g=1
gate G5 "diff sync-back: y applies allowed only, n applies nothing, hidden/read-only rejected" $g $((SECONDS-t))

# ---------------------------------------------------------------- G6
t=$SECONDS; g=0
FORGE_VERIFIER=subprocess python3 airlock/tests/bench_gate.py 120 2>/dev/null | tail -1 >"$OUT/g6-host-before.json" || g=1
FORGE_VERIFIER=persistent python3 airlock/tests/bench_gate.py 120 2>/dev/null | tail -1 >"$OUT/g6-host-after.json" || g=1
FORGE_VERIFIER=subprocess airlock/proof/bench.sh "$OUT/bench-before" >"$OUT/g6-docker-before.md" 2>&1 || g=1
FORGE_VERIFIER=persistent airlock/proof/bench.sh "$OUT/bench-after" >"$OUT/g6-docker-after.md" 2>&1 || g=1
python3 - "$OUT" <<'PY' || g=1
import json, sys
o = sys.argv[1]
hb, ha = (json.load(open(f"{o}/g6-host-{x}.json")) for x in ("before", "after"))
def gate_row(f):
    rows = json.load(open(f))["rows"]
    return [r for r in rows if r["call"].startswith("charge")][0]
db, da = gate_row(f"{o}/bench-before/bench.json"), gate_row(f"{o}/bench-after/bench.json")
print(f"     host  GateLog.reserve n={hb['n']}: before {hb['median_ms']} / {hb['p95_ms']} ms -> after "
      f"{ha['median_ms']} / {ha['p95_ms']} ms (median / p95)")
print(f"     docker charge via airlock n={da['n']}: before {db['proxy_median_ms']} / {db['proxy_p95_ms']} ms -> after "
      f"{da['proxy_median_ms']} / {da['proxy_p95_ms']} ms; direct {da['direct_median_ms']} ms")
json.dump({"host_before": hb, "host_after": ha, "docker_before": db, "docker_after": da}, open(f"{o}/g6.json", "w"), indent=1)
ok = ha["n"] >= 100 and da["n"] >= 100 and ha["median_ms"] < hb["median_ms"] and da["proxy_median_ms"] < db["proxy_median_ms"]
sys.exit(0 if ok else 1)
PY
gate G6 "gated-call latency before (subprocess) vs after (persistent), median and p95" $g $((SECONDS-t))

# ---------------------------------------------------------------- G8
t=$SECONDS; g=0
python3 airlock/tests/run_llm_gate.py >"$OUT/g8-llm-gate.txt" 2>&1 || g=1
grep -E "^(FAIL|llm gate:|timings)" "$OUT/g8-llm-gate.txt" | sed 's/^/     /'
gate G8 "llm proxy: no key in box, SSE, settle, error/cut/kill keep, 20 parallel at cap, restart" $g $((SECONDS-t))

# ---------------------------------------------------------------- G7
t=$SECONDS
python3 - <<'PY'; g=$?
import sys
t = open("README.md", encoding="utf-8").read()
need = ["/forge:scan", "/forge:add", "/forge approve", "INVARIANTS.md", "/forge:airlock", "up -- claude",
        "shares the host kernel", "--pass-env", "CAN READ", "end_to_end=false", "PROVED", "NATIVE CHECK",
        "FINITE TEST", "memo", "not proved", "#91", "nosymfollow", "prove_release.sh", "Not tested"]
miss = [x for x in need if x not in t]
print("     README phrases missing: " + (", ".join(miss) if miss else "none") + f" ({len(need)} required)")
sys.exit(1 if miss else 0)
PY
gate G7 "README: unified workflow and honest labels" $g $((SECONDS-t))

# ---------------------------------------------------------------- cleanup and result
c=$(docker ps -aq --filter name=^forgeair | wc -l | tr -d ' ')
vol=$(docker volume ls -q | grep -cE '^(forgeair|forge-airlock)' | tr -d ' ')
net=$(docker network ls -q --filter name=^forgeair | wc -l | tr -d ' ')
c=$((c + $(docker ps -aq --filter name=^llm- | wc -l))); vol=$((vol + $(docker volume ls -q | grep -c '^llm-')))
net=$((net + $(docker network ls -q --filter name=^llm- | wc -l)))
echo "cleanup: containers=$c volumes=$vol networks=$net (forgeair*, forge-airlock*, llm-*)" | tee -a "$OUT/scoreboard.txt"
fail=0
for e in "${G_EXIT[@]}"; do [ "$e" = 0 ] || fail=1; done
[ "$c$vol$net" = 000 ] || fail=1
echo "prove_release: $([ $fail = 0 ] && echo PASS || echo FAIL) total=$((SECONDS-T0))s out=$OUT" | tee -a "$OUT/scoreboard.txt"
exit $fail
