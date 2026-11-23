# The Forge MCP airlock (`/forge:airlock`, `forge_airlock.py up -- claude`)

Run `claude` (or any command) in a box. In the box, hidden code is absent (also from git
history), read-only paths cannot change, the upstream MCP servers' keys are not present, and every
MCP tool is default deny. Gated tools (money, deploys, counts) are capped by the compiled escrow
verifier of #91, whose model is proved in Dafny. When the program exits, you see the diff, and
your repository changes only after you type `y`.

`end_to_end=false`. The verdict of the verifier on one log is the only PROVED part (about the
Dafny model). Everything else is a NATIVE CHECK or a FINITE TEST (see "Honest labels").

## 60-second quickstart

You need Docker with Compose v2, Python 3.8+ and git, and the forge plugin (see the top-level
README). Only `up`, `tools` and `check` need Docker; every other forge command works without it.

Then, in your repository, in Claude Code:

```
/forge:airlock
```

Claude reads the repository and `.mcp.json`, proposes hidden paths, read-only paths and a rule
for each MCP tool, asks you a few questions, writes `forge.yaml` (secrets only as `${NAME}`),
and writes `./forge-airlock`, a small wrapper that calls the installed `forge_airlock.py` by
absolute path (`airlock/forge_airlock.py` inside the installed plugin). In a terminal (not inside Claude Code: the program in the box is interactive):

```
./forge-airlock up -- claude
```

The first run builds two images (a few minutes); later runs start in about 10 to 20 seconds.
Without Claude Code, from a checkout: `airlock/forge init`, then
`./forge-airlock up -- claude` (or `airlock/forge up -- <command>`).

`./forge-airlock` holds only a path, no secret: commit it or add it to `.gitignore`. If the
plugin moves (an update), it says so; `forge_airlock.py wrapper --force` writes it again.
`init` and `wrapper` never replace a different `./forge-airlock` without a yes.

## Credentials of the program in the box

`claude` in the box needs model access. Nothing from your shell is copied into the box
implicitly. Three ways:

- **`llm` in `forge.yaml` (recommended; see "Model access through the airlock").** The box holds
  NO key. The airlock adds the key from your shell and caps the spending. No `--pass-env`.
- log in inside the box (Claude Code asks on first start; the login lives in the box's
  temporary `/tmp` and is gone afterwards), or
- `./forge-airlock up --pass-env ANTHROPIC_API_KEY -- claude` (without `llm`). This prints a
  warning: **the agent CAN read that value** and can send it to any host it can reach (here only
  the hosts in `agent.egress_allow`). With `--pass-env`, do not say that the agent "holds zero
  keys": it holds that one. `--pass-env` still works for other values, with the same warning.

An upstream secret named in `forge.yaml`, or the `llm.key_env` key, cannot be passed with
`--pass-env` (refused), and if its value reaches the agent anyway, the canary reports BREACH and
nothing runs.

## Model access through the airlock (`llm`)

```yaml
llm:
  provider_url: https://api.anthropic.com   # default
  key_env: ANTHROPIC_API_KEY                # the NAME of the variable in your shell; never the value
  unit: cost                                # cost: estimated cost units (micro-USD); or tokens
  session_cap: 10000000                     # per `forge up` run (10 USD in cost units)
  daily_cap: 30000000                       # optional: per UTC day, all runs of this repository
  per_request_max: 5000000                  # one request may reserve at most this much
  allowed_models: [claude-opus-5-5, claude-sonnet-5-5, claude-haiku-5-5]
  prices:                                   # USD per million tokens; needed for unit: cost
    claude-opus-5-5: {input_per_mtok: 4, output_per_mtok: 20}
  bytes_per_input_token: 1                  # default; 1 = strict upper bound, 0 overshoot (see below)
```

