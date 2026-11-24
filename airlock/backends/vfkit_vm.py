#!/usr/bin/env python3
"""vfkit_vm.py - run ONE command in a disposable Linux VM (Apple Virtualization.framework via vfkit).

Used by `forge up --isolation microvm` on macOS (forge_airlock.py) and by the tests:
  python3 -I airlock/backends/vfkit_vm.py [--share TAG=HOSTDIR:GUESTDIR[:ro]]... [--forward PORT=HOST:HOSTPORT[,NAME]...]...
          [--env K=V]... [--uid N] [--gid N] [--cwd DIR] [--tty] [--nat-control] -- <command> [args...]
Exit: the command's exit code; 2 = vfkit, the image or hardware virtualization is missing (fail
closed, nothing ran); 124 = timeout; 125 = the VM did not report an exit code (setup failed).

The VM (see guest/boxinit.py):
  - own kernel (Alpine linux-virt, pinned) and an initramfs root (vfkit_image.py); 1 GiB, 2 vCPUs;
  - NO network device. Its only way out is one vsock connection (guest/vsockmux.py) whose
    streams the host relay connects to the --forward targets only: guest TCP 127.0.0.1:PORT ->
    HOST:HOSTPORT (in forge up: the airlock's port published on the host's 127.0.0.1, or the
    egress proxy's). Any other port is refused. Nothing else is reachable;
  - workspace and other folders as virtio-fs shares (nosuid, nodev; :ro ones read-only in the guest);
  - the job (command, environment, canary token) comes through a control share that the guest's
    PID 1 reads and unmounts before the command starts; the command never sees it;
  - stdio of the command is the host's stdio (virtio console hvc0); kernel messages and the exit
    status go to a log file (hvc1), not to the program's output.
--nat-control is the planted NETWORK breach for the negative control only: it adds a NAT network
device (vmnet) and a NIC driver; the canary must then report BREACH.
Host side: Python standard library only. Label: NATIVE CHECK (the VM boundary is Apple's
hypervisor and the guest kernel; this file only configures it; nothing here is proved)."""
import json
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import vfkit_image  # noqa: E402
sys.path.insert(0, os.path.join(HERE, "guest"))
import vsockmux  # noqa: E402

EXIT_UNAVAILABLE = 2


class Unavailable(Exception):
    pass


def vfkit_path():
    p = os.environ.get("FORGE_VFKIT") or shutil.which("vfkit") or (
        "/opt/homebrew/bin/vfkit" if os.path.exists("/opt/homebrew/bin/vfkit") else None)
    return p


def preflight(cache=None):
    """(vfkit binary, image folder) or raise Unavailable with the reason (fail closed)."""
    if sys.platform != "darwin":
        raise Unavailable("the vfkit backend needs macOS (Apple Virtualization.framework)")
    if os.uname().machine != "arm64":
        raise Unavailable("the vfkit agent image is arm64 only (Apple silicon)")
    hv = subprocess.run(["sysctl", "-n", "kern.hv_support"], capture_output=True, text=True).stdout.strip()
    if os.environ.get("FORGE_VFKIT_TEST_NO_HV"):  # negative control: can only make the check stricter
        hv = "0"
    if hv != "1":
        raise Unavailable(f"hardware virtualization is not available on this Mac (kern.hv_support={hv or 'unset'}); "
                          "a VM inside a VM (for example a CI runner) has none")
    vf = vfkit_path()
    if not vf or not os.access(vf, os.X_OK):
        raise Unavailable("vfkit is not installed: `brew install vfkit` (no sudo), or set FORGE_VFKIT to its path")
    img = vfkit_image.image_dir(cache)
    if not img:
        raise Unavailable("the vfkit agent image is not built: run `python3 -I airlock/backends/vfkit_image.py build` "
                          "(downloads pinned Alpine packages into ~/Library/Caches/forge-airlock/vfkit)")
    return vf, img


