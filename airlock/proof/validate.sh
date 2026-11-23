#!/usr/bin/env bash
# validate.sh - run the three domains through the SAME airlock image, each from a clean
# state (docker compose down -v), with only a different forge.yaml. Exit 0 only if every
# check below has its expected exit code. Logs go to ${1:-results/latest}/.
# Label: FINITE TEST. Container tier: the agent shares the host kernel, unless
# FORGE_ISOLATION=gvisor|microvm: then every agent-side container (agent, planted-breach controls,
# bench agent) runs under that backend's Docker runtime (airlock/backends) and the run starts with
# the isolation probe checks (positive: the agent's kernel is not the host kernel; negative: a runc
# container fails the same probe). Default FORGE_ISOLATION=docker (runc, the probe must fail).
set -u
cd "$(dirname "$0")"
# F7: one token for the canary endpoints of this run (airlock + canary only)
export FORGE_CANARY_TOKEN="${FORGE_CANARY_TOKEN:-$(python3 -c 'import secrets; print(secrets.token_hex(24))')}"
# Per-run names: two runs on one Docker host must not share containers, volumes or the image.
OWN_RUN=0; [ -n "${FORGEAIR_RUN:-}" ] || OWN_RUN=1  # an id we made ourselves: we remove its image at the end
export FORGEAIR_RUN="${FORGEAIR_RUN:-r$(python3 -c 'import secrets; print(secrets.token_hex(3))')}"
trap '[ "$OWN_RUN" = 1 ] && docker image rm "forge-airlock-proof:$FORGEAIR_RUN" >/dev/null 2>&1' EXIT
OUT="${1:-results/latest}"
mkdir -p "$OUT"
: > "$OUT/summary.txt"
FAIL=0
T_ALL=$SECONDS

rec() {  # rec <check> <actual> <expected> <seconds>
  local mark=ok
  [ "$2" = "$3" ] || { mark=FAIL; FAIL=1; }
  printf '%-4s %-62s exit=%-3s expect=%-3s %4ss\n' "$mark" "$1" "$2" "$3" "$4" | tee -a "$OUT/summary.txt"
}

ISO="${FORGE_ISOLATION:-docker}"
# macOS microvm = vfkit: the agent side runs in vfkit VMs (backends/vfkit_compose.py), not containers
VFK=""
if [ "$ISO" = microvm ] && [ "$(uname -s)" = Darwin ] && [ -z "${FORGE_AIRLOCK_MICROVM_RUNTIME:-}" ]; then VFK=1; fi
if [ -n "$VFK" ]; then
  python3 -I -c 'import sys; sys.path.insert(0, sys.argv[1]); import vfkit_vm; vfkit_vm.preflight()' ../backends \
    2>"$OUT/vfkit-preflight.log" \
    || { echo "VALIDATE: isolation microvm (vfkit) not available on this Mac (fail closed): $(tail -1 "$OUT/vfkit-preflight.log")" | tee -a "$OUT/summary.txt"; exit 2; }
  VMWS="$(cd "$OUT" && pwd)/vmws"; mkdir -p "$VMWS"; : > "$OUT/vm.pids"
  VFK_OVR="$OUT/compose.vfkit.yml"
  # Docker publishes no port of a container that is only on internal networks: airlock and mock-http
  # also join "vmhost" (a plain bridge) so that their ports reach the host's 127.0.0.1, where the VM's
  # vsock relay connects. The agent side is a VM and is on no Docker network at all.
  { echo "networks:"
    echo "  vmhost: {}"
    echo "services:"
    echo "  airlock:"
    echo "    networks: [agentnet, upstream, vmhost]"
    echo "    ports: [\"127.0.0.1::8700\"]"
    echo "    volumes: !override [\"./policies/\${FORGE_DOMAIN:-codebase}.yaml:/forge/forge.yaml:ro\", \"./fixtures:/forge-fixtures:ro\", \"logs:/data\", \"state:/state\", \"$VMWS:/ws-raw\"]"
    echo "  mock-http: {networks: [upstream, vmhost], ports: [\"127.0.0.1::9100\"]}"
    echo "  bench-direct: {volumes: !override [\"$VMWS:/ws:ro\"]}"; } >"$VFK_OVR"
  { echo "services:"
    echo "  airlock:"
    echo "    environment: {FORGE_WS_RAW: \"\"}"
    echo "    volumes: !override [\"./policies/\${FORGE_DOMAIN:-codebase}.yaml:/forge/forge.yaml:ro\", \"./fixtures:/forge-fixtures:ro\", \"logs:/data\", \"state:/state\", \"$VMWS:/ws\"]"; } >"$OUT/compose.vfkit-nosym.yml"
