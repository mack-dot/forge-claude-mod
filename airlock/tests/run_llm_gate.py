#!/usr/bin/env python3
"""The model proxy and its spending gate, end to end in Docker, with a FAKE provider and a FAKE
key (no real credential). Every Docker resource it makes is named llm-* and removed at the end.

Part 1 (the compose file that `forge up` generates, from forge_airlock.compose_doc, plus a fake
provider on the airlock's outside network):
  basic        canary PASS; no key in the agent's env, /proc or files; no route from the agent
               to the provider; JSON and SSE through the airlock; settlement = reported usage;
               provider error and cut stream keep the reservation; refusals; free paths
  key on wire  the fake provider saw the real key on every request and never the dummy
  kill         SIGKILL of the airlock while a request is PENDING: at restart it is ABORTED, spent
  concurrency  20 concurrent streams: exactly floor(room / R) forwarded, the rest refused, and
               the provider received exactly that many
  restart      the caps and the gross survive a restart of the airlock
  breach       the model key planted in the agent: canary BREACH; the model endpoint refuses
Part 2 (`forge_airlock.py up` itself): no --pass-env for the model key; --pass-env of another
value still works with its warning; --pass-env of the model key is refused.
Exit 0 only if every case passes. Label: FINITE TEST."""
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.abspath(os.path.join(HERE, ".."))
IMAGE = "llm-forge-airlock:local"
os.environ["FORGE_AIRLOCK_IMAGE"] = IMAGE
sys.path.insert(0, PKG)
sys.path.insert(0, os.path.join(PKG, "engine"))
import forge_airlock as F  # noqa: E402
from policy import Policy  # noqa: E402

KEY = "FAKE_llm_key_DEMO_ONLY_not_real_" + secrets.token_hex(4)
RESULTS, TIMES = [], []
T0 = time.time()


def case(name, cond, detail=""):
    RESULTS.append(bool(cond))
    print(("ok    " if cond else "FAIL  ") + name + ("" if cond else f"   {str(detail)[-600:]}"), flush=True)


def sh(cmd, env=None, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, env=env, **kw)


def make_repo(root):
    os.makedirs(os.path.join(root, "src"))
    open(os.path.join(root, "README.md"), "w").write("llm gate test\n")
    open(os.path.join(root, "src", "a.txt"), "w").write("hello\n")
    g = ["git", "-C", root, "-c", "user.name=t", "-c", "user.email=t@invalid", "-c", "commit.gpgsign=false"]
    sh(["git", "init", "-q", root])
    sh(g + ["add", "-A"])
    sh(g + ["commit", "-q", "-m", "init"])


def policy(provider, cap=12000):
    return {"version": 1,
            "upstreams": [{"name": "repo", "transport": "stdio", "user": 1000,
                           "command": ["python3", "-I", "/forge/mocks/mock_repo_mcp.py", "--root", "/ws",
                                       "--git-dir", "/state/filtered.git"]}],
            "tools": {"allow": [{"tool": "read_file", "upstream": "repo", "path_args": {"path": "read"}}]},
            "paths": {"hidden_paths": [".env*"]},
            "llm": {"provider_url": provider, "key_env": "LLM_TEST_KEY", "unit": "tokens", "session_cap": cap,
                    "daily_cap": 1000000, "per_request_max": 20000, "allowed_models": ["fake-model-1"]}}


def result_line(out, tag="RESULT"):
    for ln in out.splitlines():
        if ln.startswith(tag + " "):
            return json.loads(ln[len(tag) + 1:])
    return {}