def lock_readonly(ws, rels):
    """Set the macOS user-immutable flag (chflags uchg) on each read-only file and closed directory
    (and everything in it) of the host workspace folder, without following symlinks.
    Why: the guest's read-only binds cover the exact names only, and the host file system is
    case-insensitive: a guest chmod or write of README.MD reaches a read-only README.md through
    virtio-fs (tested). An immutable file cannot be written, chmod-ed, renamed, linked or deleted
    through any name, also not by its owner (the vfkit process); virtio-fs has no call to clear the
    flag (the guest's chattr fails). Returns the number of entries locked."""
    import stat as st
    root = os.path.realpath(ws)
    n = 0
    for rel in rels:
        full = os.path.join(root, rel)
        if os.path.realpath(os.path.dirname(full)) != os.path.dirname(full) or not os.path.lexists(full):
            continue  # a parent is a symlink (or the entry is gone): never lock outside the workspace
        todo = [full]
        if os.path.isdir(full) and not os.path.islink(full):
            for d, dirs, files in os.walk(full):
                todo += [os.path.join(d, x) for x in dirs + files]
        for p in todo:
            if hasattr(os, "lchflags"):
                os.lchflags(p, os.lstat(p).st_flags | st.UF_IMMUTABLE)
                n += 1
    return n


def unlock_tree(ws):
    """Clear the flags of every entry under ws and make its folders writable for their owner
    (closed dirs are 0555), so that the run folder can be deleted. Never follows a symlink."""
    import stat as st
    if not os.path.isdir(ws) or os.path.islink(ws):
        return
    for d, dirs, files in os.walk(ws):
        for x in [d] + [os.path.join(d, y) for y in dirs + files]:
            try:
                if hasattr(os, "lchflags"):
                    os.lchflags(x, 0)
                m = os.lstat(x).st_mode
                if st.S_ISDIR(m):
                    os.chmod(x, st.S_IMODE(m) | 0o700)
            except OSError:
                pass


class HostRelay:
    """The host end of the VM's tunnel (guest/vsockmux.py): vfkit connects to this unix socket when
    the guest opens its vsock connection; each OPEN frame names a guest port, and the relay
    connects ONLY to the fixed TCP target of that port (the allowlist is this map; any other port
    is refused with RST)."""

    def __init__(self, path, targets):
        self.path, self.targets = path, dict(targets)
        self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.srv.bind(path)
        os.chmod(path, 0o600)
        self.srv.listen(4)
        self.count, self.refused = 0, 0
        self.drop_first = bool(os.environ.get("FORGE_VFKIT_TEST_DROP_FIRST_TUNNEL"))
        threading.Thread(target=self.serve, daemon=True).start()

    def open(self, _sid, port):
        target = self.targets.get(port)
        if target is None:
            self.refused += 1
            return None
        vsockmux._dbg(f"host open sid={_sid} port={port} -> {target}")
        s = socket.create_connection(target, timeout=10)
        s.settimeout(None)
        vsockmux._dbg(f"host connected sid={_sid}")
        self.count += 1
        return s

    def serve(self):
        while True:
            try:
                c, _ = self.srv.accept()
            except OSError:
                return
            vsockmux._dbg("host: vfkit connected the tunnel")
            if self.drop_first:  # test knob: the failure seen at boot (host end dropped, no error in the guest)
                self.drop_first = False
                vsockmux._dbg("host: TEST: dropping this tunnel without HELLO")
                c.close()
                continue
            vsockmux.Mux(c, on_open=self.open)

    def close(self):
        try:
            self.srv.close()
        except OSError:
            pass


