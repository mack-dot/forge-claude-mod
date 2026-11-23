#!/usr/bin/env python3
"""Unit tests of forge-airlock that need no Docker: policy validation, path rules (hidden,
visible, read-only), the diff sync-back filter, the persistent verifier (memo on and off give
the SAME verdict as a fresh subprocess), gate decisions through it, and the forge.yaml draft
(no secret values). Needs python3 and git; PyYAML is optional (YAML parsing cases are skipped
without it). Prints one line per case; exit 0 only if every case passes. Label: FINITE TEST."""
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.join(HERE, "..")
sys.path.insert(0, os.path.join(PKG, "engine"))
sys.path.insert(0, PKG)
import draft as D  # noqa: E402
import gatelog  # noqa: E402
import records as R  # noqa: E402
import sync  # noqa: E402
from policy import Policy, PolicyError, Refusal  # noqa: E402

RESULTS = []


def case(name, ok, detail=""):
    RESULTS.append(ok)
    print(("ok    " if ok else "FAIL  ") + name + ("" if ok else f"   {detail}"), flush=True)


def raises(fn, exc=PolicyError, contains=""):
    try:
        fn()
    except exc as e:
        return contains in str(e) or contains in getattr(e, "code", "")
    return False


def base_doc(**over):
    d = {"version": 1,
         "upstreams": [{"name": "fs", "transport": "stdio", "command": ["node", "x.js", "/ws"]},
                       {"name": "pay", "transport": "http", "url": "http://pay:1/mcp",
                        "headers": {"Authorization": "Bearer ${PAY_KEY}"}}],
         "tools": {"allow": [{"tool": "read_file", "upstream": "fs", "path_args": {"path": "read"}},
                             {"tool": "write_file", "upstream": "fs", "path_args": {"path": "write"}}],
                   "deny": ["delete_path"],
                   "gate": [{"tool": "charge", "upstream": "pay", "unit": "value", "value_field": "amount",
                             "cap": 1000, "expires_in_s": 3600}]},
         "paths": {"hidden_paths": ["src/proprietary/**", ".env*"], "readonly_paths": ["tests/**"]}}
    d.update(over)
    return d


def test_policy():
    env = {"PAY_KEY": "FAKE_pay_key_not_real_1234"}
    p = Policy(base_doc(), environ=env)
    case("policy: a valid forge.yaml loads", len(p.tools) == 3 and p.deny == ["delete_path"])
    case("policy: ${PAY_KEY} resolves on the airlock side only",
         p.upstreams["pay"]["headers"]["Authorization"] == "Bearer FAKE_pay_key_not_real_1234")
    case("policy: the host side keeps the NAME (resolve=False reads no secret)",
         Policy(base_doc(), resolve=False).upstreams["pay"]["headers"]["Authorization"] == "Bearer ${PAY_KEY}"
         and Policy(base_doc(), resolve=False).env_names == ["PAY_KEY"])
    case("policy: a named secret that is not set stops the start (fail closed)",
         raises(lambda: Policy(base_doc(), environ={}), contains="PAY_KEY is not set"))
    case("policy: resolved secrets are known to the canary", "FAKE_pay_key_not_real_1234" in p.secrets())
    bad = base_doc()
    bad["tools"]["deny"] = ["read_file"]
    case("policy: allow and deny of one tool is refused", raises(lambda: Policy(bad, environ=env), contains="both"))
    case("policy: an unknown key is refused", raises(lambda: Policy(base_doc(extra=1), environ=env),
                                                     contains="unknown keys"))
    case("policy: version 2 is refused", raises(lambda: Policy(base_doc(version=2), environ=env)))
    g = base_doc()
    g["tools"]["gate"][0].pop("expires_in_s")
    case("policy: a gate without expiry is refused", raises(lambda: Policy(g, environ=env), contains="exactly one"))
    g = base_doc()
    g["tools"]["gate"][0].pop("value_field")
    case("policy: unit=value without value_field is refused", raises(lambda: Policy(g, environ=env)))
    case("policy: agent.egress_allow must be host:port",
         raises(lambda: Policy(base_doc(agent={"egress_allow": ["https://x"]}), environ=env), contains="host:port")
         and Policy(base_doc(agent={"egress_allow": ["api.anthropic.com:443"]}), environ=env).egress_allow
         == ["api.anthropic.com:443"])
    case("policy: forge.yaml itself is always read-only", p.readonly.match("forge.yaml"))
    # default deny and argument rules
    case("policy: an unlisted tool is refused FORGE_TOOL_BLOCKED",
         raises(lambda: p.check_call("rotate", {}), Refusal, "FORGE_TOOL_BLOCKED"))
    case("policy: a denied tool is refused FORGE_TOOL_BLOCKED",
         raises(lambda: p.check_call("delete_path", {"path": "a"}), Refusal, "FORGE_TOOL_BLOCKED"))
    try:
        import yaml  # noqa: F401
        tmp = tempfile.mkdtemp()
        open(os.path.join(tmp, "forge.yaml"), "w").write(D.HEADER + D.to_yaml(base_doc()))
        ok = Policy.load(os.path.join(tmp, "forge.yaml"), resolve=False).env_names == ["PAY_KEY"]
        r = subprocess.run([sys.executable, os.path.join(PKG, "engine", "policy.py"), "check",
                            os.path.join(tmp, "forge.yaml")], capture_output=True, text=True)
        case("policy: the YAML that draft.to_yaml writes parses back to the same document",
             ok and json.loads(r.stdout)["doc"] == base_doc() and r.returncode == 0)
        open(os.path.join(tmp, "forge.yaml"), "w").write("version: 1\nupstreams: []\ntools: {}\n")
        r = subprocess.run([sys.executable, os.path.join(PKG, "engine", "policy.py"), "check",
                            os.path.join(tmp, "forge.yaml")], capture_output=True, text=True)
        case("policy: policy.py check exits 2 on an invalid file", r.returncode == 2 and '"ok": false' in r.stdout)
        shutil.rmtree(tmp)
    except ImportError:
        print("skip  policy: YAML round trip (PyYAML not installed on this host; it is in the image)")


