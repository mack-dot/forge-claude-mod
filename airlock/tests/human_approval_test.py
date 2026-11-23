# human_approval_test.py - `require_human_approval: hardware` (part of run_tests.py; no Docker).
#
# A person signs one held call on the host (cli/forge_sign.py builds the bytes and signs with ssh-keygen); the
# airlock (engine/human_approval.py, through Airlock.tools_call) forwards it only after the signature verifies.
# LABEL: FINITE TEST with SOFTWARE ed25519 keys made in a temp folder at test time (no hardware in CI, no key in
# the repository). A FIDO2 sk-ssh-ed25519 key uses the same ssh-keygen -Y sign / -Y verify path; its touch is
# not tested here. Needs ssh-keygen (OpenSSH 8.2+).
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "engine"))
sys.path.insert(0, os.path.join(HERE, "..", "..", "cli"))
import forge_sign  # noqa: E402
import human_approval as H  # noqa: E402
from policy import Policy, PolicyError, Refusal  # noqa: E402

ENGINE = os.path.join(HERE, "..", "engine", "human_approval.py")


def keygen(d, name):
    k = os.path.join(d, name)
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", name, "-f", k], check=True)
    t, b = open(k + ".pub").read().split()[:2]
    return k, f'owner@forge.test namespaces="forge-call-approval" {t} {b}'


def cli(adir, *args, data=None):
    r = subprocess.run([sys.executable, "-I", ENGINE] + list(args), input=data, capture_output=True,
                       env={"FORGE_APPROVAL_DIR": adir, "PATH": os.environ.get("PATH", "/usr/bin:/bin")})
    return r.returncode, r.stdout.decode(), r.stderr.decode()


