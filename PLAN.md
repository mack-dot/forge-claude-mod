# Plan

Goal: a team approves short specs of its critical rules once; after that, code changes (by people or
agents) merge only if the rules are still proved. Later: guarantees about the combined state of a
whole domain, not one function.

Labels used here: PROVED = Dafny proved a statement about the model. NATIVE CHECK = compiled or
hand-written code that runs but is not proved. FINITE TEST = a finite set of cases passed.
Nothing below claims an end-to-end guarantee.

| Phase | What | State |
| --- | --- | --- |
| 0 | Timed trial: 3 to 5 people take one real rule through `/forge:add`; record minutes and questions asked. First productivity number. | NOT STARTED (needs people) |
| 1 | One standalone checker (`cli/forge_check.py`) used by the mod and by CI; a GitHub Action; `APPROVAL_CHANGED` on pull requests that touch a spec or an approval; included files are verified and scanned | BUILT (v0.2.0, v0.3.0); see README |
| 2a | Pinned Dafny downloaded on first run (no manual install) | BUILT (v0.5.0): SHA-256 pinned per platform; Windows untested |
| 2b | `/forge build`: compile the proved code to py, js, go, java, cs, rs; a Dafny fuzz harness run on the compiled code in every target with identical output required; `STALE_BUILD` and `NOT_WIRED` checks | BUILT (v0.3.0). The harness is written per check (by Claude or by hand), not generated. `usedBy` is a text check, not a call-graph check |
| 2c | `/forge:add` as a conversation: fail-closed defaults to confirm, then concrete A/B questions with numbers; answers saved as examples next to the spec | BUILT (v0.4.0; working inside Claude Code only since v0.6.0). Answers are Dafny-checked against the spec (`EXAMPLES_FAILED`). The conversation and the gap probes are a prompt to Claude, not code: their quality is not measured |
| 2d | Constant differential fuzzing, queued AFTER chronicle and the first outside trial: run the person's ACTUAL host-language code (for example `keyword.ts` and its wrapper) and the compiled proved rule side by side on generated inputs, in one process, with a property-testing library (fast-check, Hypothesis) that shrinks failures. Requirements: (1) fuzz through the real, unproved wrapper and app function, not only inside a Dafny harness; (2) host-language odd inputs (undefined, empty strings, duplicates, odd Unicode); (3) only edits to covered files trigger it, and compile only when a `.dfy` file changes, so a run takes milliseconds, not seconds; (4) every disagreement saved as a permanent test; (5) result in the status line. A disagreement is either a bug in the app code or a gap between the spec and what the person meant | BUILT, released in v0.8.0 (before its first real-project test); first real target: chronicle |
| 2e | Fuzz evaluators for more languages, on the same line protocol (no change to the core): **v0.9** Go and Rust (evaluator templates; the compiled evaluator cached under `.forge/cache/` and rebuilt only when the app file or the Dafny files change; one CI case per language with a planted bug that must be caught and shrunk); **v1.0** Java/Kotlin and C#; then any other language (Swift, Ruby, PHP, C/C++, Elixir) by splitting the evaluator in two: a reference evaluator (the proved rule compiled to Python or Go) and a code evaluator (a small wrapper in the app's language), compared by the core. Speed target: 1,500 inputs within the 1-second turn budget (expected 100–300 ms per language, to be measured) | Go and Rust BUILT (v0.9.0); Java and C# BUILT (v0.10.0, C# tested in CI only); Kotlin and the split evaluator for other languages NOT STARTED |
| 2f | `INVARIANTS.md`: every approved rule in plain words in one generated file, kept in step by CI | BUILT (v0.11.0) |
| 2g | Bypass gate: code outside the rule that reads its watched fields (`bypass`, `/forge:bypass`, `"outside"` and `UNAPPROVED_BYPASS` in `check`); allowed places approved in forge.json, kept by line hash | BUILT (Unreleased). A text search, not data flow: misses aliases, dynamic access, other names |
| 3 | `/forge challenge`: a blind second implementation; disagreements become questions only when the spec allows both answers | NOT STARTED |
| 4 | Whole-state (big software plans: many rules, one state core): one `step(State, Event)` core per domain; invariants over the combined state; random event-sequence fuzzing with minimal counterexamples (FINITE TEST); Dafny proofs of the invariants where it pays (PROVED) | NOT STARTED |
| 5 | Research: certificate checkers for unproved solvers; crash-safe protocol models; a sidecar that runs any compiled rule and holds the credentials | NOT STARTED |

## Known limits (do not round up)

- The mod's guard covers Claude's Edit and Write tools only. CI with branch protection is the lock,
  and only if nobody can bypass it and a person reviews every `APPROVAL_CHANGED`.
- A pull request can also change the workflow file itself. Protect `.github/` and `.forge/` with
  CODEOWNERS.
- A proof says the listed files meet the approved spec. It does not say the spec is the right one,
  that the compiler is correct, or that the application calls the proved code.
- The unsound-construct check is a text scan, not a parser.
- No productivity claim is measured yet (Phase 0).
- The checker runs as the same user as the agent: an agent with a shell can replace the `dafny`
  binary or the checker itself. The trusted run is CI on the exact commit.
- Emitted code is a NATIVE CHECK. Identical output from several targets does not exclude a bug that
  every backend shares.
- Open design (not built): running the agent in a container with specs read-only, a stop hook that
  loops on a failed check up to a fuse, and notifications only on done / approval needed / fuse.