fi
RT=$([ -n "$VFK" ] || python3 -I -c 'import sys; sys.path.insert(0, sys.argv[1]); import backends as B; print(B.runtime_for(sys.argv[2], B.docker_runtimes()) or "")' .. "$ISO") \
  || { echo "VALIDATE: isolation $ISO not available on this Docker host (fail closed)" | tee -a "$OUT/summary.txt"; exit 2; }
RT_OVR=""
if [ -n "$RT" ]; then  # the agent side only; airlock, mock-http, bench-direct stay on runc
  # gVisor's netstack and a Kata guest do not reach Docker's embedded DNS (a NAT rule in the netns):
  # fixed addresses for airlock and mock-http, written into the agent side's /etc/hosts.
  RT_OVR="$OUT/compose.runtime.yml"
  # two /24s picked from the run id, so parallel gvisor/microvm runs do not ask for the same subnet
  NA=$(( $(python3 -c 'import hashlib,sys; print(int(hashlib.sha256(sys.argv[1].encode()).hexdigest(), 16) % 120)' "$FORGEAIR_RUN") * 2 ))
  NB=$(( NA + 1 ))
  # mock-http only for control-egress (the agent on the upstream network): the canary of a normal agent
  # counts a resolvable upstream name as a breach.
  H="[\"airlock:172.30.$NA.10\"]"; HE="[\"airlock:172.30.$NA.10\", \"mock-http:172.30.$NB.20\"]"
  { echo "networks:"
    echo "  agentnet: {ipam: {config: [{subnet: 172.30.$NA.0/24}]}}"
    echo "  upstream: {ipam: {config: [{subnet: 172.30.$NB.0/24}]}}"
    echo "services:"
    echo "  airlock: {networks: {agentnet: {ipv4_address: 172.30.$NA.10}, upstream: {ipv4_address: 172.30.$NB.10}}}"
    echo "  mock-http: {networks: {upstream: {ipv4_address: 172.30.$NB.20}}}"
    for s in agent control-env-secret control-hidden-mount control-egress control-root-writes agent-bench; do
      printf '  %s: {runtime: %s, extra_hosts: %s}\n' "$s" "$RT" "$([ "$s" = control-egress ] && echo "$HE" || echo "$H")"
    done; } >"$RT_OVR"
  # a SECOND airlock (the lock test) must not take the live airlock's fixed address
  printf 'services:\n  airlock: {networks: !override [upstream]}\n' >"$OUT/compose.second-airlock.yml"
fi
echo "== run $FORGEAIR_RUN" | tee -a "$OUT/summary.txt"
echo "== isolation $ISO (agent runtime: ${RT:-runc}$([ -n "$VFK" ] && echo '; vfkit VM, not a container'))" | tee -a "$OUT/summary.txt"

dc_raw() {  # vfkit: the no-nosymfollow control needs its workspace mount on the shared host folder too (last file wins)
  local nosym=""; [ -n "$VFK" ] && [ "${OVR:-}" = control-nosymfollow-off.yml ] && nosym="$OUT/compose.vfkit-nosym.yml"
  docker compose -p "forgeair-$FORGEAIR_RUN-$DOM" -f docker-compose.yml ${OVR:+-f "$OVR"} ${RT_OVR:+-f "$RT_OVR"} ${VFK_OVR:+-f "$VFK_OVR"} ${nosym:+-f "$nosym"} ${XOVR:+-f "$XOVR"} "$@"; }
dc() {  # under a runtime override, `run` without --interactive: under Kata a `run -T` never ended in CI (run 45115365474); stdin is the suspect
  if [ -n "$RT" ] && [ "$1" = run ]; then shift; set -- run --interactive=false "$@"; fi
  if [ -n "$VFK" ]; then vfk_dc "$@"; return; fi
  dc_raw "$@"; }

