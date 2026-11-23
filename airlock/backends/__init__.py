"""Isolation backends of the agent container (`forge up --isolation`, forge.yaml `isolation:`).

  docker   the default: runc. The agent shares the kernel of the Docker host (on macOS, Docker
           Desktop's Linux VM) with the airlock. A kernel escape defeats the box.
  gvisor   the agent container runs under gVisor (`runtime: runsc`): its system calls go to the
           gVisor user-space kernel (the Sentry), not to the host kernel. Needs a Linux Docker host
           with runsc registered as a Docker runtime.
  microvm  the agent container runs in its own lightweight VM with its own guest kernel, through
           the Kata Containers runtime (QEMU / Cloud Hypervisor / Firecracker behind it). Needs a
           Linux Docker host with /dev/kvm and a Kata runtime registered with Docker.

Only the AGENT container changes. The airlock, the egress proxy and the exporter stay on runc:
they are the trusted side and the airlock needs its nosymfollow bind mount.

Fail closed, twice:
  1. before start: the chosen backend's runtime must be registered with the Docker daemon
     (`docker info`); else `up` refuses (exit 2). There is NO silent fall back to docker.
  2. at start, in the agent container itself, before the canary: the kernel the agent sees
     (/proc/version) must differ from the kernel the airlock sees, and for gvisor the agent's
     kernel log must name gVisor. Else the command does not run (exit 87 in the box, 10 for `up`).

Host side: Python standard library only. Label: NATIVE CHECK (the probe is a finite test,
not a proof that the runtime has no escape)."""
import json
import os
import subprocess

NAMES = ("docker", "gvisor", "microvm")
DEFAULT = "docker"
PROBE_EXIT = 87  # the agent's isolation probe failed (the command did not run)

# Runtime names looked up in `docker info` (first match wins). FORGE_AIRLOCK_<NAME>_RUNTIME overrides.
RUNTIMES = {
    "docker": (),
    "gvisor": ("runsc",),
    "microvm": ("kata", "kata-qemu", "kata-clh", "kata-fc", "io.containerd.kata.v2"),
}

HINT = {
    "gvisor": "install gVisor on a Linux Docker host and register it: `runsc install -- --reproduce-nat && systemctl restart docker` "
              "(--reproduce-nat lets Docker's embedded DNS answer in the sandbox) "
              "(https://gvisor.dev/docs/user_guide/install/). Docker Desktop on macOS or Windows has no runsc.",
    "microvm": "needs a Linux Docker host with /dev/kvm and Kata Containers registered as a Docker runtime named "
               "`kata` (https://github.com/kata-containers/kata-containers). Docker Desktop on macOS has no Kata "
               "runtime; see the section Isolation backends in airlock/README.md for the macOS status.",
}


class BackendError(Exception):
    pass


def resolve(cli_value, policy_value):
    """The backend name. forge.yaml wins over nothing; a --isolation that differs from a
    forge.yaml `isolation:` is refused (the policy owner chose it; the flag cannot weaken it)."""
    for v, where in ((cli_value, "--isolation"), (policy_value, "forge.yaml isolation")):
        if v is not None and v not in NAMES:
            raise BackendError(f"{where}: unknown backend {v!r} (one of {', '.join(NAMES)})")
    if cli_value and policy_value and cli_value != policy_value:
        raise BackendError(f"--isolation {cli_value} differs from forge.yaml isolation: {policy_value} "
                           "(remove the flag or change forge.yaml)")
    return cli_value or policy_value or DEFAULT


def docker_runtimes():
    """Runtime names registered with the Docker daemon, or None if docker info fails."""
    r = subprocess.run(["docker", "info", "--format", "{{json .Runtimes}}"], capture_output=True, text=True)
    if r.returncode != 0:
        return None
    try:
        return set(json.loads(r.stdout.strip() or "{}"))
    except ValueError:
        return None


def runtime_for(name, registered):
    """The Docker runtime to put on the agent service (None = Docker's default, runc).
    Raises BackendError when the backend is not available: fail closed, no fall back."""
    if name == "docker":
        return None
    env = os.environ.get(f"FORGE_AIRLOCK_{name.upper()}_RUNTIME")
    wanted = (env,) if env else RUNTIMES[name]
    if registered is None:
        raise BackendError(f"isolation {name}: `docker info` failed; cannot check the runtime (fail closed)")
    for rt in wanted:
        if rt in registered:
            return rt
    raise BackendError(f"isolation {name} is not available on this Docker host: no runtime "
                       f"{' or '.join(wanted)} in `docker info` (registered: {', '.join(sorted(registered)) or 'none'}). "
                       f"{HINT[name]} Refusing to start (no fall back to docker).")


def apply(doc, runtime, services=("agent",)):
    """Put the runtime on the agent service(s) of a compose document. Returns doc."""
    if runtime:
        for s in services:
            if s in doc["services"]:
                doc["services"][s]["runtime"] = runtime
    return doc


def agent_hosts(project, services, network="agentnet"):
    """["airlock:<ip>", ...] for the started services on the agent network of a compose project,
    or None if one cannot be read. Used for gvisor and microvm: their agent cannot use Docker's
    embedded DNS (127.0.0.11 is a NAT rule in the container's network namespace, which gVisor's
    netstack and a Kata guest kernel do not see), so the names go into the agent's /etc/hosts."""
    hosts = []
    for svc in services:
        r = subprocess.run(["docker", "compose", "-p", project, "ps", "-q", svc], capture_output=True, text=True)
        cid = r.stdout.strip().splitlines()[:1]
        if r.returncode or not cid:
            return None
        r = subprocess.run(["docker", "inspect", "-f", "{{json .NetworkSettings.Networks}}", cid[0]],
                           capture_output=True, text=True)
        try:
            nets = json.loads(r.stdout)
        except ValueError:
            return None
        ip = next((v.get("IPAddress") for k, v in nets.items() if k == f"{project}_{network}"), None)
        if not ip:
            return None
        hosts.append(f"{svc}:{ip}")
    return hosts


def probe_sh(name):
    """Shell text run in the agent container BEFORE the canary. $FORGE_HOST_KERNEL holds the
    airlock's /proc/version (runc: the Docker host kernel). Exit PROBE_EXIT on a mismatch."""
    if name == "docker":
        return ""
    fail = f'echo "forge-box: isolation {name} NOT in effect: $1" >&2; exit {PROBE_EXIT}'
    sh = (f'_iso_fail() {{ {fail}; }}; _k=$(cat /proc/version 2>/dev/null); '
          '[ -n "$FORGE_HOST_KERNEL" ] || _iso_fail "no host kernel string to compare"; '
          '[ -n "$_k" ] || _iso_fail "no /proc/version"; '
          '[ "$_k" != "$FORGE_HOST_KERNEL" ] || _iso_fail "the agent kernel is the host kernel"; ')
    if name == "gvisor":
        sh += 'dmesg 2>/dev/null | grep -q gVisor || _iso_fail "the kernel log does not name gVisor"; '
    sh += f'echo "forge-box: isolation {name}: agent kernel differs from the host kernel" >&2; '
    return sh
