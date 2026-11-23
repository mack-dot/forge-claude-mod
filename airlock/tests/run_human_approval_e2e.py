#!/usr/bin/env python3
"""End-to-end test of `require_human_approval: hardware` through `forge up` (needs Docker with Compose v2).

The box runs a script that calls the held tool `write_file` three times through the airlock. On the host,
`cli/forge_sign.py call` (driven through a pseudo-terminal that types `yes`) signs:
  call 1 with the owner key            -> forwarded (the file arrives in the box's workspace);
  call 2 with a rogue key              -> FORGE_HUMAN_APPROVAL_REFUSED, not forwarded;
  call 3 not at all                    -> FORGE_HUMAN_APPROVAL_TIMEOUT after timeout_s, not forwarded.
Then the review prompt is answered n (nothing synced). LABEL: FINITE TEST with SOFTWARE ed25519 keys made in a
temp folder at test time; a FIDO2 key uses the same `ssh-keygen -Y sign` path (its touch is not tested).
Uses its own image tag and project prefix (FORGE_AIRLOCK_IMAGE, FORGE_AIRLOCK_PREFIX) unless they are set."""
import hashlib
import json
import os
import pty
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.abspath(os.path.join(HERE, ".."))
SIGN = os.path.join(PKG, "..", "cli", "forge_sign.py")
os.environ.setdefault("FORGE_AIRLOCK_IMAGE", "forge-airlock-m3:local")
os.environ.setdefault("FORGE_AIRLOCK_PREFIX", "forgeha")
RESULTS = []

POLICY = """version: 1
upstreams:
  - name: fs
    transport: stdio
    command: [node, /opt/mcpfs/node_modules/@modelcontextprotocol/server-filesystem/dist/index.js, /ws]
    user: 1000
tools:
  allow:
    - {tool: read_text_file, upstream: fs, path_args: {path: read}}
    - {tool: write_file, upstream: fs, path_args: {path: write}, require_human_approval: hardware}
human_approval:
  timeout_s: 20
  allowed_signers:
    - '%s'
"""

SCRIPT = r"""
set -u
m() { python3 -I /forge/agent/mcp_cli.py call write_file "{\"path\":\"$1\",\"content\":\"$2\n\"}"; }
r1=$(m held1.txt owner-signed 2>&1); echo "CALL1: $r1"
[ -f held1.txt ] && echo "CALL1-FILE: present" || echo "CALL1-FILE: absent"
r2=$(m held2.txt rogue-signed 2>&1); echo "CALL2: $r2"
[ -f held2.txt ] && echo "CALL2-FILE: present" || echo "CALL2-FILE: absent"
r3=$(m held3.txt never-signed 2>&1); echo "CALL3: $r3"
[ -f held3.txt ] && echo "CALL3-FILE: present" || echo "CALL3-FILE: absent"
echo "BOX-DONE"
"""


def case(name, ok, detail=""):
    RESULTS.append(ok)
    print(("ok    " if ok else "FAIL  ") + name + ("" if ok else f"   {detail}"), flush=True)


def keygen(d, name):
    k = os.path.join(d, name)
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", name, "-f", k], check=True)
    t, b = open(k + ".pub").read().split()[:2]
    return k, f'owner@forge.test namespaces="forge-call-approval" {t} {b}'


def pty_run(argv, answers, env=None):
    """Run argv under a pseudo-terminal; answer each (prompt bytes, text) in order. Returns (exit, output)."""
    pid, fd = pty.fork()
    if pid == 0:
        os.execve(argv[0], argv, env or dict(os.environ))
    buf, i, pos = b"", 0, 0
    while True:
        try:
            chunk = os.read(fd, 4096)
        except OSError:
            break
        if not chunk:
            break
        buf += chunk
        if i < len(answers) and answers[i][0] in buf[pos:]:
            os.write(fd, (answers[i][1] + "\n").encode())
            pos, i = len(buf), i + 1
    _, status = os.waitpid(pid, 0)
    return os.waitstatus_to_exitcode(status), buf.decode("utf-8", "replace")