def wait_pending(adir, n=1, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        code, out, _ = cli(adir, "list")
        reqs = json.loads(out or "[]") if code == 0 else []
        if len(reqs) >= n:
            return reqs
        time.sleep(0.05)
    return []


def host_sign(key, req, namespace=forge_sign.CALL_NAMESPACE, tamper=None):
    """What `forge_sign.py call` does after the person types yes (minus the prompt and docker exec)."""
    r = dict(req)
    if tamper:
        r.update(tamper)
    return forge_sign.sign(key, forge_sign.call_request_bytes(r), namespace)


def in_thread(fn):
    box = {}

    def go():
        try:
            box["ok"] = fn()
        except Refusal as e:
            box["refusal"] = e.code
        except Exception as e:  # noqa: BLE001
            box["error"] = f"{type(e).__name__}: {e}"
    t = threading.Thread(target=go, daemon=True)
    t.start()
    return t, box


class FakeUpstream:
    def __init__(self):
        self.calls = []

    def request(self, method, params):
        self.calls.append(params)
        return {"content": [{"type": "text", "text": "done"}]}


def run(case):
    if not shutil.which("ssh-keygen"):
        case("human approval: ssh-keygen is on PATH", False)
        return
    tmp = tempfile.mkdtemp(prefix="forge-ha-test-")
    try:
        owner, owner_line = keygen(tmp, "owner")
        rogue, _ = keygen(tmp, "rogue")

        # policy
        doc = {"version": 1, "upstreams": [{"name": "repo", "transport": "stdio", "command": ["x"]}],
               "tools": {"allow": [{"tool": "push_branch", "require_human_approval": "hardware"}]}}
        case("human approval: policy without human_approval.allowed_signers is refused (fail closed)",
             _raises(lambda: Policy(dict(doc), environ={}), "allowed_signers is missing"))
        bad = dict(doc, tools={"allow": [{"tool": "push_branch", "require_human_approval": "yes"}]},
                   human_approval={"allowed_signers": [owner_line]})
        case("human approval: require_human_approval other than 'hardware' is refused",
             _raises(lambda: Policy(bad, environ={}), "must be hardware"))
        case("human approval: a malformed allowed_signers line is refused",
             _raises(lambda: Policy(dict(doc, human_approval={"allowed_signers": ["owner AAAA"]}), environ={}),
                     "is not"))
        pol = Policy(dict(doc, human_approval={"allowed_signers": [owner_line], "timeout_s": 3}), environ={})
        case("human approval: valid policy loads", pol.tools["push_branch"].human_approval
             and pol.approval_timeout_s == 3)
        case("human approval: host and airlock sign the same bytes",
             forge_sign.call_request_bytes({"v": 1, "run": "r", "call": "c", "step": "s", "tool": "t",
                                            "args_sha256": "h", "arguments": {}})
             == H.request_bytes({"v": 1, "run": "r", "call": "c", "step": "s", "tool": "t", "args_sha256": "h"}))

        # the gateway path: Airlock.tools_call with a fake upstream (no Docker)
        import gateway
        adir = os.path.join(tmp, "approvals")
        A = gateway.Airlock.__new__(gateway.Airlock)
        A.policy, A.canary, A.logs = pol, "PASSED", {}
        up = FakeUpstream()
        A.upstreams, A.route = {"repo": up}, {"push_branch": "repo"}
        A.upstream_tools = {"repo": {"push_branch": {"name": "push_branch", "inputSchema": {
            "type": "object", "properties": {"branch": {"type": "string"}}}}}}
        A.restart_lock = threading.Lock()
        A.desk = H.Desk(pol.approval_signers, 3, adir, poll_s=0.05)

        def call(branch):
            return A.tools_call({"name": "push_branch", "arguments": {"branch": branch}})

        # 1 positive: the owner signs exactly this call
        t, box = in_thread(lambda: call("main"))
        reqs = wait_pending(adir)
        held_before = len(up.calls)
        ok = bool(reqs) and reqs[0]["arguments"] == {"branch": "main"}
        if ok:
            cli(adir, "submit", reqs[0]["call"], data=host_sign(owner, reqs[0]).encode())
        t.join(10)
        case("human approval: held call is NOT forwarded before a signature; forwarded after the owner signs",
             ok and held_before == 0 and "ok" in box and len(up.calls) == 1, json.dumps(box))
        first = reqs[0] if reqs else None
        first_sig = host_sign(owner, first) if first else ""

        def attempt(name, sign_fn, want):
            n0 = len(up.calls)
            t, box = in_thread(lambda: call("main"))
            reqs = wait_pending(adir)
            if reqs and sign_fn:
                cli(adir, "submit", reqs[0]["call"], data=sign_fn(reqs[0]).encode())
            t.join(10)
            case("human approval: " + name, box.get("refusal") == want and len(up.calls) == n0,
                 json.dumps(box) + f" forwarded={len(up.calls) - n0}")

        attempt("rogue key signature: FORGE_HUMAN_APPROVAL_REFUSED, not forwarded",
                lambda r: host_sign(rogue, r), "FORGE_HUMAN_APPROVAL_REFUSED")
        attempt("owner signature over OTHER arguments: refused, not forwarded",
                lambda r: host_sign(owner, r, tamper={"args_sha256": H.args_sha256({"branch": "evil"})}),
                "FORGE_HUMAN_APPROVAL_REFUSED")
        attempt("owner signature of another step id: refused, not forwarded",
                lambda r: host_sign(owner, r, tamper={"step": "0" * 32}), "FORGE_HUMAN_APPROVAL_REFUSED")
        attempt("replay of the first call's valid signature: refused, not forwarded",
                lambda r: first_sig, "FORGE_HUMAN_APPROVAL_REFUSED")
        attempt("owner key, wrong namespace (forge-approval): refused, not forwarded",
                lambda r: host_sign(owner, r, namespace=forge_sign.APPROVAL_NAMESPACE),
                "FORGE_HUMAN_APPROVAL_REFUSED")
        attempt("garbage signature: refused, not forwarded", lambda r: "not a signature\n",
                "FORGE_HUMAN_APPROVAL_REFUSED")
        attempt("no signature: FORGE_HUMAN_APPROVAL_TIMEOUT after timeout_s, not forwarded", None,
                "FORGE_HUMAN_APPROVAL_TIMEOUT")
        code, out, _ = cli(adir, "list")
        case("human approval: no held call is left after refusals and the timeout", code == 0 and out.strip() == "[]")
        code, _, _ = cli(adir, "submit", "f" * 32, data=b"x")
        case("human approval: submit for an unknown call id is refused", code == 1)
        case("human approval: a call without require_human_approval is never held",
             not Policy(dict(doc, tools={"allow": [{"tool": "push_branch"}]}), environ={})
             .tools["push_branch"].human_approval)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _raises(fn, contains):
    try:
        fn()
    except PolicyError as e:
        return contains in str(e)
    return False