def test_paths():
    tmp = tempfile.mkdtemp()
    for rel in ["src/app.ts", "src/proprietary/core.ts", "src/ui/button.ts", "src/ui/secret.ts", "tests/a.test.ts",
                "README.md", ".env.local", "deep/x/.env"]:
        os.makedirs(os.path.dirname(os.path.join(tmp, rel)) or tmp, exist_ok=True)
        open(os.path.join(tmp, rel), "w").write("x\n")
    os.symlink("proprietary/core.ts", os.path.join(tmp, "src/link.ts"))
    doc = base_doc()
    doc["paths"]["mcp_root"] = tmp
    p = Policy(doc, environ={"PAY_KEY": "k" * 10})
    case("paths: hidden directory pattern matches files below it", p.is_hidden("src/proprietary/core.ts"))
    case("paths: a basename pattern (.env*) matches at any depth", p.is_hidden(".env.local") and p.is_hidden("deep/x/.env"))
    case("paths: a public file is visible", not p.is_hidden("src/app.ts"))
    case("paths: '..' that leaves the workspace is refused FORGE_PATH_OUTSIDE",
         raises(lambda: p.check_path("../etc/passwd", "read"), Refusal, "FORGE_PATH_OUTSIDE"))
    case("paths: an absolute path outside the workspace is refused",
         raises(lambda: p.check_path("/etc/passwd", "read"), Refusal, "FORGE_PATH_OUTSIDE"))
    case("paths: a symlink to hidden code is refused after realpath()",
         raises(lambda: p.check_path("src/link.ts", "read"), Refusal, "FORGE_PATH_HIDDEN"))
    case("paths: 'src/../src/proprietary/core.ts' is refused",
         raises(lambda: p.check_path("src/../src/proprietary/core.ts", "read"), Refusal, "FORGE_PATH_HIDDEN"))
    case("paths: a write to a read-only path is refused FORGE_PATH_READONLY",
         raises(lambda: p.check_path("tests/a.test.ts", "write"), Refusal, "FORGE_PATH_READONLY")
         and raises(lambda: p.check_path("tests/new.ts", "write"), Refusal, "FORGE_PATH_READONLY")
         and raises(lambda: p.check_path("forge.yaml", "write"), Refusal, "FORGE_PATH_READONLY"))
    case("paths: a read of a read-only path is allowed", p.check_path("tests/a.test.ts", "read") == "tests/a.test.ts")
    case("paths: .git is refused", raises(lambda: p.check_path(".git/config", "read"), Refusal, "FORGE_PATH_REFUSED"))
    case("paths: git_revpath with a hidden path is refused",
         raises(lambda: p.check_revpath("HEAD~2:.env.local"), Refusal, "FORGE_PATH_HIDDEN"))
    vdoc = base_doc()
    vdoc["paths"] = {"hidden_paths": ["src/ui/secret.ts"], "visible_paths": ["src/ui/**", "README.md"],
                     "mcp_root": tmp}
    v = Policy(vdoc, environ={"PAY_KEY": "k" * 10})
    case("paths: visible_paths keeps only matching files", not v.is_hidden("src/ui/button.ts")
         and v.is_hidden("src/app.ts") and v.is_hidden("tests/a.test.ts") and not v.is_hidden("README.md"))
    case("paths: hidden wins over visible", v.is_hidden("src/ui/secret.ts"))
    case("paths: with visible_paths, a read outside it is refused",
         raises(lambda: v.check_path("src/app.ts", "read"), Refusal, "FORGE_PATH_HIDDEN"))
    case("paths: with visible_paths, an empty list is refused",
         raises(lambda: Policy(dict(vdoc, paths={"visible_paths": []}), environ={"PAY_KEY": "k" * 10})))
    shutil.rmtree(tmp)


