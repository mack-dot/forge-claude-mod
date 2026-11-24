#!/usr/bin/env bash
# prove_release.sh - the release battery of the forge plugin: 9 gates, exit 0 only if all pass.
#   ./prove_release.sh [out_dir]          (default: .forge/cache/release/<time>)
# Needs: python3, git, Dafny 4.11 or the pinned download, node 22.18+, go + goimports, cargo,
# java (the checker tests), Claude Code (`claude plugin validate`), Docker with Compose v2.
#   G1 checker tests (tests/run_tests.py), airlock unit tests, `claude plugin validate .`,
#      scans for secrets and home-directory paths (0 findings)
#   G2 hidden_paths / visible_paths: shell cat, ../, symlinks, concurrent symlink swap, git show
#   G3 real server-filesystem + gated MCP: default deny, arg rules, 20 parallel calls stop at the
#      cap, duplicate ids, mid-call crash, timeout, kill of the airlock mid-call, restarts
#   G4 every planted breach caught by the canary; fail-closed boot (invalid policy, tampered log)
#   G5 diff sync-back: allowed edits applied on y, none on n, hidden and read-only edits rejected,
#      also their case / Unicode-normalization / trailing dot-space aliases and case-only renames
#   G6 gated-call latency before (subprocess per call) and after (persistent verifier), >= 100 calls
#   G7 README: the unified workflow and its honest labels (the README, and the docs pages it links)
#   G8 llm model proxy (fake provider, fake key): no key in the box, SSE, settlement = reported usage,
#      error / cut stream / kill keep the reservation, 20 concurrent stop at the cap, restart, forge up
#   G9 in-box red team (M2.3) through the real forge up: probes in the agent box for /proc, git objects,
#      fds and sockets, env, temp files find no hidden content, hidden name, canary token, upstream key
#      or host path; one planted breach per family is detected; Docker sync-back of case / NFD /
#      trailing dot-space aliases, a case-only rename, a FIFO and a socket: all refused, export finishes
# Label: FINITE TEST (one machine, the listed inputs). Container tier: shares the host kernel.
set -u
cd "$(dirname "$0")"
export PATH="$PATH:$HOME/go/bin"   # goimports, if installed with go install
ROOT=$PWD
OUT="${1:-.forge/cache/release/$(date +%Y%m%d-%H%M%S)}"
mkdir -p "$OUT"
OUT=$(cd "$OUT" && pwd)
T0=$SECONDS
# Per-run names for the proof battery (validate.sh, bench.sh): containers, volumes and the image tag
# carry this id, so another run on the same Docker host cannot remove or replace them mid-run.
export FORGEAIR_RUN="${FORGEAIR_RUN:-r$(python3 -c 'import secrets; print(secrets.token_hex(3))')}"
# Docker objects of other tools (forge up, the llm gate) have their own random names; the cleanup
# check below counts only objects that did not exist when this run started.
docker_names() { { docker ps -a --format '{{.Names}}'; docker volume ls -q; docker network ls --format '{{.Name}}'; } 2>/dev/null \
  | grep -E '^(forgeair|forge-airlock|llm-)' | sort -u; }
BEFORE=$(docker_names)
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
FORGE_SCAN_SELFTEST=1 python3 - "$OUT" <<'PY' || g=1
import os, re, sys
out = sys.argv[1]
files = [f for f in open(f"{out}/files.z", "rb").read().decode().split("\0") if f and os.path.isfile(f)]
SECRET = re.compile(r"sk-ant-[A-Za-z0-9_-]{8,}|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16}|"
                    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|xox[abpr]-[A-Za-z0-9-]{10,}|sk_live_[A-Za-z0-9]{10,}|"
                    r"AIza[0-9A-Za-z_-]{35}")
# Home paths, also in the dashed form of tool scratch folders (-Users-<name>-…), local temp folders, and the
# running user's own name (a scoreboard once carried a scratch path with it; 2026-11-23).
user = os.environ.get("USER", "")
HOME = re.compile("/" + "Users/[A-Za-z0-9._-]+|/" + "home/[a-z][a-z0-9_-]*/|-" + "Users-[A-Za-z0-9._]+-|/private/" + "tmp/[A-Za-z]"
                  + ("|\\b" + re.escape(user) + "\\b" if len(user) >= 4 and user not in ("root", "runner", "user") else ""))
if os.environ.get("FORGE_SCAN_SELFTEST"):  # negative control: a planted path must be found
    t = "out=/private/" + "tmp/claude-501/-" + "Users-someone-x/scratchpad/run"
    assert HOME.search(t), "the home-path scan misses a planted scratch path"
    print("     scan self-test: a planted scratch path is found")
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
# aliases on a case-insensitive / normalizing host (red team 2026-11-22): TESTS/evil.ts, .GIT/hooks/*,
# Forge.yaml, SRC/proprietary/*, NFC/NFD, trailing dot/space, case-only rename -> all REJECTED, repo byte-identical
a_ok=$(grep -c '^ok    sync alias:' "$OUT/g1-airlock-unit.txt"); a_bad=$(grep -c '^FAIL  sync alias:' "$OUT/g1-airlock-unit.txt")
echo "     sync alias cases (case, NFC/NFD, trailing dot/space, case-only rename, TOCTOU): $a_ok ok, $a_bad failed"
[ "$a_bad" = 0 ] && [ "$a_ok" -ge 15 ] || g=1
for a in 'TESTS/evil.ts' '.GIT/hooks/post-checkout' 'Forge.yaml' 'SRC/proprietary/core.ts' 'NFD form of the hidden' \
         'trailing dot and space' 'case-only rename' 'negative control'; do
  grep -qF "ok    sync alias: $a" "$OUT/g1-airlock-unit.txt" || { echo "     missing alias case: $a"; g=1; }