AGENT_SIDE=" agent control-env-secret control-hidden-mount control-egress control-root-writes agent-bench "
vfk_stop_vms() {  # the VMs of `run -d`, then the shared workspace folder (flags first: read-only entries are immutable)
  local p; for p in $(cat "$OUT/vm.pids" 2>/dev/null); do pkill -TERM -P "$p" 2>/dev/null; kill "$p" 2>/dev/null; done; : > "$OUT/vm.pids"
  python3 -I -c 'import sys; sys.path.insert(0, sys.argv[1]); import vfkit_vm; vfkit_vm.unlock_tree(sys.argv[2])' ../backends "$VMWS"
  rm -rf "${VMWS:?}"; mkdir -p "$VMWS"; }
vfk_vm() {  # vfk_vm [run options] <agent-side service> [command...]: the service in a vfkit VM
  local detach="" nodeps="" envs=() a svc p_air p_mock
  while [ $# -gt 0 ]; do case "$1" in
    --rm|-T|--interactive=false) shift;; --no-deps) nodeps=1; shift;; -d) detach=1; shift;;
    -e) envs+=(-e "$2"); shift 2;; *) break;; esac; done
  svc=$1; shift
  [ -n "$nodeps" ] || dc_raw up -d --wait airlock >/dev/null 2>&1 || { echo "vfk: airlock did not start" >&2; return 2; }
  dc_raw --profile controls --profile bench config --format json >"$OUT/vm-config.json" 2>/dev/null || return 2
  p_air=$(dc_raw port airlock 8700 2>/dev/null | head -1 | sed 's/.*://')
  p_mock=$(dc_raw port mock-http 9100 2>/dev/null | head -1 | sed 's/.*://')
  [ -n "$p_air" ] || { echo "vfk: the airlock's port is not published (fail closed)" >&2; return 2; }
  dc_raw exec -T airlock python3 -I -c "import json; d=json.load(open('/state/workspace.json')); print(json.dumps({'closed_dirs': d.get('closed_dirs', []), 'readonly_files': d.get('readonly_files', [])}))" >"$OUT/vm-ro.json" 2>/dev/null \
    || { echo "vfk: no read-only list from the airlock (fail closed)" >&2; return 2; }
  a=(--config "$OUT/vm-config.json" --service "$svc" --volume "ws=$VMWS" --net "agentnet=8700:127.0.0.1:${p_air:-0}:airlock")
  # control-root-writes in a VM: guest root alone cannot write read-only entries (guest read-only binds
  # AND the host immutable flag; measured: the canary PASSES as guest root). The planted breach for the
  # VM is therefore "root AND no read-only protection"; the canary must report it.
  [ "$svc" = control-root-writes ] || a+=(--ro-list "$OUT/vm-ro.json" --lock)
  [ -n "$p_mock" ] && a+=(--net "upstream=9100:127.0.0.1:$p_mock:mock-http")
  if [ -n "$detach" ]; then
    python3 -I ../backends/vfkit_compose.py "${a[@]}" ${envs[@]+"${envs[@]}"} -- "$@" >/dev/null 2>&1 &
    echo $! >>"$OUT/vm.pids"; echo $! >>"$OUT/vm.allpids"; return 0
  fi
  python3 -I ../backends/vfkit_compose.py "${a[@]}" ${envs[@]+"${envs[@]}"} -- "$@"; }
vfk_dc() {
  local a svc="" skip="" rc
  case "$1" in
    run)
      for a in "${@:2}"; do
        if [ -n "$skip" ]; then skip=""; continue; fi
        case "$a" in -e|-v|--entrypoint|-u|-w) skip=1;; -*) ;; *) svc=$a; break;; esac
      done
      case "$AGENT_SIDE" in *" $svc "*) shift; vfk_vm "$@"; return;; esac;;
    up)
      case " $* " in *" --exit-code-from agent "*)  # = start airlock (+ mock-http), run the agent, stop all
        dc_raw up -d --wait airlock mock-http >/dev/null 2>&1 || return 1
        vfk_vm --no-deps agent; rc=$?; dc_raw stop >/dev/null 2>&1; return $rc;; esac;;
    down|--profile)
      case " $* " in *" down "*) vfk_stop_vms; dc_raw "$@"; rc=$?; vfk_stop_vms; return $rc;; esac;;
  esac
  dc_raw "$@"; }