def test_sync():
    tmp = tempfile.mkdtemp()
    real, stage, exp = (os.path.join(tmp, x) for x in ("real", "stage", "export"))
    files = {"src/app.ts": "a\n", "src/proprietary/core.ts": "secret\n", "tests/a.test.ts": "t\n",
             "README.md": "r\n", "keep.txt": "k\n", "conflict.txt": "base\n", "gone.txt": "g\n"}
    for root in (real, stage):
        for rel, txt in files.items():
            os.makedirs(os.path.dirname(os.path.join(root, rel)), exist_ok=True)
            open(os.path.join(root, rel), "w").write(txt)
    open(os.path.join(real, "conflict.txt"), "w").write("you changed it meanwhile\n")
    os.makedirs(os.path.join(tmp, "outside"))
    os.symlink(os.path.join(tmp, "outside"), os.path.join(real, "linkdir"))
    os.makedirs(os.path.join(exp, "files"))
    news = {}

    def ch(rel, status, txt=None, symlink=False):
        c = {"path": rel, "status": status, "symlink": symlink}
        if txt is not None:
            n = len(news) + 1
            news[n] = txt.encode()
            open(os.path.join(exp, "files", str(n)), "wb").write(news[n])
            c.update(file=f"files/{n}", sha256=__import__("hashlib").sha256(news[n]).hexdigest(), mode="100644")
        return c
    changes = [ch("src/app.ts", "modified", "a\nb\n"), ch("src/new.ts", "added", "n\n"),
               ch("src/proprietary/core.ts", "modified", "agent overwrote it\n"),
               ch(".env", "added", "PAY=evil\n"), ch("tests/a.test.ts", "modified", "weakened\n"),
               ch("forge.yaml", "added", "version: 1\n"), ch("conflict.txt", "modified", "agent\n"),
               ch("gone.txt", "deleted"), ch("node_modules/x.js", "added", "x\n"),
               ch("linkdir/escape.txt", "added", "out\n"), ch("src/sym", "added", "/etc/passwd", symlink=True),
               ch(".mcp.json", "added", "{}\n")]
    doc = base_doc()
    pol = Policy(doc, resolve=False)
    apply, rejected = sync.plan(pol, changes, exp, stage, real, ignored=lambda rel: rel.startswith("node_modules/"))
    got = {c["path"] for c in apply}
    rej = dict(rejected)
    case("sync: allowed changes are proposed (modified, added, deleted)",
         got == {"src/app.ts", "src/new.ts", "gone.txt", ".mcp.json"}, got)
    case("sync: a change to a hidden path is never applied", "src/proprietary/core.ts" in rej and ".env" in rej)
    case("sync: a change to a read-only path is never applied (incl. forge.yaml)",
         "tests/a.test.ts" in rej and "forge.yaml" in rej)
    case("sync: your own change since the start is not overwritten", "conflict.txt" in rej)
    case("sync: ignored files (node_modules) are not applied", "node_modules/x.js" in rej)
    case("sync: a write through a symlinked parent in the real repository is refused", "linkdir/escape.txt" in rej)
    case("sync: symlinks are never applied", "src/sym" in rej)
    text = sync.render(apply)
    case("sync: the review flags .mcp.json as sensitive", "SENSITIVE" in text and "+b" in text)
    before = {rel: open(os.path.join(real, rel)).read() for rel in files if os.path.exists(os.path.join(real, rel))}
    sync.apply_changes(apply, real)
    case("sync: apply writes only the proposed files",
         open(os.path.join(real, "src/app.ts")).read() == "a\nb\n" and not os.path.exists(os.path.join(real, "gone.txt"))
         and open(os.path.join(real, "src/proprietary/core.ts")).read() == before["src/proprietary/core.ts"]
         and open(os.path.join(real, "tests/a.test.ts")).read() == before["tests/a.test.ts"]
         and not os.path.exists(os.path.join(real, ".env"))
         and not os.path.exists(os.path.join(tmp, "outside", "escape.txt")))
    bad = [dict(changes[0], sha256="0" * 64)]
    a2, r2 = sync.plan(pol, bad, exp, stage, real)
    case("sync: content that does not match its hash is refused", not a2 and r2)
    shutil.rmtree(tmp)


def _tree_hash(root):
    """sha256 over every path, type and content under root (including .git), not following links."""
    import hashlib
    h = hashlib.sha256()
    for d, dirs, files in sorted(os.walk(root, followlinks=False)):
        dirs.sort()
        for n in sorted(files + [x for x in dirs if os.path.islink(os.path.join(d, x))]):
            full = os.path.join(d, n)
            h.update(os.path.relpath(full, root).encode("utf-8", "surrogateescape") + b"\0")
            if os.path.islink(full):
                h.update(b"L" + os.readlink(full).encode("utf-8", "surrogateescape"))
            else:
                h.update(oct(os.stat(full).st_mode & 0o777).encode() + open(full, "rb").read())
        h.update(b"\1")
    return h.hexdigest()