done
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

# ---------------------------------------------------------------- G9
t=$SECONDS; g=0
python3 airlock/tests/inbox_probes/run_inbox_probes.py >"$OUT/g9-inbox-probes.txt" 2>&1 || g=1
grep -E "^(ok|FAIL)  inbox (baseline|negative)" "$OUT/g9-inbox-probes.txt" | cut -c1-150 | sed 's/^/     /'
tail -1 "$OUT/g9-inbox-probes.txt" | sed 's/^/     /'
python3 airlock/tests/inbox_probes/run_alias_e2e.py >"$OUT/g9-alias-e2e.txt" 2>&1 || g=1
grep -E "^FAIL|negative control|finished" "$OUT/g9-alias-e2e.txt" | cut -c1-150 | sed 's/^/     /'
tail -1 "$OUT/g9-alias-e2e.txt" | sed 's/^/     /'
i_ok=$(grep -c '^ok    inbox' "$OUT/g1-airlock-unit.txt"); i_bad=$(grep -c '^FAIL  inbox' "$OUT/g1-airlock-unit.txt")
echo "     in-box unit regressions (G1): $i_ok ok, $i_bad failed"
[ "$i_bad" = 0 ] && [ "$i_ok" -ge 12 ] || g=1
for f in proc git fd env files; do
  grep -q "^ok    inbox baseline $f:" "$OUT/g9-inbox-probes.txt" || { echo "     missing baseline family: $f"; g=1; }
  grep -q "^ok    inbox negative control $f:" "$OUT/g9-inbox-probes.txt" || { echo "     missing negative control: $f"; g=1; }
done
gate G9 "in-box probes (proc, git, fd, env, files) + 5 planted breaches; Docker alias sync-back" $g $((SECONDS-t))

# ---------------------------------------------------------------- G7
t=$SECONDS
python3 - <<'PY'; g=$?
import sys
import glob, re
readme = open("README.md", encoding="utf-8").read()
# The README itself (above the fold) keeps the honesty labels; the depth lives in docs/*.md, each linked from it.
top = ["/forge", "/forge approve", "INVARIANTS.md", "PROVED", "NATIVE CHECK", "FINITE TEST", "not proved",
       "end_to_end=false"]
docs = sorted(glob.glob("docs/*.md"))
unlinked = [d for d in docs if "](" + d + ")" not in readme]
t = readme + "".join(open(d, encoding="utf-8").read() for d in docs)
need = ["/forge:scan", "/forge:add", "/forge approve", "INVARIANTS.md", "/forge:airlock", "up -- claude",
        "shares the host kernel", "--pass-env", "CAN READ", "end_to_end=false", "PROVED", "NATIVE CHECK",
        "FINITE TEST", "memo", "not proved", "#91", "nosymfollow", "prove_release.sh", "Not tested"]
miss = [x for x in need if x not in t] + ["README: " + x for x in top if x not in readme]
miss += ["docs page not linked from the README: " + d for d in unlinked]
print("     README + docs phrases missing: " + (", ".join(miss) if miss else "none")
      + f" ({len(need)} required in the README or its docs, {len(top)} in the README itself, {len(docs)} docs pages linked)")
sys.exit(1 if miss else 0)
PY
gate G7 "README: unified workflow and honest labels" $g $((SECONDS-t))

# ---------------------------------------------------------------- cleanup and result
docker image rm "forge-airlock-proof:$FORGEAIR_RUN" >/dev/null 2>&1 || true
docker image rm "forge-airlock-inbox:$FORGEAIR_RUN" >/dev/null 2>&1 || true   # G9 harness image of this run
NEW=$(comm -13 <(echo "$BEFORE") <(docker_names) | grep -v '^$')
OTHER=$(echo "$NEW" | grep -v "^forgeair-$FORGEAIR_RUN-" | grep -v '^$')
MINE=$(echo "$NEW" | grep "^forgeair-$FORGEAIR_RUN-")
echo "cleanup: run $FORGEAIR_RUN: $(echo "$MINE" | grep -c .) of this run's Docker objects left; $(echo "$OTHER" | grep -c .) new object(s) from other runs (forgeair*, forge-airlock*, llm-*)" | tee -a "$OUT/scoreboard.txt"
[ -n "$NEW" ] && echo "$NEW" | sed 's/^/     left: /' | tee -a "$OUT/scoreboard.txt"
[ -n "$OTHER" ] && echo "     names without this run's id ($FORGEAIR_RUN) belong to other runs on this host or are kept by design (forge-airlock-logs-<repo>); each tool above checks its own leftovers, so only this run's names fail the release" | tee -a "$OUT/scoreboard.txt"
fail=0
for e in "${G_EXIT[@]}"; do [ "$e" = 0 ] || fail=1; done
[ -z "$MINE" ] || fail=1
echo "prove_release: $([ $fail = 0 ] && echo PASS || echo FAIL) total=$((SECONDS-T0))s out=${OUT#"$PWD"/}" | tee -a "$OUT/scoreboard.txt"
exit $fail
