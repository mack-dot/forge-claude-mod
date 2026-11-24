"""Unit regressions of the in-box red team (M2.3) that need no Docker; run by airlock/tests/run_tests.py.
  - export: a FIFO, a socket in the workspace are refused without blocking (finding IB3);
  - compose (forge up): the agent gets no bind mount of a host folder (finding IB2), the old
    form is kept only for the harnesses that ask for it;
  - probe helpers: mountinfo host-path detection, secret-like env names; the harness needle
    search attributes a hit to its family and never misses a planted needle.
Each check has a planted negative control."""
import json
import os
import shutil
import socket
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(PKG, "engine"))
sys.path.insert(0, os.path.join(PKG, "agent"))
sys.path.insert(0, PKG)
sys.path.insert(0, HERE)


def _no_block(fn, seconds=5):
    box = {}

    def go():
        try:
            box["v"] = fn()
        except Exception as e:  # noqa: BLE001
            box["e"] = e
    t = threading.Thread(target=go, daemon=True)
    t.start()
    t.join(seconds)
    return (not t.is_alive()), box


def run(case):
    import export_changes as X
    d = tempfile.mkdtemp(prefix="forge-ib-unit-")
    try:
        fifo, sock, reg = os.path.join(d, "pipe.ts"), os.path.join(d, "s.ts"), os.path.join(d, "r.ts")
        os.mkfifo(fifo)
        s = socket.socket(socket.AF_UNIX)
        s.bind(sock)
        open(reg, "w").write("export const r = 1\n")
        case("inbox IB3: export classifies a FIFO and a socket as special (not exported), a file as regular",
             X.special_kind(fifo) == "a FIFO" and X.special_kind(sock) == "a socket" and X.special_kind(reg) is None)
        done, box = _no_block(lambda: X.read_regular(fifo))
        case("inbox IB3: reading a FIFO in the export returns at once with an error (no writer, no hang)",
             done and isinstance(box.get("e"), OSError))
        done, box = _no_block(lambda: X.read_regular(reg))
        case("inbox IB3: a regular file is read with its content and mode",
             done and box.get("v") == (b"export const r = 1\n", "100644"))
        # negative control: the old read (open().read()) on the same FIFO DOES block
        done, _ = _no_block(lambda: open(fifo, "rb").read(), seconds=1)
        case("inbox IB3 negative control: the old open().read() on a FIFO blocks (the planted hang is real)",
             not done)
        if not done:  # unblock the daemon thread: open the write end once
            try:
                os.close(os.open(fifo, os.O_WRONLY | os.O_NONBLOCK))
            except OSError:
                pass
        s.close()
    finally:
        shutil.rmtree(d, ignore_errors=True)

    import forge_airlock as F
    from policy import Policy
    doc = {"version": 1, "upstreams": [{"name": "fs", "transport": "stdio", "command": ["node", "x.js", "/ws"]}],
           "tools": {"allow": [{"tool": "read_file", "upstream": "fs", "path_args": {"path": "read"}}]},
           "paths": {"hidden_paths": ["src/proprietary/**"], "readonly_paths": ["tests/**"]}}
    pol = Policy(doc, resolve=False)
    host = "/" + "Users/someone/plugins/forge/run"
    for own in (False, True):
        new = F.compose_doc(pol, "/p/forge.yaml", "/stage", host, "logs", "img", [], ["tests"], own_agent_image=own,
                            host_binds=False)
        vols = new["services"]["agent"]["volumes"]
        binds = [v for v in vols if isinstance(v, str) and v.split(":", 1)[0].startswith("/")]
        txt = json.dumps(new["services"]["agent"])
        case(f"inbox IB2: forge up's agent has no host bind mount (own image {own}): /forge-run"
             + (" and /forge/agent" if own else "") + " come from the agentrun volume",
             not binds and host not in txt and F.HERE not in txt and "seeder" in new["services"]
             and "agentrun" in new["volumes"]
             and {v["target"] for v in vols if isinstance(v, dict) and v["source"] == "agentrun"}
             == ({"/forge-run", "/forge/agent"} if own else {"/forge-run"}), vols)
    old = F.compose_doc(pol, "/p/forge.yaml", "/stage", host, "logs", "img", [], [], own_agent_image=True)
    case("inbox IB2 negative control: the old form (host_binds=True) binds host folders and is detected",
         any(isinstance(v, str) and v.startswith(host) for v in old["services"]["agent"]["volumes"]))

    import inbox_probe as I
    mi = ("208 198 254:1 /docker/volumes/x_ws/_data /workspace rw - ext4 /dev/vda1 rw\n"
          "133 200 0:81 /null /proc/kcore rw - tmpfs tmpfs rw\n"
          "212 198 254:1 /var/lib/docker/containers/abc/hosts /etc/hosts ro - ext4 /dev/sda1 rw\n")
    case("inbox probe: mountinfo of volumes, proc masks and engine files shows no host path",
         I.host_path_mounts(mi) == [])
    planted = mi + "209 198 0:50 /" + "home/someone/.claude/plugins/agent /forge/agent ro - ext4 /dev/sda1 rw\n"
    case("inbox probe negative control: a planted host bind in mountinfo is found",
         [t for t, _ in I.host_path_mounts(planted)] == ["/forge/agent"])
    case("inbox probe: secret-like env names (the canary token, *_API_KEY) found; the public GPG_KEY and the "
         "dummy ANTHROPIC_AUTH_TOKEN not",
         sorted(I.secret_like({"GPG_KEY": "x", "ANTHROPIC_AUTH_TOKEN": "x", "PATH": "x", "FORGE_CANARY_TOKEN": "x",
                               "PAY_API_KEY": "x"})) == ["FORGE_CANARY_TOKEN", "PAY_API_KEY"])

    import harness as H
    tok = "f" * 48
    hid = H.hidden_texts()[0][1].splitlines()[1].strip()
    out = b"[dump git] clean line\n[dump fd] " + hid + b"\nplain " + tok.encode() + b"\n[dump env] ok\n"
    hits = H.find_needles(out, tok)
    case("inbox harness: a planted hidden line and the token are found and attributed (fd, other)",
         {(f, lab.split(" of ")[0] if "hidden line" in lab else lab) for f, lab, _ in hits}
         >= {("fd", "hidden line 2"), ("other", "canary token")}, hits)
    case("inbox harness: a clean dump has no needle",
         H.find_needles(b"[dump git] forge-filtered commit 1 of 5\n[dump env] HOME=/tmp\n", tok) == [])