def run(argv, shares=(), forwards=(), env=None, uid=1000, gid=1000, cwd="/", tty=False, cpus=2, mem_mib=1024,
        timeout=None, log_path=None, nat_control=False, cache=None, stdout=None, ro_paths=()):
    """Run argv in a new VM; return its exit code (see the module text).
    shares: [(tag, host_dir, guest_dir, read_only)]; forwards: [(port, (host, hostport), [names])].
    Raises Unavailable before anything starts if vfkit / the image / virtualization is missing."""
    vf, img = preflight(cache)
    dbg = os.environ.get("FORGE_VFKIT_DEBUG")  # a folder: host trace and the guest console log go there
    if dbg:
        os.makedirs(dbg, exist_ok=True)
        _lock = threading.Lock()
        hostlog = os.path.join(dbg, f"host-{os.getpid()}-{int(time.time() * 1000)}.log")

        def _w(m, _p=hostlog):
            with _lock, open(_p, "a") as f:
                f.write(m + "\n")
        vsockmux.DEBUG = _w
        if log_path is None:
            log_path = hostlog.replace("host-", "console-")
    work = tempfile.mkdtemp(prefix="fvm-", dir="/tmp")  # short: unix socket paths are limited to 104 bytes
    os.chmod(work, 0o700)
    ctl = os.path.join(work, "ctl")
    os.mkdir(ctl, 0o700)
    nonce = secrets.token_hex(8)
    job = {"nonce": nonce, "argv": list(argv), "env": dict(env or {}), "uid": uid, "gid": gid, "cwd": cwd,
           "tty": bool(tty), "mounts": [{"tag": t, "target": g, "ro": bool(ro)} for t, _h, g, ro in shares],
           "forwards": [{"port": int(p), "names": list(n)} for p, _t, n in forwards],
           "ro_paths": list(ro_paths), "nat": bool(nat_control), "debug": bool(os.environ.get("FORGE_VFKIT_DEBUG"))}
    with open(os.path.join(ctl, "job.json"), "w") as f:
        json.dump(job, f)
    log_path = log_path or os.path.join(work, "console.log")
    cmd = [vf, "--cpus", str(cpus), "--memory", str(mem_mib),
           "--bootloader", f"linux,kernel={img}/Image,initrd={img}/initrd,"
                           "cmdline=console=hvc1 rdinit=/init quiet loglevel=1 panic=-1",
           "--device", "virtio-serial,stdio",
           "--device", f"virtio-serial,logFilePath={log_path}",
           "--device", f"virtio-fs,sharedDir={ctl},mountTag=forgectl",
           "--device", "virtio-rng"]
    for tag, host, _g, _ro in shares:
        cmd += ["--device", f"virtio-fs,sharedDir={os.path.realpath(host)},mountTag={tag}"]
    relays = []
    if forwards:
        sp = os.path.join(work, "mux.sock")
        relays.append(HostRelay(sp, {int(port): target for port, target, _n in forwards}))
        cmd += ["--device", f"virtio-vsock,port={vsockmux.MUX_PORT},socketURL={sp},listen"]
    if nat_control:  # planted breach (negative control): a NAT NIC and its driver
        cmd += ["--device", "virtio-net,nat", "--device", f"virtio-fs,sharedDir={img}/nat-control,mountTag=natctl"]
    rc = 125
    old_term = signal.signal(signal.SIGTERM, _term) if threading.current_thread() is threading.main_thread() else None
    # vfkit's stdio console needs a terminal on stdin: give it a pseudo-terminal and copy its
    # output to ours (and, with tty=True, our input to it, our terminal in raw mode meanwhile)
    import pty
    import termios
    import tty as ttymod
    master, slave = pty.openpty()
    out_fd = stdout.fileno() if stdout is not None else sys.stdout.fileno()
    saved, p = None, None
    try:
        err = open(os.environ["FORGE_VFKIT_STDERR"], "ab") if os.environ.get("FORGE_VFKIT_STDERR") else subprocess.DEVNULL
        p = subprocess.Popen(cmd, stdin=slave, stdout=slave, stderr=err, start_new_session=True)
        os.close(slave)
        pump = threading.Thread(target=_copy_out, args=(master, out_fd), daemon=True)
        pump.start()
        if tty and os.isatty(0):
            saved = termios.tcgetattr(0)
            ttymod.setraw(0)
            stop_in = threading.Event()
            reader = threading.Thread(target=_copy_in, args=(0, master, stop_in), daemon=True)
            reader.start()
        try:
            p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            p.send_signal(signal.SIGTERM)
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()
            return 124
        pump.join(timeout=2)
        for _ in range(20):  # the log device may flush just after the VM stops
            got = _status(log_path, nonce)
            if got is not None:
                rc = got
                return got
            time.sleep(0.05)
        return rc
    finally:
        if p is not None and p.poll() is None:  # an exception or a signal here: never leave the VM running
            p.kill()
            p.wait()
        if old_term is not None:
            signal.signal(signal.SIGTERM, old_term)
        if saved is not None:
            stop_in.set()
            reader.join(timeout=1)
            termios.tcsetattr(0, termios.TCSADRAIN, saved)
        try:
            os.close(master)
        except OSError:
            pass
        for r in relays:
            r.close()
        if not os.environ.get("FORGE_VFKIT_KEEP"):
            shutil.rmtree(work, ignore_errors=True)


