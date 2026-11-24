# boxinit.py - PID 1 of the forge vfkit agent VM (runs as root in the guest, before the agent).
#  1. mounts /sys, /tmp, /run; loads the modules of the image (fuse, virtiofs, vsock, virtio vsock);
#  2. mounts the control share (tag forgectl), reads job.json, UNMOUNTS the share (the agent never
#     sees the job, which holds the canary token), and makes /dev/hvc1 (the status port) root-only;
#  3. mounts the job's virtio-fs shares (nosuid, nodev; read-only ones with ro);
#  4. writes /etc/hosts (each forwarded name -> 127.0.0.1) and starts one listener per forward:
#     TCP 127.0.0.1:<port> in the guest -> a stream of ONE vsock connection to the host
#     (vsockmux.py) -> the host side, which connects ONLY to the fixed target of that port (the
#     airlock's published port, ...). The guest has no network device (no NIC driver is in the
#     image): this tunnel is its only way out;
#  5. runs the job's command as job.uid in a new session on /dev/hvc0 (the host's stdio);
#  6. writes "FORGE-VM-RC <nonce> <exit code>" to /dev/hvc1 and powers the VM off.
#     The command starts only after the tunnel's host end answered (HELLO, vsockmux.open_tunnel).
# Label: NATIVE CHECK (hand-written, not proved).
import json
import os
import socket
import subprocess
import sys
import threading

sys.path.insert(0, "/forge-guest")  # python -I does not add the script's folder
import vsockmux  # noqa: E402

BB = "/bin/busybox.static"


def log(msg):
    try:
        with open("/dev/hvc1", "w") as f:
            f.write(f"[boxinit] {msg}\n")
    except OSError:
        pass


def sh(*args):
    r = subprocess.run([BB] + list(args), capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"{' '.join(args)}: {r.stderr.strip()}")


def status(nonce, rc):
    with open("/dev/hvc1", "w") as f:
        f.write(f"FORGE-VM-RC {nonce} {rc}\n")
        f.flush()


def poweroff():
    try:
        os.sync()
    except OSError:
        pass
    subprocess.run([BB, "poweroff", "-f"])


def relays(forwards):
    """One vsock connection to the host for the VM's lifetime (vsockmux.py); one TCP listener on
    127.0.0.1:<port> per forward; each accepted connection becomes a stream of the tunnel."""
    if not forwards:
        return
    vsockmux._dbg("guest: connecting the tunnel")
    link = vsockmux.open_tunnel(vsockmux.vsock_connector(socket.VMADDR_CID_HOST, vsockmux.MUX_PORT))  # waits for HELLO
    mux = vsockmux.Mux(link)
    for f in forwards:
        port = int(f["port"])
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", port))
        srv.listen(128)

        def serve(srv=srv, port=port):
            while True:
                c, _ = srv.accept()
                vsockmux._dbg(f"guest: accepted a client on :{port}")
                mux.connect(port, c)
        threading.Thread(target=serve, daemon=True).start()


def copy_fd(src, dst):
    while True:
        try:
            d = os.read(src, 65536)
        except OSError:
            return
        if not d:
            return
        while d:
            d = d[os.write(dst, d):]


def raw_output(fd):
    import termios
    a = termios.tcgetattr(fd)
    a[1] &= ~termios.OPOST                                   # no \n -> \r\n
    a[3] &= ~(termios.ECHO | termios.ICANON | termios.ISIG)  # no echo, no line editing, no signals
    termios.tcsetattr(fd, termios.TCSANOW, a)