- The agent container gets `ANTHROPIC_BASE_URL=http://airlock:8701` and
  `ANTHROPIC_AUTH_TOKEN=forge-airlock-no-key` (a dummy for clients that insist on a key). The
  airlock drops every credential header the agent sends, adds the real key, and forwards only
  `POST /v1/messages` (gated) and the free `llm.free_paths` (default `count_tokens`, `models`) to
  the provider, through the egress proxy. The provider is an airlock-only egress target: the proxy
  opens it only for a CONNECT with a per-run token that the agent does not have (the canary checks
  that the agent's own CONNECT is refused). Responses, also Server-Sent Events streams, go back
  chunk by chunk.
- **Reserve.** Before forwarding: amount = `max_tokens` + estimated input (request bytes /
  `bytes_per_input_token` + 256); in cost units, input at 2x the input price (the worst cache-write
  price) and output at the output price. The compiled #91 verifier checks the session log (and the
  day log) PLUS every reservation in flight PLUS this one. On ACCEPT the reservation is journaled
  PENDING (fsync) and the request is forwarded; else HTTP 400 `FORGE_LLM_CAP_REACHED` (or
  `FORGE_LLM_PER_REQUEST_MAX`, `FORGE_LLM_MODEL`) and nothing leaves the airlock.
- **Settle.** After the response, the usage the provider reported (input + cache write + cache
  read + output tokens, or their cost) is appended to the log as one step (COMMITTED), again only
  after the verifier ACCEPTs. If the usage is unknown (provider error, a cut stream, the agent
  closing the stream, a crash or a restart), the FULL reservation is appended (ABORTED: the
  reservation is kept). If the reported usage is larger than the reservation and passes the cap
  (the estimate was wrong), the log is filled to the cap exactly and the journal says OVERRUN
  with the true amount; every later request is refused.
- The logs live in the logs volume, so the session cap survives an airlock restart and the day
  cap counts across runs. A changed `daily_cap` applies from the next UTC day (the stored log
  keeps its cap; the airlock refuses to start on a mismatch) or after `forge reset-logs`.
- The estimate is an upper bound only IF the provider counts at most one input token per
  `bytes_per_input_token` request bytes, and outputs at most `max_tokens`. With 1 that holds for
  byte-level tokenizers on text; server-side tools (web search results), fallback models and
  per-search fees are NOT in the estimate or the settlement. The provider's own token count is
  trusted (it is what you are billed for).

| Part | Label |
| --- | --- |
| Verdict of the verifier on each llm log (gross never above cap, chain, seq, duplicate id, expiry), incl. log + open reservations | PROVED (about the Dafny model only) |
| The request estimate, the reading of usage (JSON and SSE), settlement, OVERRUN clamp, the reservation journal and its restart recovery | NATIVE CHECK |
| The proxy, the header filter, the egress token, the key staying out of the box | NATIVE CHECK, plus the FINITE TEST below |
| "spending stays under the cap" for real money | NOT PROVED: holds only if the provider's usage report is correct and complete |

Test: `python3 airlock/tests/run_llm_gate.py` (gate G8 of `prove_release.sh`): a FAKE provider
in Docker that speaks the Messages API (JSON and SSE with usage fields) and a FAKE key. It
checks: no key in the agent's environment, any `/proc/<pid>/environ` or `cmdline`, or any file
(by fingerprint); no route from the agent to the provider; the provider saw the real key on every
request and never the dummy; settlement equals the reported usage (JSON and SSE, with cache
fields); a provider error, a cut stream and a SIGKILL of the airlock while PENDING keep the full
reservation; 20 concurrent streams: exactly floor(room / reservation) forwarded, the rest refused,
and the provider received exactly those; the gross and the cap survive a restart; the model key
planted in the agent gives canary BREACH and the endpoint then refuses; `forge up` works without
`--pass-env`, and refuses `--pass-env` of the model key.

## What `up` does

1. Validates `forge.yaml` in the airlock image (exit 2 if invalid; every `${NAME}` it names must
   be set in your shell; only the airlock container gets those values).