def _term(*_a):
    raise SystemExit(143)  # SIGTERM: unwind, so that the finally block stops the VM


def _copy_out(src, dst):
    while True:
        try:
            d = os.read(src, 65536)
        except OSError:
            return
        if not d:
            return
        try:
            os.write(dst, d)
        except OSError:
            return


def _copy_in(src, dst, stop):
    """Our terminal -> the VM console, until stop is set (then the terminal is ours again: a
    thread left reading it would swallow the answer to the y/N prompt after the run)."""
    import select
    while not stop.is_set():
        try:
            r, _, _ = select.select([src], [], [], 0.1)
            if not r:
                continue
            d = os.read(src, 4096)
            if not d:
                return
            os.write(dst, d)
        except OSError:
            return


def _status(log_path, nonce):
    try:
        text = open(log_path, "rb").read().decode("utf-8", "replace")
    except OSError:
        return None
    for ln in text.splitlines():
        parts = ln.strip().split()
        if len(parts) == 3 and parts[0] == "FORGE-VM-RC" and parts[1] == nonce:
            return int(parts[2])
    return None


def main(argv):
    import argparse
    if "--" not in argv:
        raise SystemExit("usage: vfkit_vm.py [options] -- <command> [args...]")
    i = argv.index("--")
    opts, command = argv[:i], argv[i + 1:]
    p = argparse.ArgumentParser(prog="vfkit_vm.py")
    p.add_argument("--share", action="append", default=[])
    p.add_argument("--forward", action="append", default=[])
    p.add_argument("--env", action="append", default=[])
    p.add_argument("--uid", type=int, default=1000)
    p.add_argument("--gid", type=int, default=None)
    p.add_argument("--cwd", default="/")
    p.add_argument("--tty", action="store_true")
    p.add_argument("--timeout", type=float, default=None)
    p.add_argument("--log", default=None)
    p.add_argument("--nat-control", action="store_true")
    a = p.parse_args(opts)
    shares = []
    for n, s in enumerate(a.share):
        tag, rest = s.split("=", 1)
        parts = rest.split(":")
        ro = parts[-1] == "ro"
        if ro:
            parts = parts[:-1]
        shares.append((tag or f"s{n}", parts[0], parts[1], ro))
    fwd = []
    for s in a.forward:
        port, rest = s.split("=", 1)
        target, *names = rest.split(",")
        h, hp = target.rsplit(":", 1)
        fwd.append((int(port), (h, int(hp)), names))
    env = dict(e.split("=", 1) for e in a.env)
    try:
        return run(command, shares, fwd, env, a.uid, a.gid if a.gid is not None else a.uid, a.cwd, a.tty,
                   timeout=a.timeout, log_path=a.log, nat_control=a.nat_control)
    except Unavailable as e:
        print(f"vfkit_vm: {e} (fail closed; nothing ran)", file=sys.stderr)
        return EXIT_UNAVAILABLE


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