# ---------------------------------------------------------------- part 1
def part1(tmp):
    repo = os.path.join(tmp, "repo")
    make_repo(repo)
    run_dir = os.path.join(tmp, "run")
    os.makedirs(os.path.join(run_dir, "agent"))
    os.makedirs(os.path.join(run_dir, "export"))
    json.dump({"mcpServers": {}}, open(os.path.join(run_dir, "agent", "mcp.json"), "w"))
    doc = policy("http://fakeprov:9000")
    ppath = os.path.join(tmp, "forge.yaml")
    json.dump(doc, open(ppath, "w"), indent=1)  # JSON is YAML
    pol = Policy(doc, resolve=False)
    stage = F.stage_repo(repo, os.path.join(run_dir, "source"))
    tag = secrets.token_hex(3)
    project, logs = f"llm-gate-{tag}", f"llm-gate-logs-{tag}"
    cdoc = F.compose_doc(pol, ppath, stage, run_dir, logs, IMAGE, ["LLM_KEY_SHA256", "LLM_KEY_LEN"], [],
                         own_agent_image=True, llm_session=f"llm-gate-{tag}")
    cdoc["services"]["fakeprov"] = {
        "image": IMAGE, "command": ["python3", "-I", "/forge/mocks/fake_provider.py", "9000"],
        "environment": [f"EXPECTED_KEY={KEY}"], "networks": ["outnet"],
        "healthcheck": {"test": ["CMD", "python3", "-c", "import socket; socket.create_connection(('127.0.0.1', 9000), 1)"],
                        "interval": "1s", "retries": 60}}
    cpath = os.path.join(run_dir, "compose.json")
    json.dump(cdoc, open(cpath, "w"), indent=1)
    env = dict(os.environ, LLM_TEST_KEY=KEY, LLM_KEY_SHA256=hashlib.sha256(KEY.encode()).hexdigest(),
               LLM_KEY_LEN=str(len(KEY)))
    dc = ["docker", "compose", "-p", project, "-f", cpath]
    ae = cdoc["services"]["agent"]["environment"]
    case("compose: agent env has ANTHROPIC_BASE_URL and only the dummy key; airlock env names the key",
         "ANTHROPIC_BASE_URL=http://airlock:8701" in ae and f"ANTHROPIC_AUTH_TOKEN={F.LLM_DUMMY_KEY}" in ae
         and not any(KEY in x or x.startswith("LLM_TEST_KEY") for x in ae)
         and "LLM_TEST_KEY" in cdoc["services"]["airlock"]["environment"], ae)

    def probe(phase, extra_env=None, timeout=600, canary=True):
        """canary=True: the first agent run after an airlock (re)start; the canary runs once per lifetime."""
        e = sum((["-e", f"{k}={v}"] for k, v in (extra_env or {}).items()), [])
        pre = ('python3 -I /forge/agent/canary.py >/tmp/c.log 2>&1 || { c=$?; tail -n 5 /tmp/c.log; exit $c; }; '
               'tail -n 1 /tmp/c.log; ') if canary else ""
        cmd = dc + ["run", "--rm", "-T"] + e + ["agent", "sh", "-c",
                                                pre + f'exec python3 -I /forge/agent/llm_probe.py {phase}']
        t = time.time()
        r = sh(cmd, env=env, timeout=timeout)
        TIMES.append((phase, round(time.time() - t, 1)))
        return r

    def fake_log():
        r = sh(dc + ["exec", "-T", "fakeprov", "python3", "-c", "import urllib.request,sys; "
                     "sys.stdout.write(urllib.request.urlopen('http://127.0.0.1:9000/fake/log').read().decode())"],
               env=env)
        try:
            return json.loads(r.stdout)
        except ValueError:
            return []

    def up_airlock():
        return sh(dc + ["up", "-d", "--wait", "airlock"], env=env, timeout=300).returncode

    try:
        sh(["docker", "volume", "create", logs])
        t = time.time()
        r = sh(dc + ["up", "-d", "--wait", "airlock", "egress", "fakeprov"], env=env, timeout=600)
        case(f"airlock, egress proxy and fake provider start ({time.time() - t:.1f} s)", r.returncode == 0, r.stderr)
        alog = sh(dc + ["logs", "--no-log-prefix", "airlock"], env=env).stdout
        case("airlock log: llm endpoint up, provider http://fakeprov:9000", "llm endpoint up on :8701" in alog, alog[-800:])

        r = probe("basic")
        print(r.stdout.rstrip())
        case("phase basic (in the agent container)", r.returncode == 0 and "[llm-probe] basic: PASS" in r.stdout,
             r.stdout[-1500:] + r.stderr[-500:])
        g_basic = result_line(r.stdout).get("gross")

        fl = fake_log()
        msgs = [e for e in fl if e["path"].startswith("/v1/")]
        case(f"key on the wire: the fake provider saw the real key on all {len(msgs)} requests, never the dummy, "
             "never an Authorization or Proxy-Authorization header",
             msgs and all(e["key_ok"] and not e["dummy_seen"] and not e["authorization"] and not e["proxy_auth_seen"]
                          for e in msgs), msgs[:3])
        n_before = sum(1 for e in fl if e["path"] == "/v1/messages")

        # ---- kill the airlock while a request is PENDING
        cmd = dc + ["run", "--rm", "-T", "agent", "python3", "-I", "/forge/agent/llm_probe.py", "slow"]
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
        slow = {}
        for ln in p.stdout:
            if ln.startswith("SLOW "):
                slow = json.loads(ln[5:])
                break
        seen = False
        for _ in range(60):
            if any((e.get("opts") or {}).get("delay") == "60" for e in fake_log()):
                seen = True
                break
            time.sleep(0.5)
        st = json.loads(sh(dc + ["exec", "-T", "airlock", "python3", "-c", "import urllib.request,sys; sys.stdout."
                                 "write(urllib.request.urlopen('http://127.0.0.1:8701/forge/llm/status').read().decode())"],
                           env=env).stdout or "{}")
        open_before = st.get("session", {}).get("open")
        sh(dc + ["kill", "-s", "KILL", "airlock"], env=env)
        try:
            p.wait(60)
        except subprocess.TimeoutExpired:
            p.kill()
        case(f"kill: the slow request reached the provider ({seen}) and was PENDING (open={open_before}) when "
             "the airlock got SIGKILL", seen and open_before == 1 and slow.get("reserved"), slow)
        t = time.time()
        rc = up_airlock()
        case(f"kill: the airlock starts again on the same log volume ({time.time() - t:.1f} s)", rc == 0)
        alog = sh(dc + ["logs", "--no-log-prefix", "airlock"], env=env).stdout
        case("kill: at restart the open reservation is settled as spent (log line)",
             "open reservation(s) at restart settled as spent" in alog, alog[-600:])
        exp = slow.get("gross_before", -1) + slow.get("reserved", 0)
        r = probe("check", {"EXPECT_GROSS": exp, "EXPECT_ABORTED": 3})
        print(r.stdout.rstrip())
        case(f"kill: after restart gross = {slow.get('gross_before')} + full reservation {slow.get('reserved')}; "
             "3 ABORTED (error, cut, kill)", r.returncode == 0, r.stdout[-1200:])
        case("kill: the basic phase's gross was the base of the slow request", slow.get("gross_before") == g_basic,
             (slow, g_basic))

        # ---- 20 concurrent
        n0 = sum(1 for e in fake_log() if e["path"] == "/v1/messages")
        r = probe("concurrency", canary=False)
        print(r.stdout.rstrip())
        conc = result_line(r.stdout)
        n1 = sum(1 for e in fake_log() if e["path"] == "/v1/messages")
        case("phase concurrency (20 concurrent streams)", r.returncode == 0, r.stdout[-1500:])
        case(f"concurrency: the provider received exactly the {conc.get('forwarded')} forwarded requests "
             f"({n1 - n0} new; refused ones never left the airlock)", n1 - n0 == conc.get("forwarded"),
             (n0, n1, conc, n_before))

        # ---- graceful restart
        t = time.time()
        sh(dc + ["restart", "airlock"], env=env, timeout=120)
        rc = up_airlock()
        r = probe("check", {"EXPECT_GROSS": conc.get("gross", -1)})
        print(r.stdout.rstrip())
        case(f"restart: the cap and the gross ({conc.get('gross')}) survive a restart; over-room request refused "
             f"({time.time() - t:.1f} s)", rc == 0 and r.returncode == 0, r.stdout[-1200:])

        # ---- planted breach: the model key in the agent (a fresh airlock: the canary runs once per lifetime)
        sh(dc + ["restart", "airlock"], env=env, timeout=120)
        up_airlock()
        r = sh(dc + ["run", "--rm", "-T", "-e", "PLANTED_MODEL_KEY", "agent", "python3", "-I",
                     "/forge/agent/canary.py"], env=dict(env, PLANTED_MODEL_KEY=KEY), timeout=600)
        case(f"breach: the model key planted in the agent's env -> canary exit {r.returncode} (expect 10)",
             r.returncode == 10 and "PLANTED_MODEL_KEY" in r.stdout, r.stdout[-600:])
        r = sh(dc + ["run", "--rm", "-T", "agent", "python3", "-c",
                     "import json,urllib.request,urllib.error as E\n"
                     "q=urllib.request.Request('http://airlock:8701/v1/messages',data=json.dumps({'model':'fake-model-1',"
                     "'max_tokens':5,'messages':[{'role':'user','content':'x'}]}).encode(),method='POST',"
                     "headers={'content-type':'application/json'})\n"
                     "try: urllib.request.urlopen(q,timeout=30); print('FORWARDED')\n"
                     "except E.HTTPError as e: print(e.code, e.read().decode()[:200])"], env=env, timeout=300)
        case(f"breach: after BREACH the model endpoint refuses: {r.stdout.strip()[:90]}",
             r.stdout.startswith("503") and "FORGE_CANARY_PENDING" in r.stdout, r.stdout + r.stderr)
        st = sh(dc + ["exec", "-T", "airlock", "python3", "-I", "-c", "import sys; sys.path.insert(0,'/forge/airlock');"
                      "import json,urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8701/forge/llm/status')"
                      ".read().decode())"], env=env).stdout
        try:
            s = json.loads(st)
        except ValueError:
            s = {}
        print("     final status: " + json.dumps(s)[:600])
        ses, day = s.get("session", {}), s.get("day", {})
        case(f"final: session gross {ses.get('gross')} <= cap {ses.get('cap')}, verdict {ses.get('proved_verdict')}; "
             f"day log gross {day.get('gross')} == session gross (one session today)",
             ses.get("proved_verdict") == "ACCEPT" and ses.get("gross", 1 << 62) <= ses.get("cap", 0)
             and day.get("proved_verdict") == "ACCEPT" and day.get("gross") == ses.get("gross"), s)
    finally:
        sh(dc + ["down", "-v", "--remove-orphans"], env=env, timeout=300)
        sh(["docker", "volume", "rm", logs])


