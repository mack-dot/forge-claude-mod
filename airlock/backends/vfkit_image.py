#!/usr/bin/env python3
"""vfkit_image.py - the Linux kernel and root file system of the macOS microvm agent (vfkit).

  python3 -I airlock/backends/vfkit_image.py build [--cache DIR]   build (or reuse) the image; prints its folder
  python3 -I airlock/backends/vfkit_image.py check [--cache DIR]   exit 0 if the image is built and verifies
  python3 -I airlock/backends/vfkit_image.py relock                 maintainers: re-resolve and re-pin (network)

Inputs, all pinned by SHA-256 in vfkit.lock.json (a mismatch stops the build, exit 3):
  - Alpine Linux aarch64 packages: python3, git, busybox-static and their dependency closure;
  - linux-virt (the same Alpine release): the kernel (an EFI zboot image; the arm64 Image inside
    it is what Virtualization.framework boots) and 7 modules (virtio-rng and rng-core, fuse, virtiofs,
    vsock, the virtio vsock transport). No network driver is put into the image: the guest has no network device.
  - airlock/backends/guest/*: the guest's PID 1 (boxinit.py) and the /init that starts it.
Output (outside the repository; default ~/Library/Caches/forge-airlock/vfkit/<key>/):
  Image   the uncompressed arm64 kernel
  initrd  a newc cpio archive, uncompressed, built deterministically (sorted, mtime 0, uid 0):
          the same lock and guest files give the same bytes (manifest.json records both hashes).
Downloads go to <cache>/dl/ and are reused when their hash matches. Alpine keeps only the newest
build of each package in its v3.22 repository, so a 404 or a mismatch after an Alpine update means
"run relock" (and review the new hashes), never "skip the check".
Host side: Python standard library only. Label: NATIVE CHECK (hand-written build, not proved)."""
import gzip
import hashlib
import io
import json
import os
import struct
import sys
import tarfile
import urllib.request
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
LOCK = os.path.join(HERE, "vfkit.lock.json")
GUEST = os.path.join(HERE, "guest")
MIRROR = "https://dl-cdn.alpinelinux.org/alpine"
BRANCH, ARCH = "v3.22", "aarch64"
ROOTS = ["busybox-static", "python3", "python3-pycache-pyc0", "git"]
MODULES = ["kernel/drivers/char/hw_random/rng-core.ko.gz", "kernel/drivers/char/hw_random/virtio-rng.ko.gz",
           "kernel/fs/fuse/fuse.ko.gz", "kernel/fs/fuse/virtiofs.ko.gz", "kernel/net/vmw_vsock/vsock.ko.gz",
           "kernel/net/vmw_vsock/vmw_vsock_virtio_transport_common.ko.gz",
           "kernel/net/vmw_vsock/vmw_vsock_virtio_transport.ko.gz"]  # insmod in this order
# NOT in the image: the NIC driver, shared into the VM only by the network negative control
NAT_MODULES = ["kernel/net/core/failover.ko.gz", "kernel/drivers/net/net_failover.ko.gz",
               "kernel/drivers/net/virtio_net.ko.gz"]
SKIP_PREFIX = ("usr/share/man/", "usr/share/doc/", "usr/share/info/", "usr/share/git-core/templates/",
               "usr/share/perl5/", "usr/lib/python3.12/ensurepip/", "usr/lib/python3.12/idlelib/",
               "usr/lib/python3.12/tkinter/", "usr/lib/python3.12/turtledemo/")


def default_cache():
    base = os.environ.get("FORGE_VFKIT_CACHE")
    if base:
        return base
    return os.path.join(os.path.expanduser("~"), "Library", "Caches", "forge-airlock", "vfkit")


def sha256(b):
    return hashlib.sha256(b).hexdigest()