json_check() {  # json_check <file> <python expression over d>
  python3 -c "import json,sys; d=json.loads(open(sys.argv[1]).read().strip().splitlines()[-1]); sys.exit(0 if ($2) else 1)" "$1"
}

restart_airlock() { dc restart airlock >/dev/null 2>&1; dc up -d --wait airlock >/dev/null 2>&1; }

echo "== build (one image for every domain)"
t=$SECONDS; docker compose -p "forgeair-$FORGEAIR_RUN-build" build -q >"$OUT/build.log" 2>&1; rec "image build" $? 0 $((SECONDS-t))

# ---- isolation probe (airlock/backends.probe_sh), with a negative control on every host
DOM=iso; export FORGE_DOMAIN=codebase
PROBE_NAME=$([ "$ISO" = docker ] && echo gvisor || echo "$ISO")   # docker: run the gvisor probe; it MUST fail
PROBE=$(python3 -I -c 'import sys; sys.path.insert(0, sys.argv[1]); import backends as B; print(B.probe_sh(sys.argv[2]))' .. "$PROBE_NAME")
dc --profile controls down -v --remove-orphans >/dev/null 2>&1
t=$SECONDS; dc up -d --wait airlock >/dev/null 2>&1
export FORGE_HOST_KERNEL=$(dc exec -T airlock cat /proc/version 2>/dev/null)
dc run --rm --no-deps -T -e FORGE_HOST_KERNEL agent sh -c "$PROBE cat /proc/version" </dev/null >"$OUT/iso-probe.log" 2>&1; rc=$?
echo "     host (airlock) kernel: $FORGE_HOST_KERNEL" | cut -c1-160 | tee -a "$OUT/summary.txt"
sed 's/^/     agent: /' "$OUT/iso-probe.log" | head -4 | cut -c1-160 | tee -a "$OUT/summary.txt"
if [ "$ISO" = docker ]; then
  rec "isolation: negative control: runc agent fails the gvisor probe (shares the host kernel)" "$rc" 87 $((SECONDS-t))
else
  rec "isolation: $ISO agent passes the probe (own kernel$([ "$ISO" = gvisor ] && echo ', gVisor in dmesg'))" "$rc" 0 $((SECONDS-t))
  t=$SECONDS
  docker run --rm --runtime runc -e FORGE_HOST_KERNEL "forge-airlock-proof:$FORGEAIR_RUN" sh -c "$PROBE true" >"$OUT/iso-probe-control.log" 2>&1
  rec "isolation: negative control: the same probe in a runc container -> exit 87" $? 87 $((SECONDS-t))
fi
unset FORGE_HOST_KERNEL
dc --profile controls down -v --remove-orphans >/dev/null 2>&1