def test_sync_aliases():
    """Regression (red team 2026-11-22): case, Unicode-normalization, trailing dot/space and
    case-only-rename aliases of hidden, read-only, .git and forge.yaml paths. On a case-insensitive
    host (macOS APFS) each of these reached the real repository before the fix."""
    import hashlib
    import unicodedata
    nfc = lambda x: unicodedata.normalize("NFC", x)  # noqa: E731
    nfd = lambda x: unicodedata.normalize("NFD", x)  # noqa: E731
    tmp = tempfile.mkdtemp()
    real, stage, exp = (os.path.join(tmp, x) for x in ("real", "stage", "export"))
    files = {"forge.yaml": "version: 1\norig: true\n", "src/app.ts": "a\n",
             "src/proprietary/core.ts": "SECRET MARGIN TABLE v2\n", "tests/ui.test.ts": "t\n",
             nfc("src/café/price.ts"): "hidden nfc\n", nfd("docs/naïve.md"): "read-only nfd\n"}
    for root in (real, stage):
        subprocess.run(["git", "init", "-q", root], check=True)
        for rel, txt in files.items():
            os.makedirs(os.path.dirname(os.path.join(root, rel)) or root, exist_ok=True)
            open(os.path.join(root, rel), "w").write(txt)
    os.makedirs(os.path.join(exp, "files"))
    doc = base_doc()
    doc["paths"] = {"hidden_paths": ["src/proprietary/**", nfc("src/café/**"), ".env*"],
                    "readonly_paths": ["tests/**", nfc("docs/naïve.md")]}
    pol = Policy(doc, resolve=False)
    counter = [0]

    def ch(rel, status="added", txt="ATTACK\n", mode="100644"):
        c = {"path": rel, "status": status, "symlink": False}
        if status != "deleted":
            counter[0] += 1
            data = txt.encode()
            open(os.path.join(exp, "files", str(counter[0])), "wb").write(data)
            c.update(file=f"files/{counter[0]}", sha256=hashlib.sha256(data).hexdigest(), mode=mode, size=len(data))
        return c

    def attempt(name, changes):
        before = _tree_hash(real)
        apply, rejected = sync.plan(pol, changes, exp, stage, real)
        sync.apply_changes(apply, real)
        rej = {r for r, _ in rejected}
        ok = not apply and rej == {c["path"] for c in changes} and _tree_hash(real) == before
        case("sync alias: " + name + " is REJECTED and the real repository is byte-identical", ok,
             f"applied={[c['path'] for c in apply]} rejected={rejected}")

    attempt("TESTS/evil.ts (case variant of read-only tests/**)", [ch("TESTS/evil.ts")])
    attempt(".GIT/hooks/post-checkout (case variant of .git)",
            [ch(".GIT/hooks/post-checkout", txt="#!/bin/sh\necho PWNED\n", mode="100755")])
    attempt("Forge.yaml (case variant of the policy file)", [ch("Forge.yaml", txt="version: 1\npwned: true\n")])
    attempt("SRC/proprietary/core.ts (case variant of a hidden path)", [ch("SRC/proprietary/core.ts")])
    attempt("NFD form of the hidden NFC path src/café/price.ts", [ch(nfd("src/café/price.ts"))])
    attempt("NFC form of the read-only NFD file docs/naïve.md", [ch(nfc("docs/naïve.md"))])
    attempt("NFD form of the read-only pattern docs/naïve.md (pattern NFC)", [ch(nfd("docs/naïve.md"))])
    attempt("trailing dot and space variants (tests./x, forge.yaml., forge.yaml , .git./hooks/x)",
            [ch("tests./x.ts"), ch("forge.yaml."), ch("forge.yaml "), ch(".git./hooks/post-commit")])
    attempt("ignorable code point in .git (.g\\u200cit/hooks/x)", [ch(".g\u200cit/hooks/post-merge")])
    attempt("case-only rename of an allowed file (src/app.ts -> src/App.ts)",
            [ch("src/app.ts", "deleted"), ch("src/App.ts", txt="a\n")])
    attempt("case variant of an allowed existing file (SRC/App.TS)", [ch("SRC/App.TS")])
    attempt("two new names that fold to one (new.md and NEW.md)", [ch("new.md"), ch("NEW.md")])
    box = ["TESTS/evil.ts", ".GIT/hooks/post-checkout", "Forge.yaml", "SRC/proprietary/core.ts",
           nfd("src/café/price.ts"), nfd("docs/naïve.md"), "tests./x.ts", "forge.yaml ", ".g\u200cit/config"]
    case("sync alias: refusal() alone (the export in the box, no host file system) refuses every alias",
         all(sync.refusal(pol, x) for x in box) and sync.refusal(pol, "src/app.ts") is None,
         [x for x in box if not sync.refusal(pol, x)])
    case("sync alias: case variants of .git and forge.yaml are flagged SENSITIVE",
         all(sync.sensitive(x) for x in (".GIT/hooks/x", ".git/config", "Forge.yaml", "sub/FORGE.YAML", "forge.yaml")))
    # negative control: a normal allowed edit still applies
    apply, rejected = sync.plan(pol, [ch("src/app.ts", "modified", "a\nb\n")], exp, stage, real)
    sync.apply_changes(apply, real)
    case("sync alias: negative control, a normal allowed edit (src/app.ts) still applies",
         not rejected and open(os.path.join(real, "src/app.ts")).read() == "a\nb\n", rejected)
    # apply-time re-check (TOCTOU): a parent swapped to a symlink after plan() is not written through
    outside = os.path.join(tmp, "outside")
    os.makedirs(outside)
    os.makedirs(os.path.join(real, "lib"))
    os.makedirs(os.path.join(stage, "lib"))
    apply, rejected = sync.plan(pol, [ch("lib/x.ts", txt="x\n")], exp, stage, real)
    os.rmdir(os.path.join(real, "lib"))
    os.symlink(outside, os.path.join(real, "lib"))
    done = sync.apply_changes(apply, real)
    case("sync alias: apply re-checks the target; a parent swapped to a symlink after the plan is not written",
         len(apply) == 1 and not done and not os.listdir(outside), (apply, done, os.listdir(outside)))
    shutil.rmtree(tmp)


def make_log(n, cap=10 ** 6):
    exp = int(time.time()) + 86400
    man = R.manifest_bytes(R.sha(b"t"), cap, exp)
    recs = [R.genesis(cap, exp, man)]
    for i in range(1, n):
        recs.append(R.step(os.urandom(16), 1, i, int(time.time()), recs[-1], i))
    return man, recs