def fetch(url, dest, want):
    """dest holds the bytes of url with SHA-256 want (download once; verify every time)."""
    if not os.path.exists(dest):
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        try:
            data = urllib.request.urlopen(url, timeout=120).read()
        except Exception as e:  # noqa: BLE001
            raise SystemExit(f"vfkit_image: download failed: {url}: {e} (Alpine may have replaced the package: "
                             "run `vfkit_image.py relock` and review the new hashes)")
        tmp = dest + ".part"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, dest)
    data = open(dest, "rb").read()
    if sha256(data) != want:
        print(f"vfkit_image: SHA-256 mismatch for {url} (got {sha256(data)}, pinned {want})", file=sys.stderr)
        raise SystemExit(3)
    return data


# ---------------------------------------------------------------- relock (maintainers)
def apkindex():
    raw = urllib.request.urlopen(f"{MIRROR}/{BRANCH}/main/{ARCH}/APKINDEX.tar.gz", timeout=60).read()
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz", ignore_zeros=True) as t:
        text = t.extractfile("APKINDEX").read().decode()
    pkgs, prov = {}, {}
    for rec in text.split("\n\n"):
        d = {}
        for ln in rec.splitlines():
            if len(ln) > 2 and ln[1] == ":":
                d.setdefault(ln[0], ln[2:])
        if "P" not in d:
            continue
        pkgs[d["P"]] = d
        prov.setdefault(d["P"], d["P"])
        for p in d.get("p", "").split():
            prov.setdefault(p.split("=")[0], d["P"])
    return pkgs, prov


def closure(pkgs, prov, roots):
    seen, todo = [], list(roots)
    while todo:
        n = todo.pop(0)
        if n.startswith("!"):
            continue
        for op in (">=", "<=", "~", "=", ">", "<"):
            n = n.split(op)[0]
        p = prov.get(n)
        if p is None:
            raise SystemExit(f"vfkit_image relock: nothing provides {n}")
        if p not in seen:
            seen.append(p)
            todo += pkgs[p].get("D", "").split()
    return sorted(seen)


def cmd_relock(_a):
    pkgs, prov = apkindex()
    names = closure(pkgs, prov, ROOTS)
    lock = {"alpine": f"{BRANCH}/main/{ARCH}", "roots": ROOTS, "packages": [], "kernel": None}
    for n in names + ["linux-virt"]:
        file = f"{n}-{pkgs[n]['V']}.apk"
        data = urllib.request.urlopen(f"{MIRROR}/{BRANCH}/main/{ARCH}/{file}", timeout=300).read()
        ent = {"name": n, "version": pkgs[n]["V"], "file": file, "sha256": sha256(data)}
        os.makedirs(os.path.join(default_cache(), "dl"), exist_ok=True)
        with open(os.path.join(default_cache(), "dl", file), "wb") as f:
            f.write(data)
        if n == "linux-virt":
            lock["kernel"] = ent
        else:
            lock["packages"].append(ent)
        print(f"pinned {file} {ent['sha256']}", file=sys.stderr)
    with open(LOCK, "w") as f:
        json.dump(lock, f, indent=1)
        f.write("\n")
    return 0


# ---------------------------------------------------------------- build
def apk_members(data):
    """(TarInfo, bytes or None) of the data part of an .apk (concatenated gzip tar segments)."""
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz", ignore_zeros=True) as t:
        for m in t:
            if m.name.startswith(".") and "/" not in m.name:  # .PKGINFO, .SIGN.*, .pre-install ...
                continue
            yield m, (t.extractfile(m).read() if m.isreg() else None)


