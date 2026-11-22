# Plan

Goal: a team approves short specs of its critical rules once; after that, code changes (by people or
agents) merge only if the rules are still proved. Later: guarantees about the combined state of a
whole domain, not one function.

Labels used here: PROVED = Dafny proved a statement about the model. NATIVE CHECK = compiled or
hand-written code that runs but is not proved. FINITE TEST = a finite set of cases passed.
Nothing below claims an end-to-end guarantee.

| Phase | What | State |
| --- | --- | --- |
| 0 | Timed trial: 3 to 5 people take one real rule through `/forge add`; record minutes and questions asked. First productivity number. | NOT STARTED (needs people) |
| 1 | One standalone checker (`cli/forge_check.py`) used by the mod and by CI; a GitHub Action; `APPROVAL_CHANGED` on pull requests that touch a spec or an approval | BUILT (v0.2.0); see README |
| 2a | Pinned Dafny downloaded on first run (no manual install) | NOT STARTED |
| 2b | `/forge build`: compile the proved code to Python first; boundary fuzz of the compiled code; Python against JavaScript comparison (catches one-backend miscompiles) | NOT STARTED |
| 2c | `/forge add` as a conversation: fail-closed defaults to confirm, then concrete A/B questions with numbers; answers saved as examples next to the spec | NOT STARTED |
| 3 | `/forge challenge`: a blind second implementation; disagreements become questions only when the spec allows both answers | NOT STARTED |
| 4 | Whole-state: one `step(State, Event)` core per domain; invariants over the combined state; random event-sequence fuzzing with minimal counterexamples (FINITE TEST); Dafny proofs of the invariants where it pays (PROVED) | NOT STARTED |
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
