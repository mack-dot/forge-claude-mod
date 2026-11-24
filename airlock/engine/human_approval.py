# human_approval.py - `require_human_approval: hardware` for one airlock tool.
#
# The airlock holds such a call until a person signs the exact call on the host with a key that only a person can
# use (a FIDO2 security key: touch; or a Secure Enclave key), and the signature verifies against the
# `human_approval.allowed_signers` lines of forge.yaml (read-only in the box, never synced back). The signed bytes
# name the run, a fresh call id, the step id, the tool and the SHA-256 of the exact arguments the upstream will
# receive, so a signature is good for one call only. Namespace: forge-call-approval.
#
# Host side: `cli/forge_sign.py call` (lists the held calls through `docker compose exec`, asks `yes`, signs with
# `ssh-keygen -Y sign`, hands the signature back with `human_approval.py submit`). The agent cannot reach this
# folder (the state volume is mounted only in the airlock); even if it could, it cannot make a valid signature.
#
# Label: NATIVE CHECK (hand-written; the signature check is OpenSSH's `ssh-keygen -Y verify`, not proved).
#
#   human_approval.py list            JSON list of held calls (for the host)
#   human_approval.py submit CALL_ID  signature on stdin; the waiting call checks it
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import time

NAMESPACE = "forge-call-approval"
DIR = os.environ.get("FORGE_APPROVAL_DIR", "/state/approvals")
CALL_RE = re.compile(r"[0-9a-f]{32}")
KEY_TYPES = ("sk-ssh-ed25519@openssh.com", "sk-ecdsa-sha2-nistp256@openssh.com", "ssh-ed25519",
             "ecdsa-sha2-nistp256", "ecdsa-sha2-nistp384", "ecdsa-sha2-nistp521", "rsa-sha2-256", "rsa-sha2-512",
             "ssh-rsa")
MAX_SIG = 16384

try:
    from policy import Refusal
except ImportError:  # the CLI below runs without the engine on its path
    class Refusal(Exception):
        def __init__(self, code, message, **data):
            super().__init__(f"{code}: {message}")
            self.code, self.message, self.data = code, message, data


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
            + "\n").encode("ascii")


def args_sha256(args):
    return hashlib.sha256(json.dumps(args, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                                     allow_nan=False).encode()).hexdigest()


def request_bytes(req):
    """Exactly what the person signs (cli/forge_sign.py call_request_bytes builds the same bytes)."""
    return canonical({k: req[k] for k in ("v", "run", "call", "step", "tool", "args_sha256")})


def valid_signer_line(line):
    parts = line.split()
    return len(parts) >= 3 and any(p in KEY_TYPES for p in parts[1:-1])