for DOM in ${DOMAINS:-codebase visible quota concurrency crash}; do
  export FORGE_DOMAIN=$DOM FORGE_PHASE=1
  OVR="$OUT/compose.mounts.$DOM.yml"
  docker run --rm -v "$PWD/policies:/p:ro" "forge-airlock-proof:$FORGEAIR_RUN" \
    python3 -I /forge/airlock/render_mounts.py "/p/$DOM.yaml" >"$OVR"
  echo "== domain $DOM (forge.yaml = policies/$DOM.yaml)"
  dc --profile controls down -v --remove-orphans >/dev/null 2>&1
  t=$SECONDS
  dc up --exit-code-from agent agent airlock mock-http >"$OUT/$DOM-up.log" 2>&1
  rec "$DOM: docker compose up (canary + scenario phase 1)" $? 0 $((SECONDS-t))
  grep -E "\[canary\] (PASS|BREACH)|\[agent\] .*ok, " "$OUT/$DOM-up.log" | sed 's/^/     /'

  if [ "$DOM" = codebase ]; then
    t=$SECONDS; restart_airlock
    dc run --rm agent sh -c "python3 -I /forge/agent/canary.py >/dev/null && SWAP_N=${SWAP_N:-3000} exec python3 -I /forge/agent/swap_attack.py" >"$OUT/$DOM-swap.log" 2>&1
    rec "$DOM: concurrent symlink-swap attack on MCP reads -> no leak" $? 0 $((SECONDS-t))
    grep -E "^\[swap\]" "$OUT/$DOM-swap.log" | head -2 | cut -c1-240 | sed 's/^/     /'
    t=$SECONDS; dc stop airlock >/dev/null 2>&1
    OVR_SAVE=$OVR; OVR="control-nosymfollow-off.yml"; dc up -d --wait airlock >/dev/null 2>&1
    dc run --rm agent sh -c "python3 -I /forge/agent/canary.py >/dev/null && SWAP_N=${SWAP_N:-3000} exec python3 -I /forge/agent/swap_attack.py" >"$OUT/$DOM-swap-control.log" 2>&1
    echo "info $DOM: CONTROL swap attack WITHOUT nosymfollow (not gated): exit=$? (20 = the attack won)" | tee -a "$OUT/summary.txt"
    grep -E "^\[swap\]" "$OUT/$DOM-swap-control.log" | head -3 | cut -c1-240 | sed 's/^/     /'
    dc stop airlock >/dev/null 2>&1; OVR=$OVR_SAVE; dc up -d --wait airlock >/dev/null 2>&1
    for c in control-env-secret control-hidden-mount control-root-writes; do
      t=$SECONDS; restart_airlock
      dc run --rm "$c" >"$OUT/$DOM-$c.log" 2>&1
      rec "$DOM: planted breach $c -> canary BREACH" $? 10 $((SECONDS-t))
      grep -E "\[canary\] BREACH:" "$OUT/$DOM-$c.log" | head -3 | sed 's/^/     /'
    done
    t=$SECONDS
    dc run --rm --no-deps agent python3 -I /forge/agent/lockdown_probe.py >"$OUT/$DOM-lockdown.log" 2>&1
    rec "$DOM: after BREACH an allowed read is refused (lockdown)" $? 0 $((SECONDS-t))
    t=$SECONDS
    dc logs --no-log-prefix airlock >"$OUT/$DOM-airlock.log" 2>&1
    n=$(grep -c "workspace: re-verified" "$OUT/$DOM-airlock.log")
    [ "$n" -ge 3 ]; rec "$DOM: workspace re-verified on each of 3 restarts (count=$n)" $? 0 $((SECONDS-t))
  fi

  if [ "$DOM" = quota ]; then
    t=$SECONDS; dc up -d --wait mock-http airlock >/dev/null 2>&1 && dc restart airlock >/dev/null 2>&1 \
      && dc up -d --wait airlock >/dev/null 2>&1
    rec "quota: docker compose restart airlock (same log volume)" $? 0 $((SECONDS-t))
    dc logs --no-log-prefix airlock 2>&1 | grep -E "reloaded" | sed 's/^/     /'
    t=$SECONDS; FORGE_PHASE=2 dc run --rm agent >"$OUT/$DOM-phase2.log" 2>&1
    rec "quota: phase 2 after restart (caps kept)" $? 0 $((SECONDS-t))
    grep -E "\[agent\] .*ok, " "$OUT/$DOM-phase2.log" | sed 's/^/     /'
    sleep 3  # the two timed-out slow_job calls finish upstream after 4 s
    t=$SECONDS; dc exec -T airlock python3 -I /forge/airlock/audit.py http://mock-http:9100/ledger >"$OUT/$DOM-audit.json" 2>&1
    rec "quota: audit (every log re-verifies ACCEPT, gross <= cap)" $? 0 $((SECONDS-t))
    json_check "$OUT/$DOM-audit.json" "d['logs']['deploy']['gross']==3 and d['logs']['run_query']['gross']==1000 and d['logs']['slow_job']['gross']==2 and d['logs']['slow_job']['journal_final_states']=={'ABORTED':2} and d['upstream_ledger']['deploy']['calls']==3 and d['upstream_ledger']['run_query']['sum']['rows']==1000 and 'drop_database' not in d['upstream_ledger'] and 'rotate_keys' not in d['upstream_ledger']"
    rec "quota: audit numbers (3 deploys, 1000 rows, 2 aborted, 0 denied calls upstream)" $? 0 0
    t=$SECONDS; restart_airlock
    dc run --rm control-egress >"$OUT/$DOM-control-egress.log" 2>&1
    rec "quota: planted breach control-egress (agent on upstream net) -> BREACH" $? 10 $((SECONDS-t))
    grep -E "\[canary\] BREACH:" "$OUT/$DOM-control-egress.log" | head -3 | sed 's/^/     /'
    t=$SECONDS; XOVR=${RT_OVR:+$OUT/compose.second-airlock.yml} dc run --rm --no-deps airlock >"$OUT/$DOM-second-writer.log" 2>&1; rc=$?
    grep -q "another airlock holds the data volume lock" "$OUT/$DOM-second-writer.log" || rc="$rc/no-reason"
    rec "quota: second airlock on the same log volume refuses to start" "$rc" 2 $((SECONDS-t))
    dc stop airlock >/dev/null 2>&1  # the live airlock holds the lock; stop it for the next cases
    for case in "unknown-key:unknown keys" "allow-and-deny:both denied and allowed" \
                "tool-not-offered:offered by 0 matching upstreams" "cap-changed:stored cap 3 != policy cap 4"; do
      bad=${case%%:*}; why=${case#*:}; t=$SECONDS
      dc run --rm --no-deps -e FORGE_POLICY=/forge/invalid/$bad.yaml -v "$PWD/policies/invalid:/forge/invalid:ro" \
        airlock >"$OUT/$DOM-invalid-$bad.log" 2>&1; rc=$?
      grep -q "$why" "$OUT/$DOM-invalid-$bad.log" || rc="$rc/no-reason"
      rec "quota: invalid policy $bad -> refuses to start" "$rc" 2 $((SECONDS-t))
    done
    t=$SECONDS
    # flip one bit of the gross field of step 2; the tamper itself must succeed, else the case fails
    if dc run --rm --no-deps --entrypoint python3 airlock -c "import os,sys; p='/data/deploy.bin'; sys.exit(9) if os.path.getsize(p) < 3*128 else None; f=open(p,'r+b'); f.seek(128*2+72); b=f.read(1); f.seek(128*2+72); f.write(bytes([b[0]^1])); f.close()" >/dev/null 2>&1; then
      dc run --rm --no-deps airlock >"$OUT/$DOM-tampered.log" 2>&1; rc=$?
      grep -q "stored log does not verify" "$OUT/$DOM-tampered.log" || rc="$rc/no-reason"
    else
      rc="tamper-not-applied"
    fi
    rec "quota: one flipped log byte -> airlock refuses to start" "$rc" 2 $((SECONDS-t))
    grep -E "FATAL" "$OUT/$DOM-tampered.log" | sed 's/^/     /'
  fi

  if [ "$DOM" = crash ]; then
    t=$SECONDS; dc up -d --wait airlock >/dev/null 2>&1
    dc run -d --rm -e FORGE_PHASE=2 agent >/dev/null 2>&1
    n=0; until dc exec -T airlock python3 -c "import json,sys; L=[json.loads(x) for x in open('/data/crashy_job.journal.jsonl') if x.strip()]; sys.exit(0 if L and L[-1]['seq']==5 and L[-1]['state']=='PENDING' else 1)" 2>/dev/null; do
      n=$((n+1)); [ $n -gt 300 ] && break; sleep 0.2; done
    docker kill -s KILL "$(dc ps -q airlock)" >/dev/null 2>&1
    rec "crash: airlock killed (SIGKILL) while gated call 5 is PENDING (waited ${n} polls)" $([ $n -le 300 ]; echo $?) 0 $((SECONDS-t))
    t=$SECONDS; dc up -d --wait airlock >/dev/null 2>&1
    dc exec -T airlock python3 -I /forge/airlock/audit.py >"$OUT/$DOM-audit.json" 2>&1
    rec "crash: after restart every log re-verifies ACCEPT" $? 0 $((SECONDS-t))
    json_check "$OUT/$DOM-audit.json" "d['logs']['crashy_job']['gross']==5 and d['logs']['crashy_job']['journal_final_states']=={'COMMITTED':2,'ABORTED':3}"
    rec "crash: exact spent total 5 of 5 (2 COMMITTED; crash, timeout, killed-in-flight ABORTED)" $? 0 0
    grep -E "PENDING at restart|reloaded" <(dc logs --no-log-prefix airlock 2>&1) | tail -2 | sed 's/^/     /'
    t=$SECONDS; FORGE_PHASE=3 dc run --rm agent >"$OUT/$DOM-phase3.log" 2>&1
    rec "crash: next call after the restart refused REJECT_OVER_CAP" $? 0 $((SECONDS-t))
    t=$SECONDS; restart_airlock; restart_airlock
    dc exec -T airlock python3 -I /forge/airlock/audit.py >"$OUT/$DOM-audit2.json" 2>&1
    json_check "$OUT/$DOM-audit2.json" "d['logs']['crashy_job']['gross']==5 and d['ok']"
    rec "crash: spent total still exactly 5 after two more restarts" $? 0 $((SECONDS-t))
  fi

  if [ "$DOM" = concurrency ]; then
    t=$SECONDS; dc up -d --wait mock-http airlock >/dev/null 2>&1
    dc exec -T airlock python3 -I /forge/airlock/audit.py http://mock-http:9100/ledger >"$OUT/$DOM-audit.json" 2>&1
    rec "concurrency: audit (every log re-verifies ACCEPT, gross <= cap)" $? 0 $((SECONDS-t))
    json_check "$OUT/$DOM-audit.json" "d['logs']['charge']['gross']==1000 and d['logs']['deploy']['gross']==5 and d['upstream_ledger']['charge']['calls']==14 and d['upstream_ledger']['charge']['sum']['amount']==1000 and d['upstream_duplicate_invoice_ids']==0 and d['upstream_ledger']['deploy']['calls']==5"
    rec "concurrency: audit numbers (sum 1000, 14 charges, 0 duplicate ids, 5 deploys)" $? 0 0
    t=$SECONDS; restart_airlock
    dc run --rm agent sh -c "python3 -I /forge/agent/protocol_regress.py pre && python3 -I /forge/agent/canary.py >/dev/null && unset FORGE_CANARY_TOKEN && exec python3 -I /forge/agent/protocol_regress.py post" >"$OUT/$DOM-protocol.log" 2>&1
    rec "concurrency: protocol red-team regressions (F1 F2 F3 F4 F5 F7) through the airlock" $? 0 $((SECONDS-t))
    grep -E "^\[protocol_regress\]" "$OUT/$DOM-protocol.log" | sed 's/^/     /'
    lc=$(docker inspect -f '{{json .HostConfig.LogConfig}}' "$(dc ps -q airlock)" 2>/dev/null)
    echo "$lc" | grep -q '"max-size":"10m"'; rec "concurrency: F2 airlock container log rotation (max-size 10m)" $? 0 0
  fi

  dc logs --no-log-prefix airlock >"$OUT/$DOM-airlock.log" 2>&1
  dc --profile controls down -v --remove-orphans >/dev/null 2>&1
done

echo "== total $((SECONDS-T_ALL))s" | tee -a "$OUT/summary.txt"
left=$(docker ps -a --filter "name=forgeair-$FORGEAIR_RUN-" -q | wc -l | tr -d ' ')
vols=$(docker volume ls -q --filter "name=forgeair-$FORGEAIR_RUN-" | wc -l | tr -d ' ')
[ "$left" = 0 ] && [ "$vols" = 0 ]; rec "cleanup: 0 containers and 0 volumes left (c=$left v=$vols)" $? 0 0
if [ -n "$VFK" ]; then
  vfk_stop_vms; rmdir "$VMWS" 2>/dev/null
  n=0; for p in $(cat "$OUT/vm.allpids" 2>/dev/null); do kill -0 "$p" 2>/dev/null && n=$((n+1)); done  # only VMs of this run
  [ "$n" = 0 ]; rec "cleanup: 0 vfkit VMs of this run left (n=$n)" $? 0 0
fi
[ "$FAIL" = 0 ] && echo "VALIDATE: PASS" | tee -a "$OUT/summary.txt" || echo "VALIDATE: FAIL" | tee -a "$OUT/summary.txt"
exit "$FAIL"