def held(repo):
    sys.path.insert(0, os.path.join(PKG, "..", "cli"))
    import forge_sign
    for p in forge_sign.airlock_projects(repo):
        code, out, _ = forge_sign.desk(p, "list")
        if code == 0 and json.loads(out or "[]"):
            return json.loads(out)
    return []


def signed_call(out):
    m = re.search(r"for call ([0-9a-f]{32})", out)
    return m.group(1) if m else None


def host_side(repo, owner, rogue, log):
    """The person on the host: sign call 1 with the owner key, call 2 with a rogue key, leave call 3."""
    for n, key in ((1, owner), (2, rogue)):
        end = time.time() + 240
        while time.time() < end and not held(repo):
            time.sleep(0.5)
        code, out = pty_run([sys.executable, SIGN, "call", "--repo", repo, "--key", key, "--once"],
                            [(b"Type yes:", "yes")])
        log.append((n, code, out))
        while time.time() < end and signed_call(out) in {r["call"] for r in held(repo)}:
            time.sleep(0.3)  # wait until the airlock has decided THIS call (the next one may already be held)


def main():
    if not shutil.which("docker"):
        print("FAIL  docker is not on PATH")
        return 1
    tmp = tempfile.mkdtemp(prefix="forge-ha-e2e-")
    owner, owner_line = keygen(tmp, "owner")
    rogue, _ = keygen(tmp, "rogue")
    repo = os.path.join(tmp, "repo")
    os.makedirs(repo)
    open(os.path.join(repo, "README.md"), "w").write("held-call test\n")
    open(os.path.join(repo, "forge.yaml"), "w").write(POLICY % owner_line)
    g = ["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false"]
    subprocess.run(["git", "init", "-q", "-b", "main", repo], check=True)
    subprocess.run(g + ["add", "-A"], check=True)
    subprocess.run(g + ["commit", "-q", "-m", "fixture"], check=True)
    key = hashlib.sha256(os.path.realpath(repo).encode()).hexdigest()[:12]
    log = []
    t = threading.Thread(target=host_side, args=(repo, owner, rogue, log), daemon=True)
    t.start()
    t0 = time.time()
    try:
        code, out = pty_run([sys.executable, os.path.join(PKG, "forge_airlock.py"), "up", "--repo", repo, "--",
                             "sh", "-c", SCRIPT], [(b"[y/N]", "n")])
        secs = time.time() - t0
        t.join(5)
        open(os.path.join(tmp, "up.log"), "w").write(out)
        lines = {l.split(":", 1)[0].strip(): l.split(":", 1)[1].strip() for l in out.replace("\r", "").split("\n")
                 if l.startswith("CALL")}
        case("e2e: the box ran to the end", "BOX-DONE" in out, out[-1500:])
        case("e2e: call 1 signed by the owner key on the host -> forwarded, file written",
             "REFUSED" not in lines.get("CALL1", "REFUSED") and lines.get("CALL1-FILE") == "present", json.dumps(lines))
        case("e2e: call 2 signed by a rogue key -> FORGE_HUMAN_APPROVAL_REFUSED, not forwarded",
             "FORGE_HUMAN_APPROVAL_REFUSED" in lines.get("CALL2", "") and lines.get("CALL2-FILE") == "absent",
             json.dumps(lines))
        case("e2e: call 3 never signed -> FORGE_HUMAN_APPROVAL_TIMEOUT, not forwarded",
             "FORGE_HUMAN_APPROVAL_TIMEOUT" in lines.get("CALL3", "") and lines.get("CALL3-FILE") == "absent",
             json.dumps(lines))
        case("e2e: host CLI showed the exact arguments and their hash matched",
             len(log) == 2 and all("matches the arguments above" in o for _, _, o in log),
             json.dumps([(n, c, o[-400:]) for n, c, o in log]))
        case("e2e: answer n -> nothing synced back", not os.path.exists(os.path.join(repo, "held1.txt")))
        print(f"measured: forge up took {secs:.1f} s (includes the 20 s timeout of call 3); exit {code}")
    finally:
        subprocess.run(["docker", "volume", "rm", "-f", f"forge-airlock-logs-{key}"], capture_output=True)
        print(f"logs: {tmp}")
    failed = RESULTS.count(False)
    print(f"{len(RESULTS)} cases, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