class Tree:
    """An in-memory root file system: path -> (mode, data or link target)."""

    def __init__(self):
        self.ent = {}

    def dir(self, path, mode=0o755):
        parts = path.strip("/").split("/")
        for i in range(1, len(parts) + 1):
            p = "/".join(parts[:i])
            if p and p not in self.ent:
                self.ent[p] = (0o040000 | (mode if i == len(parts) else 0o755), b"")

    def file(self, path, data, mode=0o644):
        path = path.strip("/")
        self.dir(os.path.dirname(path)) if "/" in path else None
        self.ent[path] = (0o100000 | mode, data)

    def link(self, path, target):
        path = path.strip("/")
        self.dir(os.path.dirname(path)) if "/" in path else None
        self.ent[path] = (0o120000 | 0o777, target.encode())

    def cpio(self):
        """newc archive, sorted, mtime 0, uid/gid 0, inode = index: deterministic bytes."""
        out = io.BytesIO()

        def rec(ino, name, mode, data):
            nb = name.encode() + b"\0"
            hdr = b"070701" + b"".join(b"%08X" % v for v in (
                ino, mode, 0, 0, 1, 0, len(data), 0, 0, 0, 0, len(nb), 0))
            out.write(hdr + nb + b"\0" * ((4 - (110 + len(nb)) % 4) % 4))
            out.write(data + b"\0" * ((4 - len(data) % 4) % 4))
        for i, p in enumerate(sorted(self.ent), start=1):
            mode, data = self.ent[p]
            rec(i, p, mode, data)
        rec(0, "TRAILER!!!", 0, b"")
        return out.getvalue()


def unzboot(b):
    """The arm64 Image inside an EFI zboot vmlinuz (header "zimg", gzip payload)."""
    if b[4:8] != b"zimg" or b[24:28] != b"gzip":
        raise SystemExit("vfkit_image: the kernel is not a gzip EFI zboot image")
    off, size = struct.unpack("<II", b[8:16])
    img = zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(b[off:off + size])
    if img[56:60] != b"ARMd":
        raise SystemExit("vfkit_image: the zboot payload is not an arm64 Image")
    return img


def guest_files():
    out = {}
    for n in sorted(os.listdir(GUEST)):
        p = os.path.join(GUEST, n)
        if os.path.isfile(p) and not n.startswith("."):
            out[n] = open(p, "rb").read()
    return out


def image_key(lock_bytes, guest):
    h = hashlib.sha256(b"forge-vfkit-image-v1\0" + lock_bytes)
    for n, b in sorted(guest.items()):
        h.update(n.encode() + b"\0" + sha256(b).encode())
    h.update(open(os.path.abspath(__file__), "rb").read())
    return h.hexdigest()[:16]


ETC = {
    "etc/passwd": "root:x:0:0:root:/root:/bin/sh\nagent:x:1000:1000:agent:/tmp:/bin/sh\nnobody:x:65534:65534::/:/sbin/nologin\n",
    "etc/group": "root:x:0:\nagent:x:1000:\nnogroup:x:65534:\n",
    "etc/hostname": "forge-box\n",
    "etc/os-release": "NAME=\"forge-airlock vfkit agent (Alpine Linux packages)\"\nID=forge-box\n",
}