# ---------------------------------------------------------------- part 2: forge up
def part2(tmp):
    fake = f"llm-fakeprov-up-{secrets.token_hex(3)}"
    r = sh(["docker", "run", "-d", "--name", fake, "-e", f"EXPECTED_KEY={KEY}", "-p", "127.0.0.1::9000", IMAGE,
            "python3", "-I", "/forge/mocks/fake_provider.py", "9000"])
    try:
        port = sh(["docker", "port", fake, "9000/tcp"]).stdout.strip().rsplit(":", 1)[-1]
        repo = os.path.join(tmp, "uprepo")
        make_repo(repo)
        json.dump(policy(f"http://host.docker.internal:{port}"), open(os.path.join(repo, "forge.yaml"), "w"), indent=1)
        env = dict(os.environ, LLM_TEST_KEY=KEY, LLM_KEY_SHA256=hashlib.sha256(KEY.encode()).hexdigest(),
                   FORGE_AIRLOCK_PREFIX="llm-up", FORGE_AIRLOCK_IMAGE=IMAGE)
        env.pop("ANTHROPIC_API_KEY", None)
        fa = [sys.executable, os.path.join(PKG, "forge_airlock.py")]
        t = time.time()
        r = sh(fa + ["up", "--repo", repo, "--agent-image", IMAGE, "--pass-env", "LLM_KEY_SHA256", "--",
                     "python3", "-I", "/forge/agent/llm_probe.py", "upcheck"], env=env, timeout=900, stdin=subprocess.DEVNULL)
        TIMES.append(("forge up", round(time.time() - t, 1)))
        print("\n".join("     " + x for x in (r.stdout + r.stderr).splitlines() if x.startswith(("ok", "FAIL", "forge-airlock"))))
        case(f"forge up: exit {r.returncode}, no --pass-env for the model key, call through the airlock "
             f"({time.time() - t:.1f} s)", r.returncode == 0 and "[llm-probe] upcheck: PASS" in r.stdout,
             r.stdout[-1500:] + r.stderr[-1500:])
        case("forge up: says where model access goes; --pass-env of another value still prints its warning",
             "model access through the airlock" in r.stderr and "WARNING: --pass-env LLM_KEY_SHA256" in r.stderr, r.stderr)
        r2 = sh(fa + ["up", "--repo", repo, "--agent-image", IMAGE, "--pass-env", "LLM_TEST_KEY", "--", "true"],
                env=env, timeout=600, stdin=subprocess.DEVNULL)
        case(f"forge up: --pass-env of the model key is refused (exit {r2.returncode})",
             r2.returncode != 0 and "model key of forge.yaml" in (r2.stderr + r2.stdout), r2.stderr[-400:])
        fl = json.loads(sh(["docker", "exec", fake, "python3", "-c", "import urllib.request,sys; sys.stdout.write("
                            "urllib.request.urlopen('http://127.0.0.1:9000/fake/log').read().decode())"]).stdout or "[]")
        case(f"forge up: the fake provider got {len(fl)} request(s), each with the real key and no dummy",
             len(fl) == 1 and all(e["key_ok"] and not e["dummy_seen"] for e in fl), fl)
        sh(fa + ["reset-logs", "--repo", repo], env=env)
    finally:
        sh(["docker", "rm", "-f", fake])