def verify(signers_lines, message, sig_text):
    """(ok, detail). ssh-keygen -Y find-principals, then -Y verify for each principal it names. Fails closed."""
    if "-----BEGIN SSH SIGNATURE-----" not in (sig_text or ""):
        return False, "no SSH signature"
    with tempfile.TemporaryDirectory(prefix="forge-call-sig-") as tmp:
        signers, sig = os.path.join(tmp, "allowed_signers"), os.path.join(tmp, "call.sig")
        with open(signers, "w", encoding="utf-8") as f:
            f.write("\n".join(signers_lines) + "\n")
        with open(sig, "w", encoding="ascii", errors="replace") as f:
            f.write(sig_text)
        try:
            fp = subprocess.run(["ssh-keygen", "-Y", "find-principals", "-s", sig, "-f", signers],
                                capture_output=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as e:
            return False, f"ssh-keygen: {e}"
        principals = [p.strip() for p in fp.stdout.decode(errors="replace").splitlines() if p.strip()]
        if fp.returncode != 0 or not principals:
            return False, "the signing key is not in human_approval.allowed_signers"
        why = ""
        for p in principals:
            r = subprocess.run(["ssh-keygen", "-Y", "verify", "-f", signers, "-I", p, "-n", NAMESPACE, "-s", sig],
                               input=message, capture_output=True, timeout=30)
            if r.returncode == 0 and f'Good "{NAMESPACE}" signature'.encode() in r.stdout + r.stderr:
                return True, f"signed by {p}"
            why = (r.stdout + r.stderr).decode(errors="replace").strip()[-200:]
        return False, "ssh-keygen -Y verify refused the signature: " + why


class Desk:
    """Holds calls that need a person's signature. One per airlock; `run` is fresh per airlock start."""

    def __init__(self, signers_lines, timeout_s=300, root=DIR, poll_s=0.2):
        self.signers, self.timeout_s, self.root, self.poll_s = list(signers_lines), timeout_s, root, poll_s
        self.run = secrets.token_hex(8)
        for d in ("pending", "signed"):
            os.makedirs(os.path.join(root, d), mode=0o700, exist_ok=True)
            for old in os.listdir(os.path.join(root, d)):  # calls of an earlier run can never be approved
                os.remove(os.path.join(root, d, old))

    def _paths(self, call):
        return os.path.join(self.root, "pending", call + ".json"), os.path.join(self.root, "signed", call + ".sig")

    def hold(self, tool, fwd_args, step=None):
        """Blocks until a valid signature over this exact call arrives (returns the signer), or refuses."""
        call = secrets.token_hex(16)
        req = {"v": 1, "run": self.run, "call": call, "step": step or call, "tool": tool,
               "args_sha256": args_sha256(fwd_args), "arguments": fwd_args, "held_at": int(time.time())}
        pend, sigp = self._paths(call)
        tmp = pend + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(req, f, sort_keys=True)
        os.replace(tmp, pend)
        print(f"[airlock] HELD {tool} call {call}: waiting up to {self.timeout_s} s for a person's signature "
              f"(host: forge_sign.py call)", flush=True)
        deadline = time.monotonic() + self.timeout_s
        try:
            while time.monotonic() < deadline:
                if os.path.exists(sigp):
                    with open(sigp, encoding="ascii", errors="replace") as f:
                        sig = f.read(MAX_SIG)
                    ok, detail = verify(self.signers, request_bytes(req), sig)
                    if not ok:
                        raise Refusal("FORGE_HUMAN_APPROVAL_REFUSED", f"call {call}: {detail}", call=call)
                    print(f"[airlock] APPROVED {tool} call {call}: {detail}", flush=True)
                    return detail
                time.sleep(self.poll_s)
            raise Refusal("FORGE_HUMAN_APPROVAL_TIMEOUT", f"call {call}: no valid signature within "
                          f"{self.timeout_s} s", call=call)
        finally:
            for p in (pend, sigp):
                if os.path.exists(p):
                    os.remove(p)


def main(argv):
    if len(argv) == 2 and argv[1] == "list":
        d = os.path.join(DIR, "pending")
        out = []
        for name in sorted(os.listdir(d)) if os.path.isdir(d) else []:
            if name.endswith(".json"):
                with open(os.path.join(d, name), encoding="utf-8") as f:
                    out.append(json.load(f))
        print(json.dumps(out))
        return 0
    if len(argv) == 3 and argv[1] == "submit":
        call = argv[2]
        if not CALL_RE.fullmatch(call) or not os.path.exists(os.path.join(DIR, "pending", call + ".json")):
            print(f"no held call {call[:40]}", file=sys.stderr)
            return 1
        sig = sys.stdin.read(MAX_SIG)
        tmp = os.path.join(DIR, "signed", call + ".tmp")
        with open(tmp, "w", encoding="ascii", errors="replace") as f:
            f.write(sig)
        os.replace(tmp, os.path.join(DIR, "signed", call + ".sig"))
        print(f"signature handed to the airlock for call {call}; the airlock checks it itself")
        return 0
    print("usage: human_approval.py list | submit CALL_ID  (signature on stdin)", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
