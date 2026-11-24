#!/usr/bin/env python3
"""Docker end-to-end test of sync-back aliases (red team M2.3; needs Docker with Compose v2).

The boxed command (case-sensitive Linux ext4) writes names that a case-insensitive or
Unicode-normalizing host (macOS APFS) sees as hidden, read-only or protected files, plus
special files, then `forge up` exports the changes and the harness answers y at the prompt:
  SRC/proprietary/core.ts, src/Proprietary/core.ts   case aliases of a hidden file
  notes/cafe<U+0301>.md                              NFD alias of the hidden NFC notes/caf<U+00E9>.md
  TESTS/ui.test.ts, Spec/api.md                      case aliases of read-only files
  Forge.yaml, .GIT/hooks/post-checkout               protected-path and .git aliases
  src/app.ts. and 'src/app.ts '                      trailing dot / trailing space
  src/ui.ts -> src/UI.ts                             case-only rename of a public file
  src/pipe.ts (FIFO), src/sock.ts (unix socket)      special files (the export must not block)
  src/ok_by_box.ts                                   the one allowed change
Pass = forge up finishes, only src/ok_by_box.ts changed in the real repository (every other byte
identical, no new name), and every alias is listed as not applied.
Negative control: the same alias writes done directly on a copy of the repository (a sync with
no alias check); the same check MUST report the breach. On a case-sensitive host the control
reports that the file system does not alias (and the test still requires the refusals).
Label: FINITE TEST (this machine and file system, these names)."""
import hashlib
import os
import shutil
import sys
import tempfile
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness as H  # noqa: E402

RESULTS = []
NFD = unicodedata.normalize("NFD", H.NOTE_NFC)
ALIASES = ["SRC/proprietary/core.ts", "src/Proprietary/core.ts", NFD, "TESTS/ui.test.ts", "Spec/api.md",
           "Forge.yaml", ".GIT/hooks/post-checkout", "src/app.ts.", "src/app.ts ", "src/UI.ts"]
SCRIPT = r"""
set -u
w() { mkdir -p "$(dirname "$1")" && printf 'ALIAS-WRITE-FROM-THE-BOX\n' > "$1"; }
w 'SRC/proprietary/core.ts'; w 'src/Proprietary/core.ts'; w "$NFD"
w 'TESTS/ui.test.ts'; w 'Spec/api.md'; w 'Forge.yaml'; w '.GIT/hooks/post-checkout'
w 'src/app.ts.'; w 'src/app.ts '
mv src/ui.ts src/UI.ts
mkfifo src/pipe.ts
python3 -c "import socket; s=socket.socket(socket.AF_UNIX); s.bind('src/sock.ts')"
printf 'export const okByBox = 1\n' > src/ok_by_box.ts
ls -la src | head -30
echo BOX-ALIAS-DONE
"""


def case(name, ok, detail=""):
    RESULTS.append(bool(ok))
    print(("ok    " if ok else "FAIL  ") + name + ("" if ok else f"   {detail}"), flush=True)


def tree(root):
    h = {}
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if x != ".git"]
        for f in files:
            p = os.path.join(d, f)
            rel = os.path.relpath(p, root)
            h[rel] = ("link:" + os.readlink(p)) if os.path.islink(p) else hashlib.sha256(open(p, "rb").read()).hexdigest()
    return h


def judge(before, after):
    """(ok, problems): only src/ok_by_box.ts may differ."""
    changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
    problems = [k for k in changed if k != "src/ok_by_box.ts"]
    return not problems and "src/ok_by_box.ts" in after, problems


def main():
    tmp = tempfile.mkdtemp(prefix="forge-alias-e2e-")
    repo = H.make_repo(tmp)
    try:
        before = tree(repo)
        code, out = H.forge_up(repo, ["sh", "-c", SCRIPT], "alias" + os.urandom(8).hex(), answer="y",
                               pass_env={"NFD": NFD})
        open(os.path.join(tmp, "alias-e2e.log"), "wb").write(out)
        text = unicodedata.normalize("NFC", out.decode("utf-8", "replace"))
        case("alias e2e: forge up finished (the export did not block on a FIFO or a socket)",
             code != 124 and "BOX-ALIAS-DONE" in text and "file(s) can be applied" in text, f"exit {code}: {text[-1500:]}")
        after = tree(repo)
        ok, problems = judge(before, after)
        case("alias e2e: answer y -> only src/ok_by_box.ts changed; hidden, read-only, protected files and names "
             "byte-identical", ok, problems)
        for a in ALIASES + ["src/pipe.ts", "src/sock.ts"]:
            na = unicodedata.normalize("NFC", a)
            case(f"alias e2e: {a!r} listed as not applied", f"not applied: {na}:" in text,
                 [ln for ln in text.splitlines() if "not applied" in ln][:20])
        hidden_note = os.path.join(repo, H.NOTE_NFC)
        case("alias e2e: the hidden NFC note still has its own content",
             open(hidden_note, encoding="utf-8").read() == H.NOTE)
        # negative control: the same writes without any alias check, on a copy
        ctl = os.path.join(tmp, "control")
        shutil.copytree(repo, ctl, symlinks=True)
        cb = tree(ctl)
        for a in ALIASES[:-1]:
            p = os.path.join(ctl, a)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w") as f:
                f.write("ALIAS-WRITE-FROM-THE-BOX\n")
        os.rename(os.path.join(ctl, "src/ui.ts"), os.path.join(ctl, "src/UI.ts"))
        open(os.path.join(ctl, "src/ok_by_box.ts"), "w").write("export const okByBox = 1\n")
        cok, cprob = judge(cb, tree(ctl))
        insensitive = os.path.exists(os.path.join(ctl, "SRC", "PROPRIETARY", "CORE.TS"))
        case(f"alias e2e negative control: a sync with no alias check is DETECTED ({len(cprob)} protected files "
             f"changed: {', '.join(cprob[:4])}; case-insensitive host: {insensitive})",
             (not cok) if insensitive else True, cprob)
        case("alias e2e: no container, network or run volume left behind", H.leftovers(repo) == 0, H.leftovers(repo))
    finally:
        H.cleanup(repo, logs=True)
        print(f"logs: {tmp}")
    failed = RESULTS.count(False)
    print(f"alias e2e: {len(RESULTS)} cases, {failed} failed")
    if not failed and not os.environ.get("FORGE_KEEP_LOGS"):
        shutil.rmtree(tmp, ignore_errors=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