2. Writes the Docker configuration into a temporary folder (you never edit Docker files).
3. Stages a private copy of the repository: the HEAD history (first parent) plus one commit with
   your uncommitted and untracked, not ignored, files.
4. The airlock rewrites that history without the hidden paths (and, with `visible_paths`,
   without everything not visible) and builds the box's workspace from the result.
5. Starts the airlock with the upstream MCP servers, and an egress proxy that opens tunnels only
   to `agent.egress_allow` (for `claude`: `api.anthropic.com:443`). The box has no other route out.
6. Runs the breach canary in the agent container (secrets, hidden content, read-only writes,
   egress, denied tools). Fail closed: on BREACH the command does not run (exit 10).
7. Runs your command interactively in the agent container (uid 1000, no capabilities, read-only
   root file system). For `claude`, it adds `--mcp-config <airlock> --strict-mcp-config`, so
   `claude` in the box sees the airlock as its only MCP server.
8. On exit, lists the changed files. Never applied: hidden paths, read-only paths, `forge.yaml`,
   `.git`, symlinks, ignored files, and files that you changed in the real repository meanwhile;
   also every case, Unicode-normalization (NFC/NFD), ignorable-code-point and trailing dot/space
   alias of them, and any name that is an alias of an existing file (for example a case-only rename):
   on a case-insensitive host (macOS) `Forge.yaml` or `.GIT/hooks/x` would else land on `forge.yaml`
   or `.git/hooks/x`. Each target is checked again right before its write.
   Flagged for review: `.mcp.json`, `.claude/`, `.github/`, `package.json` and similar files
   that change what runs on your machine. It applies the rest only after `y`.
9. Removes the containers, networks and run volumes. The gate logs stay in the volume
   `forge-airlock-logs-<repo>` (caps count across runs); `forge reset-logs` deletes them.

Other commands: `forge init` (draft `forge.yaml`, also as `--json`), `forge tools` (start each
upstream and list its real tools, so the draft can classify them), `forge check`,
`forge reset-logs`, `forge wrapper`.

## forge.yaml

The reference is in `proposals/forge-mcp-airlock-20261122/README.md` (same engine). Additions:

```yaml
upstreams:
  - name: github
    transport: stdio
    command: [npx, -y, "@modelcontextprotocol/server-github"]
    env: {GITHUB_PERSONAL_ACCESS_TOKEN: "${GITHUB_TOKEN}"}   # NAME only; value from your shell
agent:
  egress_allow: ["api.anthropic.com:443"]   # the only hosts the box can reach (CONNECT proxy)
```

`forge.yaml` is always read-only in the box and is never synced back.

## Held calls: `require_human_approval: hardware`

A tool with `require_human_approval: hardware` (in `tools.allow` or `tools.gate`) is held in the airlock until a
person signs that exact call on the host with a key only a person can use (a FIDO2 security key: touch it; see
"Signed approvals" in the top-level README for keys and the macOS notes).

```yaml
tools:
  allow:
    - {tool: push_branch, upstream: github, require_human_approval: hardware}
human_approval:
  timeout_s: 120            # default 300; a call that is not signed in time is refused
  allowed_signers:          # allowed-signers lines (OpenSSH format); forge.yaml is read-only in the box
    - 'you@example.com namespaces="forge-call-approval" sk-ssh-ed25519@openssh.com AAAA...'
```