def test_verifier():
    tmp = tempfile.mkdtemp()
    man, recs = make_log(40, cap=30)
    open(os.path.join(tmp, "m"), "wb").write(man)
    rng = random.Random(7)
    logs = [b"".join(recs[:k]) for k in (1, 2, 10, 30, 31, 40)]  # 31+ records: over the cap of 30
    good = b"".join(recs[:20])
    for _ in range(14):  # one flipped byte anywhere
        b = bytearray(good)
        i = rng.randrange(len(b))
        b[i] ^= 1 << rng.randrange(8)
        logs.append(bytes(b))
    logs.append(good + recs[5])                       # replayed record
    logs.append(good[:-5])                            # torn tail
    logs.append(good[:128 * 3] + good[128 * 4:])      # gap in seq
    dup = R.step(recs[3][48:64], 1, 20, int(time.time()), recs[19], 20)
    logs.append(good + dup)                           # duplicate step id
    now = int(time.time())
    pv_memo, same, verdicts = R.PersistentVerifier(), True, set()
    os.environ["FORGE_VERIFIER_MEMO"] = "0"
    pv_plain = R.PersistentVerifier()
    os.environ["FORGE_VERIFIER_MEMO"] = "1"
    for round_ in range(2):  # the 2nd round hits the memo table for every record seen before
        for i, lg in enumerate(logs):
            path = os.path.join(tmp, f"l{i}")
            open(path, "wb").write(lg)
            a = R.verify_file_fresh(path, os.path.join(tmp, "m"), now)
            b = pv_memo.verify(path, os.path.join(tmp, "m"), now)
            c = pv_plain.verify(path, os.path.join(tmp, "m"), now)
            verdicts.add(a["verdict"])
            if not (a == b == c):
                same = False
                print("      differs:", i, a.get("verdict"), b.get("verdict"), c.get("verdict"))
    case(f"verifier: persistent+memo, persistent and fresh subprocess give identical JSON on {len(logs)} logs x 2 "
         f"(verdicts seen: {', '.join(sorted(verdicts))})", same and "ACCEPT" in verdicts and len(verdicts) >= 4)
    case("verifier: the memo table was used (hits > 0)", pv_memo.last_memo.get("hits", 0) > 0, pv_memo.last_memo)
    pv_memo.proc.kill()
    pv_memo.proc.wait()
    r = pv_memo.verify(os.path.join(tmp, "l0"), os.path.join(tmp, "m"), now)
    case("verifier: a killed verifier process is restarted (next call still verified)", r["verdict"] == "ACCEPT", r)
    pv_memo.timeout = 0.000001
    r = pv_memo.verify(os.path.join(tmp, "l3"), os.path.join(tmp, "m"), now)
    case("verifier: no answer in time gives UNVERIFIABLE_INTERNAL (fail closed)",
         r["verdict"] == "UNVERIFIABLE_INTERNAL", r)
    pv_memo.close()
    pv_plain.close()
    # gate decisions through the persistent verifier
    d = os.path.join(tmp, "data")
    os.makedirs(d)
    log = gatelog.GateLog(d, "deploy", 3, 3600, None)
    vs = []
    for _ in range(4):
        v, seq, _g = log.reserve(1, os.urandom(16), {"tool": "deploy"})
        vs.append(v)
        if seq:
            log.finish(seq, True)
    case("gate: count cap 3 -> 3 ACCEPT, the 4th REJECT_OVER_CAP", vs == ["ACCEPT"] * 3 + ["REJECT_OVER_CAP"], vs)
    v = gatelog.GateLog(d, "pay", 1000, 3600, None)
    sid = os.urandom(16)
    a1 = v.reserve(600, sid, {})[0]
    a2 = v.reserve(100, sid, {})[0]
    a3 = v.reserve(401, os.urandom(16), {})[0]
    a4 = v.reserve(400, os.urandom(16), {})[0]
    case("gate: value cap 1000: 600 ok, replayed id REJECT_DUPLICATE_ID, 401 over, 400 ok",
         [a1, a2, a3, a4] == ["ACCEPT", "REJECT_DUPLICATE_ID", "REJECT_OVER_CAP", "ACCEPT"], [a1, a2, a3, a4])
    again = gatelog.GateLog(d, "deploy", 3, 3600, None)
    case("gate: after a reload the cap is still reached", again.reserve(1, os.urandom(16), {})[0] == "REJECT_OVER_CAP")
    case("gate: a reload with a different cap is refused",
         raises(lambda: gatelog.GateLog(d, "deploy", 4, 3600, None), gatelog.GateError, "stored cap"))
    with open(os.path.join(d, "deploy.bin"), "r+b") as f:
        f.seek(128 * 2 + 72)
        b = f.read(1)
        f.seek(128 * 2 + 72)
        f.write(bytes([b[0] ^ 1]))
    case("gate: one flipped log byte -> the log is refused at load",
         raises(lambda: gatelog.GateLog(d, "deploy", 3, 3600, None), gatelog.GateError, "does not verify"))
    shutil.rmtree(tmp)


def test_draft():
    tmp = tempfile.mkdtemp()
    repo = os.path.join(tmp, "r")
    for rel in ["src/app.ts", "src/proprietary/core.ts", "tests/a.test.ts", "infra/main.tf", ".env",
                "spec/api.md", "INVARIANTS.md", ".forge/forge.json", "node_modules/x.js"]:
        os.makedirs(os.path.dirname(os.path.join(repo, rel)) or repo, exist_ok=True)
        open(os.path.join(repo, rel), "w").write("x\n")
    open(os.path.join(repo, ".gitignore"), "w").write("node_modules/\n")
    secret = "FAKE_ghp_literal_value_DO_NOT_COPY_123456"
    json.dump({"mcpServers": {
        "github": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"],
                   "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": secret}},
        "stripe": {"type": "http", "url": "https://mcp.example.invalid/mcp",
                   "headers": {"Authorization": "Bearer ${STRIPE_KEY}"}}}}, open(os.path.join(repo, ".mcp.json"), "w"))
    subprocess.run(["git", "init", "-q", repo], check=True)
    tools = {"github": [{"name": "get_issue"}, {"name": "list_commits"}, {"name": "delete_repository"},
                        {"name": "create_pull_request"}, {"name": "frobnicate"}],
             "stripe": [{"name": "create_refund", "inputSchema": {"properties": {"amount": {"type": "integer"}}}},
                        {"name": "retrieve_balance"}]}
    doc, notes, questions = D.draft(repo, tools)
    text = D.HEADER + D.to_yaml(doc)
    case("draft: no secret value from .mcp.json reaches forge.yaml", secret not in text and secret not in json.dumps(
        [notes, questions]))
    case("draft: secrets are named ${NAME} only", "${GITHUB_PERSONAL_ACCESS_TOKEN}" in text and "${STRIPE_KEY}" in text)
    case("draft: hidden paths proposed (.env*, infra/**, proprietary)",
         {".env*", "infra/**", "src/proprietary/**"} <= set(doc["paths"]["hidden_paths"]), doc["paths"])
    case("draft: read-only paths proposed (tests, spec, .forge, INVARIANTS.md)",
         {"tests/**", "spec/**", ".forge/**", "INVARIANTS.md"} <= set(doc["paths"]["readonly_paths"]), doc["paths"])
    case("draft: ignored files are not scanned (node_modules)", not any("node_modules" in json.dumps(q)
                                                                       for q in questions))
    allow = {t["tool"] for t in doc["tools"]["allow"]}
    gate = {t["tool"]: t for t in doc["tools"]["gate"]}
    case("draft: read-like tools allowed", {"get_issue", "list_commits", "retrieve_balance"} <= allow, allow)
    case("draft: destructive tools denied", "delete_repository" in doc["tools"]["deny"])
    case("draft: money tool gated by value with a cap", gate.get("create_refund", {}).get("unit") == "value"
         and gate["create_refund"]["value_field"] == "amount" and gate["create_refund"]["cap"] > 0)
    case("draft: side-effect tool gated by count", gate.get("create_pull_request", {}).get("unit") == "count")
    case("draft: unknown tool left out (default deny) and asked about",
         "frobnicate" not in allow | set(gate) | set(doc["tools"]["deny"])
         and any(q.get("tool") == "frobnicate" and q["proposal"] == "ask" for q in questions))
    pol = Policy(doc, resolve=False)
    case("draft: the draft is a valid policy", set(pol.env_names) == {"GITHUB_PERSONAL_ACCESS_TOKEN", "STRIPE_KEY", "ANTHROPIC_API_KEY"})
    case("draft: claude gets an llm section (key by NAME, cost caps) and no egress for the model",
         doc["llm"]["key_env"] == "ANTHROPIC_API_KEY" and pol.llm["unit"] == "cost" and not pol.egress_allow)
    r = subprocess.run([sys.executable, os.path.join(PKG, "forge_airlock.py"), "init", "--repo", repo, "--json"],
                       capture_output=True, text=True)
    case("draft: `forge init --json` runs without Docker and leaks no value",
         r.returncode == 0 and secret not in r.stdout and "forge_yaml" in json.loads(r.stdout), r.stderr[-300:])
    shutil.rmtree(tmp)


