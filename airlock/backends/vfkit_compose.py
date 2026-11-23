#!/usr/bin/env python3
"""vfkit_compose.py - run a compose AGENT service in a vfkit VM instead of a container (macOS microvm).

The same service definition drives both backends, so the VM gets what the container would get
and nothing more:
  environment  the service's variables (a bare NAME takes its value from this process, like compose);
  user         uid:gid of the command in the guest (the guest's root is used only by its PID 1);
  working_dir  the command's directory;
  volumes      a named volume must be mapped to a host folder (--volume ws=DIR): it becomes a virtio-fs
               share (a subpath shares that subfolder); a bind mount is COPIED into a private folder
               first and shared read-only (a guest root cannot write the original); tmpfs /tmp exists;
  networks     each network maps to vsock forwards (--net NET=PORT:HOST:HOSTPORT:NAME[,...]): the
               guest reaches NAME:PORT and nothing else. An unmapped network, volume or option that
               widens the box (privileged, cap_add, devices, network_mode host, pid host, ipc host)
               is refused: fail closed, nothing runs (exit 2);
  command      the service's command, or the one given after --.
Read-only paths of the workspace (the airlock's workspace.json: read-only files and closed
directories) are bound read-only inside the guest (--ro-file), because the guest's virtio-fs shows
every file as owned by the caller, so file ownership cannot protect them as it does in a container.

  vfkit_compose.py --config <compose config json> --service S --volume ws=DIR [--net ...]...
                   [-e NAME[=VALUE]]... [--ro-list FILE] [--root ROOT] [--pidfile F] [-- command...]
Exit: the command's exit code; 2 = refused or the backend is unavailable (nothing ran).
Host side: Python standard library only. Label: NATIVE CHECK (hand-written, not proved)."""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import vfkit_vm  # noqa: E402

WIDENING = ("privileged", "cap_add", "devices", "pid", "ipc", "network_mode", "volumes_from", "userns_mode")


class Refused(Exception):
    pass


def _norm_volumes(vols):
    out = []
    for v in vols or []:
        if isinstance(v, dict):
            out.append({"type": v.get("type", "volume"), "source": v.get("source"), "target": v["target"],
                        "ro": bool(v.get("read_only")), "subpath": (v.get("volume") or {}).get("subpath")})
            continue
        parts = v.split(":")
        ro = len(parts) > 2 and "ro" in parts[2].split(",")
        src, tgt = parts[0], parts[1]
        out.append({"type": "bind" if src.startswith(("/", ".")) else "volume", "source": src, "target": tgt,
                    "ro": ro, "subpath": None})
    return out


def _norm_env(env, environ):
    out = {}
    if isinstance(env, dict):
        items = [(k, v) for k, v in env.items()]
    else:
        items = [(e.split("=", 1)[0], e.split("=", 1)[1] if "=" in e else None) for e in env or []]
    for k, v in items:
        if v is None:
            if k in environ:
                out[k] = environ[k]
        else:
            out[k] = str(v)
    return out


def job(svc, volume_dirs, net_forwards, work, extra_env=(), command=None, environ=None, ro_paths=()):
    """vfkit_vm.run keyword arguments for compose service dict svc. Raises Refused."""
    environ = os.environ if environ is None else environ
    for k in WIDENING:
        if svc.get(k) not in (None, [], {}, ""):
            raise Refused(f"service option {k} would widen the box; the vfkit backend refuses it")
    env = _norm_env(svc.get("environment"), environ)
    env.update(_norm_env(list(extra_env), environ))
    user = str(svc.get("user") or "1000:1000")
    uid, _, gid = user.partition(":")
    shares, n = [], 0
    for v in _norm_volumes(svc.get("volumes")):
        n += 1
        if v["type"] == "volume":
            if v["source"] not in volume_dirs:
                raise Refused(f"volume {v['source']} has no host folder for the VM")
            host = volume_dirs[v["source"]]
            if v["subpath"]:
                if ".." in v["subpath"].split("/"):
                    raise Refused(f"volume subpath {v['subpath']!r}")
                host = os.path.join(host, v["subpath"])
            shares.append((f"v{n}", host, v["target"], v["ro"]))
        elif v["type"] == "bind":
            src = v["source"]
            if not os.path.exists(src):
                raise Refused(f"bind source {src} does not exist")
            copy = os.path.join(tempfile.mkdtemp(prefix=f"bind{n}-", dir=work), "d")
            if os.path.isdir(src):
                shutil.copytree(src, copy, symlinks=True)
            else:
                raise Refused(f"bind of a single file ({src}) is not supported by the vfkit backend")
            shares.append((f"v{n}", copy, v["target"], True))
        elif v["type"] == "tmpfs":
            continue
        else:
            raise Refused(f"volume type {v['type']}")
    nets = svc.get("networks") or []
    nets = list(nets) if not isinstance(nets, dict) else list(nets.keys())
    forwards = []
    for net in nets:
        if net not in net_forwards:
            raise Refused(f"network {net} has no vsock mapping for the VM")
        forwards += net_forwards[net]
    cmd = command or svc.get("command")
    if isinstance(cmd, str):
        cmd = ["sh", "-c", cmd]
    if not cmd:
        raise Refused("no command")
    return {"argv": cmd, "shares": shares, "forwards": forwards, "env": env, "uid": int(uid),
            "gid": int(gid or uid), "cwd": svc.get("working_dir") or "/", "ro_paths": list(ro_paths)}