def build(cache):
    lock_bytes = open(LOCK, "rb").read()
    lock = json.loads(lock_bytes)
    guest = guest_files()
    key = image_key(lock_bytes, guest)
    out = os.path.join(cache, key)
    if verify(out):
        return out
    dl = os.path.join(cache, "dl")
    t = Tree()
    for d in ("bin", "sbin", "usr/bin", "usr/sbin", "lib", "usr/lib", "etc", "dev", "proc", "sys", "run", "root",
              "workspace", "forge", "forge-run", "forge-ctl"):
        t.dir(d)
    t.dir("tmp", 0o1777)
    for p in lock["packages"]:
        data = fetch(f"{MIRROR}/{lock['alpine']}/{p['file']}", os.path.join(dl, p["file"]), p["sha256"])
        for m, body in apk_members(data):
            name = m.name.strip("/")
            if not name or name.startswith(SKIP_PREFIX):
                continue
            if m.isdir():
                t.dir(name, m.mode & 0o7777)
            elif m.issym():
                t.link(name, m.linkname)
            elif m.islnk():
                src = t.ent.get(m.linkname.strip("/"))
                if src:
                    t.ent[name] = src
            elif m.isreg():
                t.file(name, body, m.mode & 0o7777)
    # busybox applets used by the guest and the tests (the static busybox; no dynamic /bin/sh)
    t.link("bin/sh", "/bin/busybox.static")
    t.link("bin/busybox", "/bin/busybox.static")
    for k, v in ETC.items():
        t.file(k, v.encode())
    for n, b in guest.items():
        t.file(f"forge-guest/{n}", b, 0o755 if n in ("init",) or n.endswith(".py") else 0o644)
    t.link("init", "/forge-guest/init")
    kp = lock["kernel"]
    kdata = fetch(f"{MIRROR}/{lock['alpine']}/{kp['file']}", os.path.join(dl, kp["file"]), kp["sha256"])
    image, mods, nat = None, {}, {}
    for m, body in apk_members(kdata):
        if m.name == "boot/vmlinuz-virt":
            image = unzboot(body)
        for want in NAT_MODULES:
            if m.isreg() and m.name.startswith("lib/modules/") and m.name.endswith("/" + want):
                nat[os.path.basename(want)[:-3]] = gzip.decompress(body)
        for want in MODULES:
            if m.isreg() and m.name.startswith("lib/modules/") and m.name.endswith("/" + want):
                mods[want] = gzip.decompress(body)
    if image is None or len(mods) != len(MODULES) or len(nat) != len(NAT_MODULES):
        raise SystemExit("vfkit_image: the kernel package lacks the kernel or a module")
    for i, want in enumerate(MODULES):
        t.file(f"lib/modules/forge/{i}-{os.path.basename(want)[:-3]}", mods[want])
    t.file("lib/modules/forge/ORDER", "\n".join(f"{i}-{os.path.basename(w)[:-3]}" for i, w in enumerate(MODULES)).encode())
    initrd = t.cpio()
    tmp = out + ".tmp"
    os.makedirs(tmp, exist_ok=True)
    with open(os.path.join(tmp, "Image"), "wb") as f:
        f.write(image)
    with open(os.path.join(tmp, "initrd"), "wb") as f:
        f.write(initrd)
    os.makedirs(os.path.join(tmp, "nat-control"), exist_ok=True)
    for n, b in nat.items():
        with open(os.path.join(tmp, "nat-control", n), "wb") as f:
            f.write(b)
    man = {"key": key, "kernel": kp["file"], "Image_sha256": sha256(image), "initrd_sha256": sha256(initrd),
           "initrd_bytes": len(initrd), "packages": len(lock["packages"])}
    with open(os.path.join(tmp, "manifest.json"), "w") as f:
        json.dump(man, f, indent=1)
    import shutil
    if os.path.exists(out):
        shutil.rmtree(out)
    os.replace(tmp, out)
    for old in os.listdir(cache):  # earlier builds (other keys) are not used any more
        if old != key and len(old) == 16 and os.path.isdir(os.path.join(cache, old)):
            shutil.rmtree(os.path.join(cache, old), ignore_errors=True)
    return out


def verify(out):
    """True if out holds an Image and initrd whose hashes match its manifest."""
    try:
        man = json.load(open(os.path.join(out, "manifest.json")))
        return (sha256(open(os.path.join(out, "Image"), "rb").read()) == man["Image_sha256"]
                and sha256(open(os.path.join(out, "initrd"), "rb").read()) == man["initrd_sha256"])
    except (OSError, ValueError, KeyError):
        return False


def image_dir(cache=None):
    """The built image folder for the current lock and guest files, or None (not built / does not verify)."""
    cache = cache or default_cache()
    out = os.path.join(cache, image_key(open(LOCK, "rb").read(), guest_files()))
    return out if verify(out) else None


def main(argv):
    import argparse
    p = argparse.ArgumentParser(prog="vfkit_image.py")
    p.add_argument("cmd", choices=["build", "check", "relock"])
    p.add_argument("--cache", default=None)
    a = p.parse_args(argv)
    if a.cmd == "relock":
        return cmd_relock(a)
    cache = a.cache or default_cache()
    if a.cmd == "check":
        d = image_dir(cache)
        print(d or "not built")
        return 0 if d else 1
    d = build(cache)
    print(d)
    print(open(os.path.join(d, "manifest.json")).read(), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
