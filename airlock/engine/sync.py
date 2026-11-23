# sync.py - the diff sync-back filter: which of the box's changes may reach the real repository.
# Standard library only (used on the host by forge_airlock.py and in the box by export_changes.py).
#
# A change is applied only if ALL hold (else it is listed as refused or skipped, never applied):
#   - the path is not hidden (hidden_paths, or outside visible_paths), not read-only
#     (readonly_paths, forge.yaml), not inside .git, and not a symlink;
#   - the path is not ignored by the real repository's .gitignore (build output, node_modules);
#   - the real file still has the content the box started from (no change of yours is overwritten);
#   - the real target and every parent directory lie inside the repository after realpath()
#     (a symlink in the real repository cannot redirect a write);
#   - ALIASES: the box is case-sensitive Linux, the host may be case-insensitive and Unicode-
#     normalizing (macOS APFS). Every check above also runs on the FOLDED path (per segment: NFD,
#     default-ignorable code points removed, casefold, NFC, trailing dots/spaces removed), so
#     'Forge.yaml', '.GIT/hooks/x', 'TESTS/x' and 'SRC/proprietary/x' are refused like their
#     canonical forms. A name that ends in a dot or space or holds an ignorable code point is
#     refused. On the host, every existing path component must have exactly the spelling that is
#     on disk (no case or normalization alias of an existing file, also not a case-only rename),
#     no component may be a symlink, and the realpath form passes the same checks. Two changes
#     whose folded paths are equal are both refused. apply_changes() checks all of it again right
#     before each write (TOCTOU) and never writes through a symlink or into any form of .git.
# Then the person must type y at the terminal. Label: NATIVE CHECK (hand-written, not proved).
import difflib
import hashlib
import os
import re
import unicodedata

# Paths that change what tools on YOUR machine run or trust: flagged in the review, not refused.
# Compared on the FOLDED path, so every case and normalization variant is flagged too.
SENSITIVE = (".git/", "forge.yaml", ".mcp.json", ".claude/", ".github/", ".vscode/", ".envrc", ".gitattributes",
             ".gitmodules", ".pre-commit-config.yaml", "package.json", "makefile")

# Default-ignorable code points (HFS+ ignores some of them in names; git's core.protectHFS list).
_IGNORABLE = re.compile("[\u00ad\u034f\u061c\u115f\u1160\u17b4\u17b5\u180b-\u180f\u200b-\u200f"
                        "\u202a-\u202e\u2060-\u206f\u3164\ufe00-\ufe0f\ufeff\uffa0]")


def fold_seg(seg):
    """One path segment as a case-insensitive, normalization-insensitive file system sees it."""
    s = unicodedata.normalize("NFD", seg)
    s = _IGNORABLE.sub("", s)
    s = unicodedata.normalize("NFC", s.casefold())
    return s.rstrip(". ") or s


def fold(rel):
    return "/".join(fold_seg(x) for x in rel.split("/"))


def _folded_sets(pol):
    """hidden, visible and read-only patterns, folded the same way (cached on the policy)."""
    got = getattr(pol, "_forge_folded_sets", None)
    if got is None:
        mk = type(pol.hidden)
        vis = mk([fold(p) for p in pol.visible.patterns], "visible_paths") if pol.visible is not None else None
        ro = list(pol.readonly.patterns) + list(pol.protected.patterns)
        got = (mk([fold(p) for p in pol.hidden.patterns], "hidden_paths"), vis,
               mk([fold(p) for p in ro], "readonly_paths"))
        pol._forge_folded_sets = got
    return got