def test_static():
    cmd = open(os.path.join(PKG, "..", "commands", "airlock.md")).read()
    case("static: commands/airlock.md has description front matter", cmd.startswith("---\ndescription:"))
    case("static: the command uses AskUserQuestion and never asks for secret values",
         "AskUserQuestion" in cmd and "never" in cmd.lower())
    man = json.load(open(os.path.join(PKG, "..", ".claude-plugin", "plugin.json")))
    case("static: one plugin: plugin.json names the plugin forge", man.get("name") == "forge")
    case("static: commands/airlock.md calls airlock/forge_airlock.py in the plugin",
         "${CLAUDE_PLUGIN_ROOT}/airlock/forge_airlock.py" in cmd)
    bad = []
    for d, dirs, fs in os.walk(PKG):
        dirs[:] = [x for x in dirs if x not in ("__pycache__", "results")]
        for f in fs:
            try:
                t = open(os.path.join(d, f), encoding="utf-8").read()
            except (UnicodeDecodeError, OSError):
                continue
            if "/" + "Users/" in t:
                bad.append(os.path.relpath(os.path.join(d, f), PKG))
    case("static: no absolute host home path in the package", not bad, bad)


def test_redteam():
    """Regression tests of the protocol red-team findings F1 to F8 (tests/redteam_regress.py)."""
    sys.path.insert(0, HERE)
    import redteam_regress
    redteam_regress.run(lambda name, ok, detail="": case("redteam: " + name, bool(ok), detail))


