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

## The airlock (`airlock/`, `/forge:airlock`)

- Container tier: the box shares the host kernel (on macOS, Docker Desktop's Linux VM). A kernel or
  container-runtime escape defeats it. The airlock container has `CAP_SYS_ADMIN` and no AppArmor profile (for
  the `nosymfollow` mount); the agent container has no capability.
- `--pass-env NAME` exposes that value to the agent, which can send it to any host in `agent.egress_allow`.
  Use a separate, spend-capped key. An allowed host can always receive data from the agent.
- The startup canary is a finite test: a probe that passes shows only that those attempts failed.
- Only the verifier's verdict on one gate log is proved (about the Dafny model); everything around it,
  including the memo that makes it fast, is hand-written and tested, not proved.
- Report a way to read a hidden path, reach an upstream key, pass a cap or change a hidden or read-only path
  through the sync-back the same way as a soundness hole (below).

## Reporting a problem

Report a way to get `VERIFIED` or `BUILT` for something that should not pass (a soundness hole) through
GitHub's private vulnerability reporting on this repository ("Security" tab, "Report a vulnerability").
Include the smallest project that shows it. Soundness holes found so far are listed in `CHANGELOG.md`
under "Fixed (soundness)".