On the host, while the box runs: `python3 <plugin>/cli/forge_sign.py call --key ~/.ssh/forge_approval_sk`. It
lists each held call (tool, step id, the exact arguments the upstream will receive, and their SHA-256), asks
`yes`, signs with `ssh-keygen -Y sign -n forge-call-approval` and hands the signature to the airlock through
`docker compose exec` (the folder is on the airlock's state volume; the agent container does not mount it).

- **Signed bytes:** canonical JSON of `{v, run, call, step, tool, args_sha256}`: `run` is fresh per airlock start,
  `call` is a fresh random id per held call, `step` is the gate's step id (gated tools) or the call id. A signature
  is good for one call only.
- **Order:** canary, policy and argument checks first; then the hold; then (gated tools) the verifier's reservation;
  then the upstream. A held call is not forwarded and spends nothing before the signature verifies.
- **Refusals:** a key not in `allowed_signers`, a signature over other arguments or another step, a replayed
  signature, another namespace or garbage: `FORGE_HUMAN_APPROVAL_REFUSED`. No signature in `timeout_s`:
  `FORGE_HUMAN_APPROVAL_TIMEOUT`. A tool that asks for it without `human_approval.allowed_signers`: the policy is
  invalid (fail closed).
- **Client timeouts:** the agent's MCP client waits too. `mcp_cli.py` waits 60 s; keep `timeout_s` below your
  client's limit.

Tests: `tests/human_approval_test.py` (in `run_tests.py`, no Docker: 16 cases through `Airlock.tools_call` with a
fake upstream) and `tests/run_human_approval_e2e.py` (Docker, `forge up`: owner-signed call forwarded, rogue-signed
call refused, unsigned call timed out, none forwarded except the first). LABEL: FINITE TEST with software ed25519
keys made at test time; a FIDO2 key uses the same `ssh-keygen -Y` path, its touch is not tested. The hold, the
folder hand-off and the host CLI are NATIVE CHECK; the signature check is OpenSSH's, not proved.

## Faster gates, same proved verifier

#97 started a new verifier subprocess for every gated call and re-verified the whole log
(about +300 ms per call). Now:

- the compiled Leg-1 verifier (Dafny-proved model) runs as ONE persistent process for the
  airlock's life (`engine/verifier_server.py`), through the unchanged glue `leg1_cli.py`;
- inside it, the compiled `Sha.Sha256` and `Ed.ValidateKey` are memoized, keyed by the exact input
  bytes. They are pure functions, so a repeated call returns what the compiled code returned for
  the same bytes. Every call still verifies EVERY record of the log; no incremental shortcut, no
  Rust leg (the unproved Rust leg of #91 is not used and not offered);
- start-up, reload and `audit.py` still use a fresh subprocess with no memo.
  `FORGE_VERIFIER=subprocess` restores the #97 path; `FORGE_VERIFIER_MEMO=0` turns the memo off.

Measured latency of one gated call, 120 calls after 5 warm-up calls (log grows to 126 records):

| Where | Before (#97: subprocess per call) median / p95 | After (persistent + memo) median / p95 |
| --- | --- | --- |
| host, `GateLog.reserve` (Python 3.10, macOS, no Docker) | 307.75 / 503.3 ms | 15.25 / 23.47 ms |
| Docker, `charge` through the airlock, agent to upstream (bench.sh) | 338.16 / 776.48 ms | 22.67 / 30.33 ms |

Persistent process without the memo: 276.3 / 446.28 ms (host): process start was not the main
cost; the compiled SHA-256 over every record was. The cost still grows with the log (about
0.1 ms per record after the memo), because every record is verified on every call.

## Tests

```
python3 airlock/tests/run_tests.py      # no Docker: policy, paths, sync filter, verifier, draft
python3 airlock/tests/run_up_e2e.py     # Docker: forge up end to end (y, n, breach)
airlock/proof/prove_airlock.sh          # Docker: the battery (5 domains) + benchmark
python3 airlock/tests/run_llm_gate.py   # Docker: the model proxy and its spending gate (fake provider)
./prove_release.sh                      # all 8 release gates of the plugin (see the top-level README)
```

`run_tests.py`: policy validation, `${NAME}` handling, path rules (hidden, visible, read-only,
symlinks, `..`), the sync-back filter, the persistent verifier (memo on, memo off and a fresh
subprocess give identical JSON on 24 logs, valid and broken, twice; restart after a kill; fail
closed on timeout), gate decisions through it, and the `forge.yaml` draft (no secret value).
`run_up_e2e.py`: a hostile script in the box; `n` leaves the repository unchanged; `y` applies
exactly the allowed files; a passed upstream secret gives canary BREACH, exit 10, nothing synced.

### The two gaps of #97, closed

- **Symlink swap (TOCTOU).** The workspace volume is mounted at `/ws-raw`; the airlock bind-mounts
  it at `/ws` with the Linux `nosymfollow` flag before anything else, and every file-serving
  upstream runs there (as uid 1000). The kernel follows no symlink under `/ws`, so a symlink
  swapped between the airlock's `realpath()` check and the upstream's `open()` cannot redirect
  the open; hidden paths do not exist on disk. Self-test at start; fail closed. Cost: symlinks
  in the workspace cannot be read through MCP tools (ELOOP); the agent's shell still follows them.
  Needs `CAP_SYS_ADMIN` and `apparmor=unconfined` for the AIRLOCK container (never the agent).
  Test: `agent/swap_attack.py`, 3000 MCP reads (5 tools) while two threads swap `src/swap.ts` and
  `src/dswap` between allowed targets, `/etc`, the airlock's source copy, its policy and hidden
  paths. A CONTROL run without the mount is recorded but not gated: in our runs it did not leak
  either (the realpath check and server-filesystem's own check held), so the mount is a second,
  kernel-level layer, not a fix for an observed leak.
- **Mid-call crash and timeout.** Domain `crash` (`proof/policies/crash.yaml`, cap 5): an
  upstream that exits in the middle of a call (FORGE_UPSTREAM_FAILED, step ABORTED and spent; the
  upstream is started again for the next call), an upstream that hangs past its 8 s timeout
  (ABORTED, spent), and SIGKILL of the airlock container while call 5 is PENDING (at restart:
  ABORTED, spent). The audit then shows exactly 5 of 5 spent (2 COMMITTED, 3 ABORTED), the 6th call
  is refused REJECT_OVER_CAP, and the total is still exactly 5 after two more restarts.

## Protocol hardening (red-team findings F1 to F8)

- **Request framing (F1).** `/mcp` and the canary endpoints need one decimal `Content-Length`
  of at most 1 MiB (missing: 411, malformed or `Transfer-Encoding`: 400, larger: 413). Every
  socket read times out after `FORGE_READ_TIMEOUT_S` (30 s); at most `FORGE_MAX_CONN` (256)
  connections are served at once, one more gets 503 and is closed.
- **Logs (F2).** Untrusted strings (tool names, paths, method names) are cut to 120 characters
  plus their length and a hash in log lines and error replies. `forge up` and the proof
  compose rotate container logs (json-file, `max-size: 10m`, `max-file: 3`).
- **Argument rules (F3).** `not_in` and `not_equals` are deny-lists: they need `type`
  (string, integer or boolean), and every listed value must have that type. For strings they
  compare NFKC-normalized, without format characters (zero-width), stripped and case-folded.
  `equals` and `in` compare exactly, type included (`1` is not `true`). Homoglyphs from other
  scripts are NOT folded: prefer `in` or `regex` (allow-lists) over deny-lists.
  `strict_args` is now ON by default: an argument is refused unless the policy names it
  (`arg_rules`, `path_args`, `value_field`, `id_field`, `allow_args: [..]`) or the upstream's
  `inputSchema.properties` lists it. Opt-out per tool: `strict_args: false`.
- **Replay ids (F4).** A gate's `id_field` must be a string; it is NFC-normalized and stripped,
  then must fully match `id_regex` (default `[A-Za-z0-9._:-]{1,128}`). The normalized id is the
  replay key and is what the upstream receives.
- **Numbers (F5).** `NaN`, `Infinity`, `-Infinity` and numbers that overflow to infinity do not
  parse (400), and nothing is serialized with `allow_nan`.
- **Upstreams (F6).** A stdio upstream that times out is killed before the step is marked
  ABORTED; the next call starts a new one. Lines on its stdout that are not a JSON-RPC response
  object are skipped (the reader does not stop).
- **Canary (F7).** `/forge/canary/plan` and `/forge/canary/report` need the header
  `x-forge-canary-token`. `forge up` makes a random token per run and gives it only to the
  airlock and to the canary process (the command runs after `unset FORGE_CANARY_TOKEN`);
  the proof scripts export one per run. Fingerprints are salted per workspace build.
- **Gate steps (F8).** Every exit after a reserved step (result, upstream error, any exception)
  writes COMMITTED or ABORTED; nothing stays PENDING until a restart.

Tests: `tests/redteam_regress.py` (part of `run_tests.py`; `FORGE_ENGINE_DIR=<old engine>` runs it
against other code) and `agent/protocol_regress.py` in `validate.sh` (domain concurrency).

## Honest labels

| Part | Label |
| --- | --- |
| Verdict of the compiled #91 verifier on one log (cap, chain, seq, duplicate id, expiry) | PROVED (about the Dafny model only) |
| The compiled verifier as it runs, `leg1_cli.py`, `verifier_server.py` and its memo table | NATIVE CHECK |
| "memo on gives the same verdict as memo off" | FINITE TEST (24 logs x 2) plus the purity argument above |
| Everything else in `engine/`, `forge_airlock.py`, the canary, the egress proxy, the sync filter, the draft heuristics | NATIVE CHECK |
| Every run and number in this README | FINITE TEST (one machine) |
| Isolation | container tier: the box shares the host kernel (on macOS, Docker Desktop's Linux VM) |
| Hosts in `agent.egress_allow` | reachable: the box can send data there |
| `--pass-env NAME` | the agent can read that value |
| `llm`: spending caps | the verifier's verdict on the log is PROVED; estimate, settlement and proxy are NATIVE CHECK |
| `require_human_approval: hardware` | NATIVE CHECK (hold, hand-off, host CLI); signature check by OpenSSH `ssh-keygen -Y verify`; FINITE TEST with software keys |

## Not tested

- The `llm` model proxy with real Claude Code work in the box. The gate test uses a fake provider.
  Against the real API, ONE smoke request only (`run_llm_gate.py --real-smoke`, 2026-11-23,
  `claude-opus-5-5`, `max_tokens` 20, cap 3000 tokens, through the egress proxy with TLS): HTTP 200,
  usage 13 input + 6 output, 19 tokens settled = the reported usage, no key variable in the box.
  Not tested beyond that: streaming against the real API, Claude Code's own request mix (its models,
  `count_tokens`, retries) and TLS under load, provider-side features that bill outside
  the usage fields (server tools, fallbacks), and a model price change.
- `claude` doing real work in the box with a model key. Tested: Claude Code
  2.1.330 in the box reports the airlock as "✔ Connected" (`claude mcp list`), and the proxy
  tunnels to `api.anthropic.com:443` and refuses `github.com:443` (403).
- Interactive TTY on Linux and Windows hosts (tested on macOS through a pseudo-terminal only);
  Windows is not supported (no `pty`, POSIX paths).
- Real upstream servers other than server-filesystem 2.2.0 and the mocks (for example
  `npx -y @modelcontextprotocol/server-github`), real credentials, `forge tools` against them.
- Large repositories (thousands of commits), merges (first parent only), submodules (dropped),
  LFS, binary hidden files; a cold Docker cache on a new machine.
- The `/forge:airlock` conversation itself (it is a prompt to Claude; its quality is not
  measured) and the marketplace install by an invited user on another machine.
- Crash between the log append and the PENDING journal line (ORPHAN_SPENT path); a kill of the
  host or of Docker itself; `nosymfollow` on hosts with AppArmor or SELinux enforcing (Linux hosts
  were not tested; only Docker Desktop's VM).
- Everything else listed under "Not tested" in the #97 README still applies, except the symlink
  swap and the mid-call crash, which are now tested (FINITE TEST).