def test_llm():
    import llm_proxy as L
    env = {"PAY_KEY": "FAKE_pay_key_not_real_1234", "MODEL_KEY": "FAKE_model_key_not_real_5678"}
    llm = {"key_env": "MODEL_KEY", "session_cap": 1000, "allowed_models": ["m1"]}
    p = Policy(base_doc(llm=llm), environ=env)
    case("llm: defaults (provider api.anthropic.com:443, x-api-key, tokens, per_request_max = session_cap)",
         (p.llm["host"], p.llm["port"], p.llm["auth_header"], p.llm["unit"], p.llm["per_request_max"])
         == ("api.anthropic.com", 443, "x-api-key", "tokens", 1000))
    case("llm: the key is a secret of the airlock (canary) and its NAME is passed to the airlock only",
         "FAKE_model_key_not_real_5678" in p.secrets() and "MODEL_KEY" in p.env_names
         and Policy(base_doc(llm=llm), resolve=False).llm_key is None)
    case("llm: key_env unset on the airlock side -> fail closed",
         raises(lambda: Policy(base_doc(llm=llm), environ={"PAY_KEY": "FAKE_pay_key_not_real_1234"}), contains="MODEL_KEY"))
    for bad, why in ((dict(llm, key_env="sk-ant-value"), "NAME"), (dict(llm, provider_url="https://x/v1"), "no path"),
                     (dict(llm, unit="cost"), "no price"), (dict(llm, allowed_models=[]), "allowed_models"),
                     (dict(llm, extra=1), "unknown keys"), (dict(llm, free_paths=["/v1/messages"]), "always gated")):
        case(f"llm: invalid section refused ({why})", raises(lambda: Policy(base_doc(llm=bad), environ=env), contains=why))
    d = tempfile.mkdtemp()
    try:
        cfg = dict(p.llm, session_cap=100)
        g = L.LLMGate(cfg, d, "unit")
        a, b = os.urandom(16), os.urandom(16)
        la, _ = g.reserve(a, 60, {})
        lb, why = g.reserve(b, 60, {})
        case("llm gate: a reservation counts while in flight (60 + 60 > 100 refused by the verifier)",
             la and lb is None and why["verdict"] == "REJECT_OVER_CAP")
        g.settle(la, a, 10, "usage")
        lb, _ = g.reserve(b, 60, {})
        case("llm gate: settlement to reported usage frees the rest (10 settled, then 60 fits)",
             lb is not None and g.session.gross() == 10)
        g2 = L.LLMGate(cfg, d, "unit")  # restart while b is PENDING
        case("llm gate: PENDING at restart -> full reservation spent (ABORTED)",
             g2.session.gross() == 70 and g2.status()["session"]["reservations"].get("ABORTED") == 1)
        c = os.urandom(16)
        lc, _ = g2.reserve(c, 30, {})
        case("llm gate: usage above the reservation and the cap -> log clamped at the cap, OVERRUN",
             g2.settle(lc, c, 45, "usage") == ["OVERRUN"] and g2.session.gross() == 100
             and g2.status()["session"]["proved_verdict"] == "ACCEPT")
        cc = dict(cfg, unit="cost", prices={"m1": {"input_per_mtok": 4, "output_per_mtok": 20}})
        gc = L.LLMGate(cc, d, "cost")
        est, _ = gc.estimate({"model": "m1", "max_tokens": 10}, 100)
        act = gc.actual("m1", {"input_tokens": 100, "output_tokens": 10, "cache_read_input_tokens": 1000,
                               "cache_creation_input_tokens": 50,
                               "cache_creation": {"ephemeral_5m_input_tokens": 50, "ephemeral_1h_input_tokens": 0}})
        case(f"llm cost units: estimate {est} = (100+256)*4*2 + 10*20; actual {act} = 400+250+400+200",
             est == 3048 and act == 1250)
        case("llm: usage without input_tokens/output_tokens is unknown (None)", gc.actual("m1", {"output_tokens": 3}) is None)
        sse = L.SSEUsage()
        for part in (b'event: message_start\ndata: {"type":"message_start","message":{"usage":{"input_tokens":5,',
                     b'"output_tokens":1}}}\n\nevent: message_delta\ndata: {"type":"message_delta","usage":{"output_tokens":9}}',
                     b'\n\nevent: message_stop\ndata: {"type":"message_stop"}\n\n'):
            sse.feed(part)
        case("llm SSE reader: usage across chunk borders, complete after message_stop",
             sse.complete() and sse.usage["input_tokens"] == 5 and sse.usage["output_tokens"] == 9)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_human_approval():
    """require_human_approval: hardware (tests/human_approval_test.py; software keys made at test time)."""
    sys.path.insert(0, HERE)
    import human_approval_test
    human_approval_test.run(lambda name, ok, detail="": case(name, bool(ok), detail))


