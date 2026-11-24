# Airlock: an agent box for your repository

Optional, and not part of the day-1 loop: `/forge airlock` (needs Docker). The full design, threat model and
test list are in [`airlock/README.md`](../airlock/README.md).

[Back to the README](../README.md)

# Airlock: an agent box for your repository

`python3 airlock/forge_airlock.py up -- <command>` (or `./forge-airlock up -- claude` after `/forge:airlock`)
reads `forge.yaml`, generates the Docker configuration in a temporary folder, stages your repository, builds the
box's workspace with the hidden paths absent (also from git history), starts the airlock with your MCP servers
(they hold the keys; secrets are named in `forge.yaml` as `${NAME}` only), runs a breach canary in the box (fail
closed), and runs the command interactively. MCP tools are default deny; tools that spend money or act on the
world can be gated with a cap, decided by the compiled escrow verifier of Forge's #91 (its model is PROVED in
Dafny), kept running as one process. The box reaches only the airlock and the hosts in `agent.egress_allow`.
With `llm` in `forge.yaml`, `claude` in the box needs NO key: it reaches the model through the airlock, which adds
the key from your shell and caps the spending per session and per day (no `--pass-env`; `airlock/README.md`).

Honest labels:

| Part | Label |
| --- | --- |
| The verifier's verdict on one gate log: cap, money chain, sequence, duplicate id, expiry (#91) | PROVED, about the Dafny model only |
| The compiled verifier as it runs, its CLI glue, the persistent process | NATIVE CHECK (compiled or hand-written, not proved) |
| The memo of its pure SHA-256 and key validation (the speed-up: about 300 ms to about 20 ms per gated call) | NATIVE CHECK; "same verdict with and without it" is a FINITE TEST, not proved |
| Policy engine, path checks, workspace filter, `nosymfollow` mount, canary, egress proxy, sync-back filter | NATIVE CHECK |
| Every run of `prove_release.sh` | FINITE TEST (one machine, listed inputs) |
| Isolation | container tier: the box shares the host kernel (on macOS, Docker Desktop's Linux VM); a kernel or runtime escape is out of scope |
| `llm` model proxy: verifier's verdict on the spending log | PROVED (Dafny model); the token estimate, the reading of the provider's usage report and the proxy are NATIVE CHECK |
| `--pass-env NAME` | the agent CAN READ that value (and can send it to the allowed hosts); use a separate, spend-capped key |
| End to end | `end_to_end=false`: tested with mocks, fake keys and the real server-filesystem, not with a real paid service |

`./prove_release.sh` runs all 8 release gates (checker tests, plugin validation and scans; hidden paths incl. a
concurrent symlink swap; gated MCP incl. crash, timeout and restart; planted breaches; diff sync-back; gate
latency; the README and its docs pages; the `llm` model proxy against a fake provider) and exits 0 only if all pass. Not tested: see "Not tested" in `airlock/README.md`
(no real model session in the box, no Linux or Windows host, no real paid upstream).

## More in `airlock/README.md`

Each of these is optional and has its own section, honest labels and test list in
[`airlock/README.md`](../airlock/README.md):

- Streamable HTTP MCP servers, several upstreams in one box, and roles that only narrow what a box may see and call.
- Held calls that wait for a person (`require_human_approval: hardware`).
- Isolation backends (`--isolation`): `docker` (the container tier: shares the host kernel), `gvisor` and `microvm`
  (Linux: a Kata VM per agent container; CI runs gvisor and Kata). On macOS, `microvm` runs the agent in a vfkit VM
  (Apple Virtualization.framework, its own guest kernel; `brew install vfkit`, no sudo): the battery passes 37/37 on
  an Apple-silicon Mac (FINITE TEST, run by hand: GitHub's macOS runners have no nested virtualization, so CI builds
  the image and runs the unit cases only). NOT built: Claude Code inside the VM (the image has no Node), and
  `--agent-image` with vfkit.
- Protocol hardening and the in-box red team (probes run inside the box); every finding has a regression test.