def refusal(pol, rel, symlink=False):
    """Why this workspace path may never be synced back (None = it may, subject to the rest).
    Checks the logical path AND its folded form (case, Unicode normalization, ignorable code
    points, trailing dots and spaces): no alias of a protected path passes."""
    rel = rel.strip("/")
    if not rel or rel.startswith("../") or "/../" in "/" + rel + "/" or rel.startswith("/"):
        return "path outside the workspace"
    segs = rel.split("/")
    if any(x in ("", ".", "..") for x in segs):
        return "path outside the workspace"
    frel = fold(rel)
    fsegs = frel.split("/")
    if rel == ".git" or rel.startswith(".git/") or ".git" in fsegs:
        return "inside .git (any case or normalization of .git)"
    if any(x != x.rstrip(". ") for x in segs):
        return "a name ends in a dot or a space (an alias on some file systems)"
    if any(_IGNORABLE.search(unicodedata.normalize("NFD", x)) for x in segs):
        return "a name holds an ignorable code point (an alias on some file systems)"
    fhid, fvis, fro = _folded_sets(pol)
    if pol.hidden.match(rel) or pol.is_hidden(rel) or fhid.match(frel) or (fvis is not None and not fvis.match(frel)):
        return "hidden path (hidden_paths or outside visible_paths; any case or normalization)"
    if pol.readonly.match(rel) or pol.protected.match(rel) or fro.match(frel):
        return "read-only path (readonly_paths or forge.yaml; any case or normalization)"
    if symlink:
        return "symlink (symlinks are never applied)"
    return None


def sensitive(rel):
    f = fold(rel.strip("/"))
    segs = f.split("/")
    if ".git" in segs or "forge.yaml" in segs:
        return True
    return any(f == s.rstrip("/") or (s.endswith("/") and f.startswith(s)) or f.endswith("/" + s)
               for s in SENSITIVE)


def target_problem(real_root, rel):
    """Host-side check of the REAL target (call it at plan time and again right before the write).
    Returns (reason, canonical_rel): reason None = the target may be written.
    Every existing component must be spelled exactly as on disk (no case or normalization alias),
    must not be a symlink, and the realpath form must stay inside the repository and out of .git."""
    root = os.path.realpath(real_root)
    segs = rel.strip("/").split("/")
    cur, canon = root, []
    for i, seg in enumerate(segs):
        try:
            names = os.listdir(cur)
        except FileNotFoundError:
            canon.extend(segs[i:])
            break
        except NotADirectoryError:
            return "a parent of the target is a file in the real repository", None
        fs = fold_seg(seg)
        same = [n for n in names if fold_seg(n) == fs]
        if same and seg not in same:
            alias = "/".join(canon + [same[0]])
            return f"case or normalization alias of the existing path {alias!r} (not applied)", None
        if not same:
            canon.extend(segs[i:])
            break
        nxt = os.path.join(cur, seg)
        if os.path.islink(nxt):
            return "a symlink in the real repository is on the path (not applied)", None
        canon.append(seg)
        cur = nxt
    real = os.path.realpath(os.path.join(root, *segs))
    if real != root and not real.startswith(root + os.sep):
        return "the real path leaves the repository (symlink in a parent)", None
    rrel = os.path.relpath(real, root).replace(os.sep, "/")
    if ".git" in fold(rrel).split("/"):
        return "the real path is inside .git", None
    return None, rrel


def _sha_file(path):
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return None


def _inside(root, path):
    real_root = os.path.realpath(root)
    parent = os.path.realpath(os.path.dirname(path))
    if parent != real_root and not parent.startswith(real_root + os.sep):
        return False
    return not os.path.islink(path)


