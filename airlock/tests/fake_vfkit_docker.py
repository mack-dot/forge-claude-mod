#!/usr/bin/env python3
"""fake_vfkit_docker.py - NEGATIVE CONTROL ONLY: a "vfkit" that boots no VM.

It reads the job that vfkit_vm.py prepared (the control share), runs the command in a plain Docker
container (runc: the SAME kernel as the airlock) with the job's shares and environment, and reports
the exit code on the status port exactly like the guest's boxinit.py. A backend that is really
the Docker host must be caught by the in-box kernel probe:
  FORGE_VFKIT=airlock/tests/fake_vfkit_docker.py forge up --isolation microvm ...  -> exit 10
Used by tests/run_up_e2e.py (case "fake vfkit"). Never use it for anything else."""
import json
import os
import subprocess
import sys


def main(argv):
    shares, log = {}, None
    for i, a in enumerate(argv):
        if a == "--device" and i + 1 < len(argv):
            kind, _, rest = argv[i + 1].partition(",")
            opts = dict(x.split("=", 1) for x in rest.split(",") if "=" in x)
            if kind == "virtio-fs":
                shares[opts["mountTag"]] = opts["sharedDir"]
            elif kind == "virtio-serial" and "logFilePath" in opts:
                log = opts["logFilePath"]
    job = json.load(open(os.path.join(shares["forgectl"], "job.json")))
    cmd = ["docker", "run", "--rm", "--network", "none", "-u", f"{job['uid']}:{job['gid']}", "-w", job["cwd"]]
    for m in job["mounts"]:
        cmd += ["-v", f"{shares[m['tag']]}:{m['target']}" + (":ro" if m.get("ro") else "")]
    env = dict(os.environ)
    for k, v in job["env"].items():
        cmd += ["-e", k]
        env[k] = v
    cmd += [os.environ.get("FORGE_AIRLOCK_IMAGE", "forge-airlock:local")] + job["argv"]
    rc = subprocess.run(cmd, env=env, stdin=subprocess.DEVNULL).returncode
    with open(log, "a") as f:
        f.write(f"FORGE-VM-RC {job['nonce']} {rc}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
