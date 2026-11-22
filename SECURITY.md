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

## Reporting a problem

Report a way to get `VERIFIED` or `BUILT` for something that should not pass (a soundness hole) through
GitHub's private vulnerability reporting on this repository ("Security" tab, "Report a vulnerability").
Include the smallest project that shows it. Soundness holes found so far are listed in `CHANGELOG.md`
under "Fixed (soundness)".