def plan(pol, changes, export_dir, stage_root, real_root, ignored=lambda rel: False):
    """Returns (apply, rejected). apply: list of change dicts with 'new' (bytes or None).
    rejected: list of (path, reason)."""
    apply, rejected = [], []
    groups = {}
    for c in changes:
        groups.setdefault(fold(c["path"].strip("/")), set()).add(c["path"])
    for c in changes:
        rel = c["path"]
        why = c.get("refused") or refusal(pol, rel, c.get("symlink", False))
        if not why and len(groups[fold(rel.strip("/"))]) > 1:
            why = ("case or normalization collision with " + ", ".join(
                repr(x) for x in sorted(groups[fold(rel.strip("/"))]) if x != rel) + " (none of them applied)")
        if why:
            rejected.append((rel, why))
            continue
        if ignored(rel):
            rejected.append((rel, "ignored by the repository's .gitignore"))
            continue
        real = os.path.join(real_root, rel)
        if not _inside(real_root, real):
            rejected.append((rel, "the real path leaves the repository (symlink in a parent)"))
            continue
        why, canon = target_problem(real_root, rel)
        if not why:
            why = refusal(pol, canon)
            if why:
                why = f"its real path {canon!r} is protected: {why}"
        if not why:
            why, _ = target_problem(stage_root, rel)  # the base copy must not alias either
            if why:
                why = "in the staged copy: " + why
        if why:
            rejected.append((rel, why))
            continue
        base = os.path.join(stage_root, rel)
        base_sha = _sha_file(base) if os.path.lexists(base) else None
        real_sha = _sha_file(real) if os.path.lexists(real) else None
        if base_sha != real_sha:
            rejected.append((rel, "changed in the real repository since the box started (not overwritten)"))
            continue
        new = None
        if c["status"] != "deleted":
            new = open(os.path.join(export_dir, c["file"]), "rb").read()
            if hashlib.sha256(new).hexdigest() != c.get("sha256"):
                rejected.append((rel, "exported content does not match its hash"))
                continue
        apply.append(dict(c, new=new, base=base))
    return apply, rejected


def render(apply, max_lines=4000):
    out = []
    for c in apply:
        old = b""
        if os.path.exists(c["base"]):
            old = open(c["base"], "rb").read()
        new = c["new"] or b""
        flag = "   <-- SENSITIVE: changes what tools on your machine run; read it" if sensitive(c["path"]) else ""
        if b"\x00" in old[:8192] or b"\x00" in new[:8192]:
            out.append(f"binary {c['status']}: {c['path']} ({len(new)} bytes){flag}")
            continue
        a = old.decode("utf-8", "replace").splitlines(keepends=True)
        b = new.decode("utf-8", "replace").splitlines(keepends=True)
        out.append(f"{c['status']}: {c['path']}{flag}")
        out.extend(x.rstrip("\n") for x in difflib.unified_diff(
            a, b, "a/" + c["path"] if c["status"] != "added" else "/dev/null",
            "b/" + c["path"] if c["status"] != "deleted" else "/dev/null"))
    if len(out) > max_lines:
        out = out[:max_lines] + [f"... {len(out) - max_lines} more lines (all of them are applied on y)"]
    return "\n".join(out)


def _mkdirs(real_root, rel_dir):
    """Create the parent directories one by one; never through a symlink."""
    cur = os.path.realpath(real_root)
    for seg in [x for x in rel_dir.split("/") if x]:
        cur = os.path.join(cur, seg)
        if not os.path.lexists(cur):
            os.mkdir(cur, 0o755)
        if os.path.islink(cur) or not os.path.isdir(cur):
            return False
    return True


def apply_changes(apply, real_root):
    """Write the approved changes. Every target is checked again right before its write (TOCTOU):
    the alias, symlink and .git checks of target_problem, before and after creating parents."""
    done = []
    for c in apply:
        rel = c["path"].strip("/")
        real = os.path.join(real_root, rel)
        if (rel == ".git" or ".git" in fold(rel).split("/") or not _inside(real_root, real)
                or target_problem(real_root, rel)[0]):
            continue
        if c["status"] == "deleted":
            if os.path.lexists(real) and not os.path.islink(real):
                os.remove(real)
                done.append(c["path"])
            continue
        if not _mkdirs(real_root, os.path.dirname(rel)):
            continue
        if not _inside(real_root, real) or target_problem(real_root, rel)[0]:
            continue
        tmp = os.path.join(os.path.dirname(real), f".forge-airlock.{os.getpid()}.{os.urandom(6).hex()}.tmp")
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        except OSError:
            continue
        with os.fdopen(fd, "wb") as f:
            f.write(c["new"])
            os.fchmod(f.fileno(), 0o755 if c.get("mode") == "100755" else 0o644)
        if target_problem(real_root, rel)[0] or (os.path.lexists(real) and os.path.islink(real)):
            os.remove(tmp)
            continue
        os.replace(tmp, real)
        done.append(c["path"])
    return done
