# Security

## What this tool protects, and what it does not

- **Protected by the checker, when it runs in CI on the exact commit with branch protection:**
  a merge cannot change an approved spec, `forge.json` or `approved.json` without a visible
  `APPROVAL_CHANGED`, and listed code cannot merge unless Dafny proves it against the approved spec.
- **Not a security boundary: the mod inside Claude Code.** Its guard covers Claude's Edit and Write
  tools only. An agent with a shell runs as your user: it can edit any file, replace the `dafny`
  binary, or replace the checker. Treat local verdicts as feedback. Trust the CI run.
- **Not covered at all:** code that no check lists; whether the spec is the right one; Dafny's
  compilers (emitted code is a NATIVE CHECK); your wrapper around the emitted code; the `usedBy` text
  check (it is not a call-graph analysis); the unsound-construct scan (text, not a parser).
- A pull request can change the workflow file. Protect `.github/` and `.forge/` with CODEOWNERS, and pin
  the Action to a commit hash, not a tag: tags can be moved.

## Signed approvals (optional; `require_signed_approval`)

- With signed approvals, `check` gives `VERIFIED` only for an `approved.json` that a key in the trust anchor signed
  (`ssh-keygen -Y verify`, namespace `forge-approval`), and only while every covered file (forge.json, specs,
  answers, decision records, `.forge/allowed_signers`, `signed_files`) matches its signed hash. Else
  `APPROVAL_UNSIGNED`, exit 2.
- The trust anchor is never the working tree: `--allowed-signers FILE` / `FORGE_ALLOWED_SIGNERS` (outside the
  project) or `.forge/allowed_signers` at the `--base` ref. Trusted: that file or the base branch's protection,
  OpenSSH's `ssh-keygen`, and the owner's key. Only `sk-` (FIDO2) keys show a touch; restrict with
  `signer_key_types`. With no anchor and the requirement switched off in the working tree, nothing is enforced.
- A signature has no date: an old signed approval of old files verifies again (rollback). On a pull request,
  `APPROVAL_CHANGED` still shows it.
- The airlock's `require_human_approval: hardware` holds a call until a signature over that exact call (run, call
  id, step id, tool, argument hash) verifies against `human_approval.allowed_signers` in `forge.yaml`.

## The airlock (`airlock/`, `/forge:airlock`)

- Container tier: the box shares the host kernel (on macOS, Docker Desktop's Linux VM). A kernel or
  container-runtime escape defeats it. The airlock container has `CAP_SYS_ADMIN` and no AppArmor profile (for
  the `nosymfollow` mount); the agent container has no capability.
- `--isolation gvisor` (runsc) or `--isolation microvm` (a Kata VM) moves ONLY the agent container off the
  shared kernel, on a Linux Docker host that has that runtime; `up` refuses when it is missing and checks
  in the box that the agent's kernel is not the host kernel. The airlock stays on runc. gVisor and Kata are
  third-party code: an escape from them is out of scope, as is a hypervisor escape.
- `--pass-env NAME` exposes that value to the agent, which can send it to any host in `agent.egress_allow`.
  Use a separate, spend-capped key. An allowed host can always receive data from the agent.
- With `llm` in `forge.yaml`, the model key stays in the airlock: the box gets only a dummy value and reaches
  the model through the airlock, which adds the key. Only the verifier's verdict on the spending log is proved;
  the token estimate, the reading of the provider's usage report and the proxy are hand-written (NATIVE CHECK).
  The cap holds for real money only if the provider's usage report is correct and complete (server-side tool
  fees and fallback models are not counted). The agent can still send data to the model provider in its requests.
- The startup canary is a finite test: a probe that passes shows only that those attempts failed.
- Only the verifier's verdict on one gate log is proved (about the Dafny model); everything around it,
  including the memo that makes it fast, is hand-written and tested, not proved.
- Report a way to read a hidden path, reach an upstream key, pass a cap or change a hidden or read-only path
  through the sync-back the same way as a soundness hole (below).

## Reporting a problem

Report a way to get `VERIFIED` or `BUILT` for something that should not pass (a soundness hole), or `VERIFIED` without
a valid signature when signed approvals are required, through
GitHub's private vulnerability reporting on this repository ("Security" tab, "Report a vulnerability").
Include the smallest project that shows it. Soundness holes found so far are listed in `CHANGELOG.md`
under "Fixed (soundness)".