def test_isolation():
    """Isolation backends (airlock/backends): choice, fail-closed availability, compose output, probe."""
    import backends as B
    import forge_airlock as FA
    case("isolation: default is docker", B.resolve(None, None) == "docker")
    case("isolation: --isolation alone is used", B.resolve("gvisor", None) == "gvisor")
    case("isolation: forge.yaml alone is used", B.resolve(None, "microvm") == "microvm")
    case("isolation: --isolation that differs from forge.yaml is refused",
         raises(lambda: B.resolve("docker", "gvisor"), B.BackendError, "differs"))
    case("isolation: an unknown backend is refused", raises(lambda: B.resolve("lxc", None), B.BackendError, "unknown"))
    runc = {"runc", "io.containerd.runc.v2"}
    case("isolation: docker needs no extra runtime", B.runtime_for("docker", runc) is None)
    case("isolation: negative control: gvisor on a host without runsc is refused (no fall back)",
         raises(lambda: B.runtime_for("gvisor", runc), B.BackendError, "not available"))
    case("isolation: negative control: microvm on a host without a kata runtime is refused",
         raises(lambda: B.runtime_for("microvm", runc), B.BackendError, "not available"))
    case("isolation: negative control: docker info failure is refused for gvisor",
         raises(lambda: B.runtime_for("gvisor", None), B.BackendError, "fail closed"))
    case("isolation: gvisor with runsc registered -> runtime runsc", B.runtime_for("gvisor", runc | {"runsc"}) == "runsc")
    case("isolation: microvm with kata registered -> runtime kata", B.runtime_for("microvm", runc | {"kata"}) == "kata")
    pol = Policy(base_doc(isolation="gvisor"), environ={"PAY_KEY": "FAKE_pay_key_not_real_1234"})
    case("isolation: forge.yaml isolation: gvisor loads", pol.isolation == "gvisor")
    case("isolation: forge.yaml isolation: an unknown value is refused",
         raises(lambda: Policy(base_doc(isolation="vm"), environ={"PAY_KEY": "x"}), PolicyError, "isolation"))
    pol = Policy(base_doc(), environ={"PAY_KEY": "x"})
    for rt in (None, "runsc", "kata"):
        doc = FA.compose_doc(pol, "/p/forge.yaml", "/s", "/r", "logs", "img", [], [], agent_runtime=rt)
        sv = doc["services"]
        case(f"isolation: compose with agent runtime {rt}: only the agent changes",
             sv["agent"].get("runtime") == rt and all("runtime" not in v for k, v in sv.items() if k != "agent"))
    case("isolation: docker has no probe", B.probe_sh("docker") == "")
    for name in ("gvisor", "microvm"):
        r = subprocess.run(["sh", "-c", B.probe_sh(name) + "echo RAN"], capture_output=True, text=True,
                           env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")})
        case(f"isolation: negative control: {name} probe with no host kernel string -> exit {B.PROBE_EXIT}, command not run",
             r.returncode == B.PROBE_EXIT and "RAN" not in r.stdout, (r.returncode, r.stdout, r.stderr))


def test_vfkit():
    """macOS microvm (vfkit): the compose -> VM translation, preflight refusals, the image build is deterministic.
    Host-independent (no VM is started here)."""
    import tempfile
    sys.path.insert(0, os.path.join(PKG, "backends"))
    import vfkit_compose as VC
    import vfkit_image as VI
    import vfkit_vm as VM
    work = tempfile.mkdtemp()
    bind = os.path.join(work, "src")
    os.makedirs(bind)
    open(os.path.join(bind, "f"), "w").write("x")
    svc = {"user": "1000:1000", "working_dir": "/workspace", "environment": ["A=1", "PASSED", "UNSET"],
           "volumes": ["ws:/workspace", f"{bind}:/forge-run:ro",
                       {"type": "volume", "source": "ws", "target": "/workspace/tests", "read_only": True,
                        "volume": {"subpath": "tests"}}],
           "networks": ["agentnet"], "command": ["true"], "read_only": True, "cap_drop": ["ALL"]}
    fw = {"agentnet": [(8700, ("127.0.0.1", 1234), ["airlock"])]}
    j = VC.job(svc, {"ws": "/H/ws"}, fw, work, environ={"PASSED": "p"})
    case("vfkit: service env -> VM env (NAME from this process; an unset NAME is left out)",
         j["env"] == {"A": "1", "PASSED": "p"}, j["env"])
    case("vfkit: the ws volume is shared rw, its subpath read-only, a bind is a read-only COPY",
         [(h, g, ro) for _t, h, g, ro in j["shares"]][:1] == [("/H/ws", "/workspace", False)]
         and ("/H/ws/tests", "/workspace/tests", True) in [(h, g, ro) for _t, h, g, ro in j["shares"]]
         and any(g == "/forge-run" and ro and h.startswith(work) for _t, h, g, ro in j["shares"]), j["shares"])
    case("vfkit: network agentnet -> only its vsock forwards", j["forwards"] == fw["agentnet"], j["forwards"])
    for k, v in (("privileged", True), ("cap_add", ["SYS_ADMIN"]), ("network_mode", "host"), ("devices", ["/dev/kvm"])):
        case(f"vfkit: negative control: service option {k} is refused (it would widen the box)",
             raises(lambda: VC.job(dict(svc, **{k: v}), {"ws": "/H"}, fw, work), VC.Refused, k))
    case("vfkit: negative control: a network without a vsock mapping is refused",
         raises(lambda: VC.job(dict(svc, networks=["agentnet", "upstream"]), {"ws": "/H"}, fw, work), VC.Refused, "upstream"))
    case("vfkit: negative control: a named volume without a host folder is refused",
         raises(lambda: VC.job(dict(svc, volumes=["logs:/data"]), {"ws": "/H"}, fw, work), VC.Refused, "logs"))
    old = dict(os.environ)
    try:
        os.environ["FORGE_VFKIT"] = os.path.join(work, "no-such-vfkit")
        case("vfkit: negative control: vfkit missing -> Unavailable (exit 2 in up)",
             raises(VM.preflight, VM.Unavailable, "not installed" if sys.platform == "darwin" else "macOS"))
        os.environ.pop("FORGE_VFKIT")
        os.environ["FORGE_VFKIT_TEST_NO_HV"] = "1"
        case("vfkit: negative control: no hardware virtualization -> Unavailable",
             raises(VM.preflight, VM.Unavailable, "virtualization" if sys.platform == "darwin" else "macOS"))
    finally:
        os.environ.clear()
        os.environ.update(old)
    t = VI.Tree()
    t.file("b", b"2")
    t.file("a/x", b"1", 0o755)
    t.link("l", "/a/x")
    t2 = VI.Tree()
    t2.link("l", "/a/x")
    t2.file("a/x", b"1", 0o755)
    t2.file("b", b"2")
    case("vfkit: initrd cpio is deterministic (insertion order, time and owner do not change the bytes)",
         t.cpio() == t2.cpio() and t.cpio().startswith(b"070701"))
    lock = json.load(open(VI.LOCK))
    case("vfkit: every pinned package and the kernel have a SHA-256",
         all(len(p["sha256"]) == 64 for p in lock["packages"] + [lock["kernel"]]) and len(lock["packages"]) > 10)
    shutil.rmtree(work, ignore_errors=True)


def test_names():
    """Parallel boxes: the image tag follows the build context's content (two checkouts never share a tag)."""
    import forge_airlock as FA
    d = tempfile.mkdtemp()
    try:
        os.makedirs(os.path.join(d, "engine"))
        open(os.path.join(d, "engine", "g.py"), "w").write("x = 1\n")
        a = FA.source_tag(d)
        os.makedirs(os.path.join(d, "engine", "__pycache__"))
        open(os.path.join(d, "engine", "__pycache__", "g.pyc"), "wb").write(b"junk")
        os.makedirs(os.path.join(d, "tests"))
        open(os.path.join(d, "tests", "t.py"), "w").write("y\n")
        case("names: source tag ignores __pycache__ and tests (not in the image)", FA.source_tag(d) == a)
        open(os.path.join(d, "engine", "g.py"), "w").write("x = 2\n")
        case("names: negative control: changed engine code gives another image tag", FA.source_tag(d) != a)
        case("names: default image tags carry the source tag, not :local",
             FA.IMAGE.endswith(":" + FA._TAG) and FA.CLAUDE_IMAGE.endswith(":" + FA._TAG) and ":local" not in FA.IMAGE
             if "FORGE_AIRLOCK_IMAGE" not in os.environ else True)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def main():
    for t in (test_policy, test_paths, test_sync, test_sync_aliases, test_verifier, test_draft, test_llm, test_static, test_redteam, test_human_approval, test_isolation, test_vfkit, test_names):
        try:
            t()
        except Exception as e:  # noqa: BLE001
            case(f"{t.__name__} crashed: {type(e).__name__}: {e}", False)
    failed = RESULTS.count(False)
    print(f"{len(RESULTS)} cases, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