def _chmod_tree(ws, rels, writable):
    """Read-only entries (and everything in a closed dir): writable -> u+w; else the airlock's modes
    (files 0444, dirs 0555). Never follows a symlink."""
    import stat as st
    n = 0
    for rel in rels:
        full = os.path.join(ws, rel)
        todo = [full]
        if os.path.isdir(full) and not os.path.islink(full):
            for d, dirs, files in os.walk(full):
                todo += [os.path.join(d, x) for x in dirs + files]
        for p in todo:
            if not os.path.lexists(p) or os.path.islink(p):
                continue
            is_dir = st.S_ISDIR(os.lstat(p).st_mode)
            os.chmod(p, (0o755 if is_dir else 0o644) if writable else (0o555 if is_dir else 0o444))
            n += 1
    return n


def main(argv):
    import argparse
    cmd = None
    if "--" in argv:
        i = argv.index("--")
        argv, cmd = argv[:i], argv[i + 1:] or None
    p = argparse.ArgumentParser(prog="vfkit_compose.py")
    p.add_argument("--config", required=True)
    p.add_argument("--service", required=True)
    p.add_argument("--volume", action="append", default=[])
    p.add_argument("--net", action="append", default=[])
    p.add_argument("-e", dest="env", action="append", default=[])
    p.add_argument("--ro-list", default=None, help="JSON {readonly_files, closed_dirs} (paths under --root)")
    p.add_argument("--root", default="/workspace")
    p.add_argument("--agent-code", default=os.path.join(os.path.dirname(HERE), "agent"),
                   help="copied and shared read-only at /forge/agent (the container image has it there)")
    p.add_argument("--timeout", type=float, default=None)
    p.add_argument("--nat-control", action="store_true")
    p.add_argument("--lock", action="store_true",
                   help="also set the host immutable flag on the --ro-list entries of the ws volume folder")
    p.add_argument("--plant-unprotected", action="store_true",
                   help="NEGATIVE CONTROL ONLY: clear the host flags and make the --ro-list entries writable for "
                        "this run (no guest read-only binds either); restored afterwards")
    a = p.parse_args(argv)
    doc = json.load(open(a.config))
    svc = doc["services"].get(a.service)
    if svc is None:
        print(f"vfkit_compose: no service {a.service}", file=sys.stderr)
        return 2
    vols = dict(v.split("=", 1) for v in a.volume)
    nets = {}
    for spec in a.net:
        net, rest = spec.split("=", 1)
        nets.setdefault(net, [])
        for one in rest.split(","):
            port, host, hport, name = one.split(":")
            nets[net].append((int(port), (host, int(hport)), [name]))
    ro = []
    if a.ro_list:
        d = json.load(open(a.ro_list))
        rels = d.get("closed_dirs", []) + d.get("readonly_files", [])
        ro = [f"{a.root.rstrip('/')}/{r}" for r in rels]
        if a.plant_unprotected and "ws" in vols:
            vfkit_vm.unlock_tree(vols["ws"])
            planted = _chmod_tree(vols["ws"], rels, writable=True)
            ro = []
        elif a.lock and "ws" in vols:
            vfkit_vm.lock_readonly(vols["ws"], rels)
    work = tempfile.mkdtemp(prefix="fvc-")
    try:
        try:
            kw = job(svc, vols, nets, work, a.env, cmd, ro_paths=ro)
        except Refused as e:
            print(f"vfkit_compose: {e} (fail closed; nothing ran)", file=sys.stderr)
            return 2
        code = os.path.join(work, "agent-code")
        shutil.copytree(a.agent_code, code, ignore=shutil.ignore_patterns("__pycache__"))
        kw["shares"].append(("agentcode", code, "/forge/agent", True))
        sys.stdout.flush()
        try:
            return vfkit_vm.run(kw.pop("argv"), timeout=a.timeout, nat_control=a.nat_control, **kw)
        except vfkit_vm.Unavailable as e:
            print(f"vfkit_compose: {e} (fail closed; nothing ran)", file=sys.stderr)
            return 2
    finally:
        shutil.rmtree(work, ignore_errors=True)
        if a.plant_unprotected and a.ro_list and "ws" in vols:  # back to the airlock's modes, then locked
            _chmod_tree(vols["ws"], rels, writable=False)
            vfkit_vm.lock_readonly(vols["ws"], rels)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
