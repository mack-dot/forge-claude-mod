# Changelog

Each entry says what was tested and how. "Not tested" items are listed, not hidden.

## 0.7.0 — 2026-11-22
- Added: `mutate` (CLI) and `/forge mutate`. Plants small bugs (14 kinds: comparison, arithmetic, logic,
  constants) in the implementation files, one at a time in a temporary copy, and counts how many the proof
  catches. A survivor is a change the spec allows. On the example: 6 of 6 caught; with clause W4 removed from
  the spec, 1 survives (`0 < amount` to `0 <= amount`), which is exactly the gap the removal opens.
- Added: success targets to `TRIAL.md` (set before a trial, reported with every target missed), and rows for
  mutation results, real bugs found and false alarms.
- Fixed: the README linked to the private research repository.
- Tested: 27 cases (2 new).

## 0.6.1 — 2026-11-22
- Changed: typing `/forge scan` (with a space, as earlier versions said) now puts `/forge:scan` into the prompt
  box, so the person presses Enter. The same for `add`, `explain`, `fix`, `doctor`, `build-setup`.
- Not tested: the prompt-box fill in an interactive session (headless sessions have no prompt box); if the
  fill fails, the message tells the person to type the command.

## 0.6.0 — 2026-11-22
- Fixed: every step where Claude was meant to talk with the person never started. The mod handed its
  instructions to Claude with `$.prompt.submit` from inside the `/forge` command, and Claude Code refuses
  that there ("it would wait on the turn this hook is holding"). The refusal was silent because the call's
  result was not read. Found when the first real user typed `/forge scan` and nothing happened.
  Affected since 0.1.0: the guided steps of `/forge`, `/forge add`, `/forge scan`, and the explanation
  after `/forge approve`.
- Changed: those steps are now Markdown commands that Claude Code runs as a normal Claude turn:
  `/forge:scan`, `/forge:add <rule>`, `/forge:explain`, `/forge:fix`, `/forge:build-setup`, `/forge:doctor`.
  `/forge` itself keeps the deterministic parts (next step, check, approve, build, spec, status) and names
  the `/forge:` command to type next.
- Added: static tests that fail if `register.ts` calls `$.prompt.submit` or a command has no description.
- Tested: headless Claude Code sessions with the plugin loaded: `/forge` (step text), `/forge:scan` on a small
  project (a ranked table; it found two real input gaps; no file changed), `/forge:explain` on the example
  (all clauses and all 4 decisions covered). 25 cases.
- Not tested: `/forge:add` (it asks questions, which a headless session cannot answer), `/forge:fix`,
  `/forge:build-setup`, `/forge:doctor`.

## 0.5.1 — 2026-11-22
- Added: `/forge scan`. Claude surveys the codebase and ranks up to 7 rules worth proving (file, rule,
  cost of failure, provable or not, effort) and names what should not be proved. It changes no file. It is a
  prompt to Claude: its ranking is a suggestion, not measured.

## 0.5.0 — 2026-11-22
- Added: no manual Dafny install. If Dafny is not found, the checker downloads the official Dafny 4.11.0
  release for the machine (macOS arm64/x64, Linux x64, Windows x64) into `~/.cache/forge-check`, checks
  the SHA-256 that GitHub publishes for that file, and refuses to install on a mismatch. `setup` command;
  `FORGE_NO_DOWNLOAD=1` forbids the download. `/forge` step 1 runs it.
- Tested: with no Dafny on the machine, `/forge` in a headless Claude Code session downloaded Dafny and
  proved the example (16 s); a wrong pinned hash is refused and nothing is installed; macOS's own
  `/usr/bin/python3` (3.9) runs the checker. CI job `auto-dafny` does the same on fresh Linux and macOS.
- Not tested: Windows; a machine behind a proxy.

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
