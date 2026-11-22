# Changelog

Each entry says what was tested and how. "Not tested" items are listed, not hidden.

## 0.4.1 — 2026-11-22
- Fixed: `/forge check` and `/forge build` did nothing inside Claude Code since 0.2.0. Their timeouts
  (15 and 30 minutes) were over the host limit of 10 minutes, so the hook was refused. Found by the first
  run of the installed plugin in a real Claude Code session. A static test now fails on any timeout over
  the limit.
- Added: a prerequisites table in the README.
- Tested: install from GitHub into a clean config (`claude plugin install`, exit 0); `/forge check`,
  `/forge build check`, `/forge status` in a headless Claude Code session with the plugin loaded.
- Not tested: `/forge approve` then `/forge approve yes` typed by a person in one interactive session; the
  rule conversation.

## 0.4.0 — 2026-11-22
- Changed: `/forge` step 2 and `/forge add` are a conversation that starts from the person's own words
  (before: Claude picked a function from the code and wrote the rule itself). Defaults to confirm, then
  concrete A/B questions in rounds, then Dafny probes for cases that nobody asked about.
- Added: a check can list `examples` (the person's answers as asserts against the abstract spec) and
  `decisions` (a plain-text record). Approval covers both. New verdict `EXAMPLES_FAILED` when the spec
  contradicts an answer or leaves it open.
- Changed: the mod's `/forge approve` goes through the checker (`spec --json`, then
  `approve --hashes`), so the mod and CI agree on what an approval covers.
- Tested: 25 cases (7 new); `tsc` exit 0.
- Not tested: the conversation itself with a person (it is a prompt to Claude, not code); Phase 0.

## 0.3.1 — 2026-11-22
- Fixed: two TypeScript type errors in the mod, found by the first type-check (`tsc`, exit 0 after the fix).
- Added: `CHANGELOG.md`, `SECURITY.md`, `CONTRIBUTING.md`, `TRIAL.md`.
- Not tested: `/forge check` and `/forge build` typed inside Claude Code by a person.

## 0.3.0 — 2026-11-22
- Added: `build`. Compiles the proved code to py, js, go, java, cs, rs; runs a Dafny fuzz harness on the
  compiled code in every target and requires identical output; `STALE_BUILD` and `NOT_WIRED` checks;
  `/forge build`; `build: "true"` in the Action.
- Fixed (soundness): proofs in included files were not run (Dafny skips them without
  `--verify-included-files`), and included files were not scanned for `assume` and similar. A false lemma in
  an included file passed. Both are closed; two tests cover them.
- Tested: 18 cases locally and in CI; all six targets on Linux in CI (4,584 fuzz cases each).

## 0.2.0 — 2026-11-22
- Added: `cli/forge_check.py`, one checker for the mod and for CI; the GitHub Action; `APPROVAL_CHANGED`
  for pull requests that change a spec, `forge.json` or `approved.json`; `examples/wallet-cap`; `PLAN.md`.
- Tested: 11 cases locally and in CI.

## 0.1.2 — 2026-11-22
- Added: two-step approval that shows the contract (`/forge approve`, then `/forge approve yes`);
  `/forge spec`; approval covers `forge.json`; `BINDING_BROKEN`; `UNSOUND_CONSTRUCT`.
- Fixed (soundness): code could switch to refining a new, weaker spec and still pass.

## 0.1.1 — 2026-11-21
- Added: MIT license; the `dafny` path in `forge.json`.

## 0.1.0 — 2026-11-21
- First version: `/forge` guided setup, `/forge approve`, `/forge check`, status line, guard on
  Claude's Edit and Write tools.