def main():
    nonce = "none"
    try:
        sh("mount", "-t", "sysfs", "sys", "/sys")
        sh("mount", "-t", "tmpfs", "-o", "mode=1777,nosuid,nodev", "tmp", "/tmp")
        sh("mount", "-t", "tmpfs", "-o", "mode=755,nosuid,nodev", "run", "/run")
        sh("hostname", "forge-box")
        os.chmod("/dev/hvc1", 0o600)
        d = "/lib/modules/forge"
        for m in open(f"{d}/ORDER").read().split()[2:]:  # 0 and 1 (the rng) are loaded by /init
            sh("insmod", f"{d}/{m}")
        sh("mount", "-t", "virtiofs", "-o", "ro,nosuid,nodev,noexec", "forgectl", "/forge-ctl")
        job = json.load(open("/forge-ctl/job.json"))
        sh("umount", "/forge-ctl")
        nonce = job["nonce"]
        if job.get("debug"):
            vsockmux.DEBUG = log
        for m in sorted(job.get("mounts", []), key=lambda m: m["target"].count("/")):  # parents first
            os.makedirs(m["target"], exist_ok=True)
            sh("mount", "-t", "virtiofs", "-o", ("ro," if m.get("ro") else "") + "nosuid,nodev", m["tag"], m["target"])
        for p in job.get("ro_paths", []):  # read-only files and closed dirs of the workspace: a read-only
            if os.path.lexists(p) and not os.path.islink(p):  # bind is also a mount point: no rename, no delete
                sh("mount", "--bind", p, p)
                sh("mount", "-o", "remount,bind,ro,nosuid,nodev", p)
        if job.get("nat"):  # the planted network breach of the negative control ONLY: the NIC driver is
            os.makedirs("/forge-nat", exist_ok=True)  # not in the image; the control shares it (tag natctl)
            sh("mount", "-t", "virtiofs", "-o", "ro", "natctl", "/forge-nat")
            for m in ("failover", "net_failover", "virtio_net"):
                sh("insmod", f"/forge-nat/{m}.ko")
            sh("ip", "link", "set", "eth0", "up")
            # macOS vmnet shared mode: 192.168.64.0/24, gateway and DNS proxy .1 (no DHCP lease is waited for)
            sh("ip", "addr", "add", "192.168.64.213/24", "dev", "eth0")
            sh("ip", "route", "add", "default", "via", "192.168.64.1")
            with open("/etc/resolv.conf", "w") as f:
                f.write("nameserver 192.168.64.1\n")
        names = [n for f in job.get("forwards", []) for n in f["names"]]
        with open("/etc/hosts", "w") as f:
            f.write("127.0.0.1 localhost " + " ".join(names) + "\n")
        sh("ip", "link", "set", "lo", "up")
    except Exception as e:  # noqa: BLE001  fail closed: the command never runs
        log(f"setup failed: {e}")
        status(nonce, 125)
        poweroff()
        return
    tty = bool(job.get("tty"))
    con = os.open("/dev/hvc0", os.O_RDWR | os.O_NOCTTY)
    if not tty:
        raw_output(con)
        rpipe, wpipe = os.pipe()
    env = {"PATH": "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin", "HOME": "/tmp", "TERM": "xterm"}
    env.update(job.get("env", {}))
    go_r, go_w = os.pipe()
    pid = os.fork()  # before any thread exists in PID 1 (the relays start after the fork)
    if pid == 0:
        try:
            os.close(go_w)
            if os.read(go_r, 1) != b"1":  # the parent could not start the relays: never run the command
                os._exit(125)
            os.setsid()
            if tty:
                fd = os.open("/dev/hvc0", os.O_RDWR)  # the session's controlling terminal
                os.dup2(fd, 0)
            else:  # like `docker run -T`: stdin /dev/null, stdout and stderr a pipe (not a terminal)
                os.dup2(os.open("/dev/null", os.O_RDONLY), 0)
                fd = wpipe
            os.dup2(fd, 1)
            os.dup2(fd, 2)
            uid, gid = int(job.get("uid", 1000)), int(job.get("gid", 1000))
            os.setgroups([])
            os.setgid(gid)
            os.setuid(uid)
            os.chdir(job.get("cwd", "/"))
            os.umask(0o022)
            os.execvpe(job["argv"][0], job["argv"], env)
        except Exception as e:  # noqa: BLE001
            os.write(2, f"forge-box: cannot run {job['argv'][:1]}: {e}\n".encode())
        os._exit(127)
    os.close(go_r)
    try:
        relays(job.get("forwards", []))
        os.write(go_w, b"1")
    except Exception as e:  # noqa: BLE001
        log(f"setup failed: relays: {e}")
    os.close(go_w)
    if not tty:
        pump = threading.Thread(target=copy_fd, args=(rpipe, con))
        pump.start()
        os.close(wpipe)
    rc = 255
    while True:
        try:
            p, st = os.wait()
        except ChildProcessError:
            break
        if p == pid:
            rc = os.waitstatus_to_exitcode(st)
            rc = rc if rc >= 0 else 128 - rc
            break
    try:
        os.kill(-1, 9)  # everything the agent left behind
    except OSError:
        pass
    if not tty:
        pump.join(timeout=5)
    status(nonce, rc)
    poweroff()


if __name__ == "__main__":
    try:
        main()
    finally:
        poweroff()
        sys.exit(0)