SMOKE = r"""
import json, urllib.request, urllib.error
def st():
    return json.load(urllib.request.urlopen('http://airlock:8701/forge/llm/status', timeout=30))['session']
g0 = st()['gross']
q = urllib.request.Request('http://airlock:8701/v1/messages', method='POST', data=json.dumps({
    'model': 'claude-opus-5-5', 'max_tokens': 20, 'messages': [{'role': 'user', 'content': 'Say ok.'}]}).encode(),
    headers={'content-type': 'application/json', 'anthropic-version': '2023-06-01', 'x-api-key': 'forge-airlock-no-key'})
try:
    r = urllib.request.urlopen(q, timeout=120); code, d = r.status, json.load(r)
except urllib.error.HTTPError as e:
    code, d = e.code, json.loads(e.read() or b'{}')
s = st()
print('SMOKE ' + json.dumps({'status': code, 'type': d.get('type'), 'stop_reason': d.get('stop_reason'),
      'usage': d.get('usage'), 'error_type': (d.get('error') or {}).get('type'), 'charged': s['gross'] - g0,
      'gross': s['gross'], 'cap': s['cap'], 'verdict': s['proved_verdict']}))
"""


def real_smoke():
    """ONE request with max_tokens 20 against the real API through the airlock, cap 3000 tokens.
    Runs only when ANTHROPIC_API_KEY is already exported; the key is never printed."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("real smoke: SKIPPED (ANTHROPIC_API_KEY is not set)")
        return 0
    tmp = tempfile.mkdtemp(prefix="llm-smoke-")
    tag = secrets.token_hex(3)
    project, logs = f"llm-smoke-{tag}", f"llm-smoke-logs-{tag}"
    try:
        repo, run_dir = os.path.join(tmp, "repo"), os.path.join(tmp, "run")
        make_repo(repo)
        os.makedirs(os.path.join(run_dir, "agent"))
        json.dump({"mcpServers": {}}, open(os.path.join(run_dir, "agent", "mcp.json"), "w"))
        doc = policy("https://api.anthropic.com", cap=3000)
        doc["llm"].update(key_env="ANTHROPIC_API_KEY", allowed_models=["claude-opus-5-5"], per_request_max=3000,
                          daily_cap=None)
        doc["llm"].pop("daily_cap")
        ppath = os.path.join(tmp, "forge.yaml")
        json.dump(doc, open(ppath, "w"))
        pol = Policy(doc, resolve=False)
        stage = F.stage_repo(repo, os.path.join(run_dir, "source"))
        cdoc = F.compose_doc(pol, ppath, stage, run_dir, logs, IMAGE, [], [], own_agent_image=True,
                             llm_session=project)
        cpath = os.path.join(run_dir, "compose.json")
        json.dump(cdoc, open(cpath, "w"))
        dc = ["docker", "compose", "-p", project, "-f", cpath]
        sh(["docker", "volume", "create", logs])
        if sh(dc + ["up", "-d", "--wait", "airlock", "egress"], timeout=300).returncode:
            print("real smoke: the airlock did not start")
            return 1
        r = sh(dc + ["run", "--rm", "-T", "agent", "sh", "-c", "python3 -I /forge/agent/canary.py >/tmp/c.log 2>&1 "
                     "|| { tail -n 3 /tmp/c.log; exit 9; }; env | grep -c '^ANTHROPIC_API_KEY=' ; exec python3 -c \"$0\"",
                     SMOKE], timeout=300)
        res = result_line(r.stdout, "SMOKE")
        print("real smoke: " + json.dumps(res))
        ok = res.get("status") == 200 and res.get("charged") == sum(
            (res.get("usage") or {}).get(k) or 0 for k in ("input_tokens", "output_tokens", "cache_creation_input_tokens",
                                                            "cache_read_input_tokens")) and r.stdout.startswith("0")
        print(f"real smoke: {'PASS' if ok else 'FAIL'} (agent env had ANTHROPIC_API_KEY lines: {r.stdout[:1]})")
        return 0 if ok else 1
    finally:
        sh(["docker", "compose", "-p", project, "-f", os.path.join(tmp, "run", "compose.json"), "down", "-v"], timeout=300)
        sh(["docker", "volume", "rm", logs])
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    if "--real-smoke" in sys.argv:
        F.build_images(False)
        try:
            return real_smoke()
        finally:
            sh(["docker", "rmi", IMAGE])
    t = time.time()
    F.build_images(False)
    TIMES.append(("image build", round(time.time() - t, 1)))
    tmp = tempfile.mkdtemp(prefix="llm-gate-")
    try:
        part1(tmp)
        part2(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        left = sh(["sh", "-c", "docker ps -aq --filter name=llm- | wc -l; docker volume ls -q | grep -c '^llm-'; "
                               "docker network ls -q --filter name=llm- | wc -l"]).stdout.split()
        case(f"cleanup: llm-* containers/volumes/networks left: {'/'.join(left)}", left == ["0", "0", "0"], left)
        sh(["docker", "rmi", IMAGE])
    print("timings (s): " + ", ".join(f"{k} {v}" for k, v in TIMES))
    print(f"llm gate: {sum(RESULTS)}/{len(RESULTS)} ok in {time.time() - T0:.1f} s")
    return 0 if RESULTS and all(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
