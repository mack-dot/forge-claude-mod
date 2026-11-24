# Changelog

Each entry says what was tested and how. "Not tested" items are listed, not hidden.

## Unreleased — recursive-rule acid test

A recursive rule (a reply chain walked through a map by id, with cycles) taken through the whole Forge flow, on the
owner's app copy and on a new synthetic fixture. Each item says what was tested and how.
- **New fixture** `examples/thread-visibility` (synthetic, written for this repository): a thread whose replies share
  the fate of every post above them (takedown, flag, other channel); the walk has a visited set, so a cycle ends it.
  The Dafny model proves termination and the rule; `tests/run_tests.py` runs it with planted bugs that must be caught:
  a cycle hang (DIFF_ERROR in seconds), a reply that ignores its parent chain (D1, D3, D4, D5 fail), a check of the
  direct parent only (D3 fails), a Dafny walk without its visited set and a Dafny model without the ancestors
  (FAILED). Negative control: the correct code gives DIFF_OK with 9 of 9 decisions.
- **Fix (soundness): an approval now covers the files the spec includes.** Before, `spec/X.dfy` could include a file
  with the rule's predicates; a change there left the spec's own hash unchanged and `check` said VERIFIED (the model
  calls the changed predicate). Now such a file is a contract file: `SPEC_CHANGED`, shown in `/forge approve` as
  "file the spec includes". Upgrade: a project whose spec includes files gets NOT_APPROVED once; approve again.
- **Fix: the build manifest and `diff`'s freshness test cover included Dafny files.** Before, a changed included
  file (for example the impl of another check that this impl includes) did not make the build stale, and `diff`
  ran the old compiled code. Upgrade: such a project is BUILD_STALE once; run build.
- **Fix: shrinking no longer uses up the fuzz budget.** All shrinking in one run (failing recorded decisions and new
  disagreements) gets at most a quarter of the time budget. Before, one failing decision was shrunk with up to 300
  evaluator calls; with a slow evaluator that was the whole 1.5 s end-of-turn budget. Measured (one machine, shared
  with other jobs, so noisy): the fixture with a 4 ms buggy candidate searched 0, 8 and 11 fresh inputs on main (one
  run took 3.1 s), 8, 77 and 73 with the fix (1.5 to 2.1 s). A test pins >= 25. On the owner's app copy (nested rows,
  ~200 messy field/kind pairs) the gain was not clear under that load: main 7 to 40 inputs, fix 5 to 30 (6 seeds).
  A cap on the messy step's share was tried and dropped: it cut messy coverage without a clear gain.
- **Fix: the next-rule hint skips an item whose subject another check states as its rule** (in its decision record,
  outside Out of scope and Notes). Before, a rule kept offering a function as its next rule after a second rule had proved that function.
- `/forge:add`: a spec may import another check's abstract spec; extra includes go in the binding prefix.
- Found, not fixed: `bypass` lists the check's own unproved wrapper (a file that imports the emitted module) as a
  hit; `mutate` copies the whole project for each mutant (2.2 GB on the owner's app copy); `check` took 35 to 49 s
  there (the proofs alone about 15 s; the rest is not profiled yet).
## Unreleased

- New (callers): a static call graph for JavaScript/TypeScript and Python (`cli/forge_callgraph.py`; standard
  library only). A check's `"callers"` names its governed decision, the wrapper that calls the proved code and the
  replaced logic; `check` gives `UNVERIFIED_CALLER` (exit 1) for a caller of the replaced logic (also through an
  import alias, a re-export or a namespace import), a same-name re-implementation, an unresolved member call by the
  decision's name, or a decision that no longer reaches its wrapper, unless it is in `"allowedCallers"`. Other
  languages: `callers not checked`. New commands `callers` (report, `--record`) and `status` (the one-line status).
- New (feeders): the same graph ranks the unproved functions that feed the decision's parameters (direct callers that
  compute an argument; functions whose return values flow into one) by leverage; `next_rule` names the top one.
- New (status line): the mod's line comes from `forge_check.py status`: green `forge ✓ model PROVED · 0 bypasses ·
  fuzz N ok · callers N ok · records current`; yellow `forge ⚠ DRIFT: …` naming a stale decision note, a wrapper
  changed since the last approval or clean diff run (`.forge/wrapper-hashes.json`, a `WRAPPER_CHANGED` warning in
  `check`), or a new unverified caller; red `forge ✗` otherwise.
- forge-ci: its own summary rows for the caller gate and for each `serviceTest` (`NOT_CALLED`), the caller part in each
  rule's status line, the top feeder, and a new Action negative control `unverified-caller`.
- Tested: `tests/callgraph_cases.py` (in `tests/run_tests.py`), 23 cases; see the pull request for what ran where.

- New (diff): **stateful trace fuzz** (`cli/forge_trace.py`) for models of the form `Step(State, Event, now)`. A
  `"trace"` block on a `js` or `py` diff entry gives seeded multi-event traces with the clock moving and injected
  retries, replays, reorders and restarts (save, then load). Model and candidates run in lockstep; verdict and state
  are compared after every step; a failing trace is shrunk by delta debugging (drop events, then simplify times and
  fields) and printed with an `again:` command. Failing traces are saved and replay first.
- New example `examples/subscription-machine` (Dafny model proved, py and js built) with a correct shipped step
  function and four planted multi-event bugs.
- Tested (`tests/run_tests.py`, 9 new cases): all four planted bugs caught and shrunk to 2, 4, 2 and 2 steps in JS and
  Python; the correct code 0 disagreements over 50,000 traces per language; the repro command finds the same trace;
  saved traces replay; negative controls (`max_len: 1` finds nothing; `restart: 0` hides the restart bug); an
  unknown `trace` key is a configuration error.
- Not tested: trace mode for the Go, Rust, Java and C# evaluators (not built); time going backwards
  (`time_backwards`) has no test case.
- **Bypass gate: decision reads only (AST) in TS/JS and Python.** A text hit of a watched field counts only where the
  field is read in a decision (if/while condition, comparison, `in`, `.includes`, ternary, switch/case, `&&`/`||`
  guard, filter/find/some/every predicate, comprehension `if`, assert, SQL `WHERE` parameter, alias). Each hit names
  its kind. TS/JS: TypeScript 5.6.3 compiler API (downloaded once, SHA-256 pinned); Python: `ast`. Other languages
  keep the text search ("text search, not AST"); a file that does not parse, or no parser on the machine, falls back
  to the text search for that file and says so. `allowedBypasses` entries stay valid by line hash. Code:
  `cli/forge_ast.py`, `cli/eval/ts_decisions.cjs`, a small hook in `run_bypass`.
  Tested: 15 new cases in `tests/run_tests.py` (a planted read in a ternary, a switch, an `.includes`/`in` and a
  filter predicate, in TS and in Python, each `BYPASS_FOUND`; the gate gives `UNAPPROVED_BYPASS` on a real proof; a
  file of only types/comments/imports gives 0 hits; parse errors and a missing parser fall back to text; Go stays
  text; old approvals stay allowed). Measured on a private app copy: 290 text lines to 76 hits (46 AST decision
  reads, all checked by hand; 30 data-file lines by text); the three known places still found.
  Not tested: Windows; Python older than 3.10 (`match` does not parse there and falls back to text); TS/JS syntax
  newer than TypeScript 5.6.
- New (diff, M1.1b): messy-input fuzz in the Go, Rust, Java and C# evaluators, as in JavaScript/TypeScript and
  Python. An adapter may name raw-value `wrappers` (or one `wrapper`) and a fail-closed answer: Go
  `json.RawMessage` (`failClosed`), Rust `serde_json::Value` (`fail_closed`), Java the evaluator's parsed value
  (`failClosed`), C# `JsonElement` (`FailClosed`). Go and Rust bind them at build time (an adapter without them still
  builds); Java and C# find them by reflection. A panic or exception is `THROWS_ON_MESSY_INPUT`. `undefined` is
  JavaScript only; Rust does not get the lone surrogate (serde_json rejects it while it parses), announced in the
  protocol hello as `"skip"` and reported as `skipped_kinds`.
- Example: three planted wrappers per language (`messygo/`, `messy_wrappers.rs`, `messyjava/`, `messycs/`, adapters
  `diff/wallet_messy.{go,rs,java,cs}`): one that crashes on a null, missing or wrong-typed amount, one that coerces
  `"5"`, and a fail-closed one (negative control).
- Tested: `tests/run_tests.py` 115 cases, 0 failed (16 new for Go, Rust, Java: messy off finds nothing; the crash and
  the coercion are found on `amount` only and shrunk to cap 0, spent 0; the fail-closed wrapper has 0 messy
  disagreements over about 1,525 messy inputs; every kind is tried and judged; saved cases replay).
  `tests/run_cs_diff.py`: the same for C#, plus the warm speed of a 1,500-input run (CI only: no .NET locally).
- Not tested: C# on a local machine; wrappers that take a typed struct (the adapter passes raw JSON).
- New: `diff --coverage` (off by default): per watched file of a py or js diff entry, the lines and branches the
  run reached, and every unreached branch by `file:line` and source line. coverage.py when importable, else a
  standard-library tracer (`cli/eval/py_cover.py`); JS/TS through Node's built-in `NODE_V8_COVERAGE` (blocks never
  run; V8 gives no branch total). Not measured: Go, Rust, Java, C#, cmd. Code: `cli/forge_coverage.py`, small hooks
  in `run_diff`/`diff_report`.
- New: `mutate --app`: mutants of the shipped code (the diff entries' watch files) in a temporary copy, one diff
  run each (fixed seed and input count); caught / survived / not run, survivors marked as on a line the fuzz
  reached or never reached; equivalent mutants are possible and said so. py and js by default, `--langs all` for
  the compiled languages. Code: `cli/forge_mutate_app.py`.
- Tested (`tests/coverage_mutate_cases.py`, 10 cases, 11 with coverage.py installed): on the example with the
  planted bugs fixed, the example's inputs reach 4/4 lines and 2/2 branches of `legacy_wallet.py` and catch every
  mutant of the cap comparison (Python and TypeScript; 19 of 21 caught overall); a deliberately weak inputs module
  (negative control) drops to 1/2 branches, names the unreached reject path in both languages, and lets the
  cap-comparison mutants survive (8 of 21). Project files unchanged after `mutate --app`. coverage.py 7.16 and the
  stdlib tracer give the same counts on the example (checked by hand on one machine; the CI runner has no
  coverage.py, so CI tests the tracer only).
- Measured (one machine): `--coverage` adds about 85 ms to a 1,500-input Python or TypeScript diff (about 195 ms
  with coverage.py); `mutate --app` costs one diff run per mutant (0.15–0.4 s py/js; 0.4–1.4 s Go/Rust/Java).
- Not tested: coverage of Go, Rust, Java, C# (not built); `mutate --app` on C# (no .NET SDK here); JS regular
  expression literals are not masked, so a mutation inside one may be planted (it then shows as caught, survived
  or not run like any other).
- **Zero-config CI** (M1.3): `examples/workflows/forge.yml` plus the Action with no inputs runs every gate the
  project's `.forge/forge.json` makes possible: `check` with `--base` taken from the event (pull_request base
  branch, merge_group base commit, the commit before a push; other events: not compared, and the summary says so),
  the bypass gate, `build --check` when a check has a build, `invariants --check`, and a diff sweep when checks
  have diff entries (`diff-inputs` 50,000, `diff-budget-seconds` 600 per entry, `diff-seed` = the run ID by
  default). Node and Go are installed only when a check uses them. New driver `cli/forge_ci.py`; every verdict
  still comes from `forge_check.py`. Outputs `result` and `report` (JSON). See `docs/ci.md`.
- Job summary: one scope-honest status line per rule, a table of every gate's verdicts, the bypass count, the fuzz
  counts and times, and the exact commands (base commit and seed) that reproduce the run.
- Fixed: `check --base` with the project in a subfolder compared a decision record's whole file instead of its
  binding part (`git show ref:path` reads from the repository top; now `ref:./path`), so a notes-only edit gave
  `APPROVAL_CHANGED`.
- **Upgrade note:** the defaults change. `build` was off and is now `auto`; `invariants` was off and is now on
  (commit `INVARIANTS.md`, or set `invariants: "false"`); the diff sweep is new and on (`diff: "false"` turns it
  off). `build: "true"` still works.
- Tested on GitHub-hosted `ubuntu-latest` (selftest run 45113316698): job `action-controls` makes a fixture
  repository from the example (Python only; only the correct wrapper fuzzed) and runs the Action with no inputs.
  Unchanged: pass, 50,000 inputs, `DIFF_OK`. Spec clause W1 removed: `APPROVAL_CHANGED` (with `base: none`:
  `SPEC_CHANGED`). A new `refund.py` comparing `spent + amount <= cap`: `UNAPPROVED_BYPASS`. A hand edit of
  `INVARIANTS.md`: `INVARIANTS_STALE`. A rare wrapper bug (exact-cap spend rejected when `cap % 250 == 27`):
  `DISAGREE` in the 50,000-input sweep (detection numbers: see docs/ci.md). A first version of that bug (cap ends
  in 777, about 0.9 hits in 10,000 inputs) was missed by the 50,000-input sweep in run 45113930328 (seed
  2164257368): a flaky control, replaced. Base
  detection per event and the subfolder fix have their own steps in `checker-tests` (the old code fails the
  subfolder step: checked on one machine).
- Measured on the runner: the 50,000-input Python sweep took 1,500–4,054 ms per job (run 45113316698) and
  2,158–2,198 ms (run 45114616292); the example's five languages, 50,000 inputs each, 1,901–2,813 ms per language
  (run 45113316698) and 3,829–5,389 ms (run 45114616292). The replaced bug in run 45114616292: 1,500 inputs found
  it in 8 of 20 seeds, 50,000 in 10 of 10.
- Not tested: macOS and Windows runners with the Action; `pull_request_target` and `merge_group` events on GitHub
  (only the detection from a stored event file); Java, Rust and .NET versions other than the runner's own.
## Unreleased — signed approvals (S1: an approval only a person with a hardware key can make)

- New (optional): `"require_signed_approval": true` in forge.json. `approve --hardware [--key KEY]` (and
  `/forge approve yes --hardware`) signs the canonical approved.json with `ssh-keygen -Y sign -n forge-approval`
  into `.forge/approved.json.sig`. approved.json then also covers `.forge/allowed_signers` and `"signed_files"`
  (for example `forge.yaml`, `INVARIANTS.md`). `check` verifies against a trust anchor outside the working tree
  (`--allowed-signers`, `FORGE_ALLOWED_SIGNERS`, or `.forge/allowed_signers` at `--base`); else verdict
  `APPROVAL_UNSIGNED`, exit 2. Optional `"signer_key_types"` (for example only `sk-` FIDO2 types). New
  `cli/forge_sign.py`.
- New (airlock): `require_human_approval: hardware` per tool and `human_approval.allowed_signers` in forge.yaml. The
  call is held until a signature over the exact call (run, call id, step id, tool, argument hash) verifies;
  `forge_sign.py call` signs on the host. The airlock image now has `openssh-client`.
- The mod's guard also blocks Claude's Edit and Write tools on `.forge/approved.json.sig` and
  `.forge/allowed_signers`.
- Tested (FINITE TEST, software ed25519 keys made at test time, no hardware): `tests/run_signed_approval.py`, 27
  cases: a rogue agent (`tests/rogue_agent.py`) that rewrites spec, forge.yaml, INVARIANTS.md, approved.json and the
  .sig, switches the requirement off, or adds its own key and re-signs gets `APPROVAL_UNSIGNED`/exit 2 in every case,
  with the anchor outside the project and at `--base`; owner-signed gives `VERIFIED`/0. Airlock: 16 unit cases and a
  Docker `forge up` end-to-end test (6 cases). Mutation controls: an accept-all verifier fails 8 signed-approval
  cases; removing the hash coverage check fails 7; an accept-all call verifier fails 6 airlock cases; skipping the
  hold fails 8.
- Not tested: a real FIDO2 key or a Secure Enclave key (needs the owner's hardware; commands in the pull request);
  the Action on a signed project (no workflow change here); PIN (`verify-required`) through the mod.
- **Airlock isolation backends** (`forge up --isolation docker|gvisor|microvm`, forge.yaml `isolation:`):
  the agent container runs on runc (default), gVisor `runsc`, or a Kata Containers VM. Fail closed: a
  backend whose runtime Docker does not have is refused (exit 2, no fall back), and a probe in the box
  checks that the agent's kernel is not the host kernel before the canary (exit 10). Tested: unit cases,
  `run_up_e2e.py` (missing backend refused; runc posing as gVisor caught), and on GitHub ubuntu-latest
  (workflow `isolation.yml`, runs 45116333410 and 45121073630) the G2-G4 battery 34/34 (docker), 35/35 (gvisor), 35/35
  (microvm) and the `up` e2e 12/12 under each. Not tested: macOS `microvm` beyond a VM boot (see the
  airlock README).

## 0.13.0 — 2026-11-23

One release with the owner's feedback from a real app and the airlock:
- **Bypass gate** (`watchedFields`, `UNAPPROVED_BYPASS`, `/forge:bypass`): code outside a proved rule that decides on its fields fails `check` until it is routed through the rule or allowed by a person.
- **Messy-input fuzz** of the unproved wrapper (`THROWS_ON_MESSY_INPUT`).
- **Scope-honest status**, `DECISION_NOTE_STALE`, notes edits without re-approval, next-rule suggestion, build syntax step over app files and their importers (`APP_SYNTAX_ERROR`), short approval digest (`--full` for every file).
- **Fast generated JS module** (no `new Function`) and `serviceTest` (`NOT_CALLED`).
- **forge-airlock** in the plugin: an agent box with hidden paths absent from disk, default-deny MCP, gates on the compiled #91 verifier, canary, egress allowlist, reviewed sync-back; red-team fixes (sync-back case/Unicode aliases; protocol F1–F8); model proxy so the box holds no API key, with spending capped by the proved verifier (strict 1 byte/token reservation, settled to reported usage). `prove_release.sh`: 8 gates, all exit 0 on three consecutive runs.
- **Upgrade note:** a committed `js` build is `STALE_BUILD` until you run `/forge build` once (the new module is part of the build). Approvals from before 0.13 stay valid; approve once more to edit notes without re-approval.

- New (`js` target): `build` also writes `<name>.mjs` and `<name>.d.mts` next to the emitted `<name>.js`: one ES
  module with real exports, a private pinned copy of bignumber.js 9.3.1 (SHA-256 checked) and helpers
  (`toDafnyString`/`fromDafnyString` with a bounded cache of frozen seqs, `toDafnyInt`/`fromDafnyInt`, seq and set
  helpers). No `new Function`, no `eval`, no file reading, no npm install in the app (owner feedback #6: a real app
  needed a `new Function` loader, bignumber.js and string conversions on every call). The runtime's string literals
  use the same cache. Code: `cli/js_wrapper.py`, two small hooks in `cli/forge_check.py`.
- New (build): the js fuzz harness also runs through its own generated module under
  `node --disallow-code-generation-from-strings` and must print the same output as the plain emitted script.
- Upgrade: run `build` once; a committed js build without the module is `STALE_BUILD`.
- `/forge:build-setup` tells Claude to import the module instead of writing a loader, with a template wrapper.
- New (build): optional `"serviceTest": {"command": [...], "cwd": "."}` per check. `build` and `build --check` run
  it with `FORGE_TRACE` set; the generated js module writes one line per process on its first call. Verdicts
  `NOT_CALLED` (the command passed, no call traced) and `SERVICE_TEST_FAILED`; the number of calling processes is
  recorded. Only the js module is traced (not the py target, not an app's own loader of the `.js` file).
- Measured (one machine, Node 24.3.0, warm, medians of 5 passes, 3 runs): wallet `Spend` 2,000 x 10 calls: old
  loader 6.34–6.42 ms, module 6.48–6.73 ms (no gain: ints use bignumber.js in both); a rule of a real app, 2,000 calls
  with 10 limits each, on a copy of the app: old wrapper 62.6–81.9 ms, module 26.7–41.3 ms, no decision differs.
  The app's test suite on the copy with its rule file on the module: the same results as with the old wrapper. Its
  service test as serviceTest: `NOT_CALLED` with
  the old `new Function` wrapper (not traced), `BUILT` with 1 calling process on the module wrapper.
- Tested: 62 cases (12 new): the module imports under `--disallow-code-generation-from-strings` and a copy that
  calls `new Function` does not; the generator is deterministic and refuses `new Function`, `eval` and other
  `require`s; same results as the old loader on 5,000 wallet fuzz inputs and the 4 decisions, and an edited module
  disagrees; string/seq/set/int helpers equal the old loader with the cache on, off and at 16 entries, and a cache
  that returns a wrong seq is caught; a hand edit of the module is `STALE_BUILD`; the build's harness-module check
  fails on a different output; serviceTest `BUILT` / `NOT_CALLED` / `SERVICE_TEST_FAILED`.
- Not tested: browsers and Cloudflare Workers themselves (only Node with code generation from strings forbidden);
  Windows; `--unicode-char false` builds (the helpers then pass JS strings through, untested).
- Later option, not built: Rust output compiled to WebAssembly.

- Status line and `check` say what VERIFIED covers: `model PROVED`, the fuzz count with `wrapper NOT proved`, bypasses, `records current`.
- `DECISION_NOTE_STALE`: code named in a decision record changed after approval (warning only). `.forge/described.json` is written at approval.
- Approval hashes only the binding sections of a decision record (`n:` hashes); notes sections can be edited without re-approval. Old full-file approvals are still accepted.
- Next-rule suggestion from the Out-of-scope items that name code.
- `build`: `APP_SYNTAX_ERROR` when an app file a check touches, or any JS/TS file that imports one, does not parse; optional `typecheck` command (`TYPECHECK_FAILED`). `check` warns on the same.
- `/forge approve` shows a digest; `--full` prints every file; approved, unchanged files collapse to one line.
- Measured on a copy of a real app (2 rules): stale note flagged on a changed function; the note fixed without re-approval; a missing brace in a file three imports away flagged; another function suggested as the next rule; approval screen 26,214 → 1,404 characters with both rules already approved.
- Added (fuzzer): messy inputs for the unproved wrapper that turns app/JSON values into proved-code values (owner
  feedback: a real app's wrapper threw on an id field set to `undefined`, and the fuzz never sent one). An
  adapter may export `wrapper` / `wrappers` (the app's real wrapper, called with raw values) and `failClosed` /
  `fail_closed`. Each field is in turn made undefined (JavaScript), null, a number, a boolean, "", a lone surrogate,
  a long text, [], {}, the number as text, or removed. A throw is always `THROWS_ON_MESSY_INPUT`; any other answer
  must equal the fail-closed answer (or the proved rule's answer where the input is one it takes). Grouped, shrunk
  and saved like other disagreements. On by default when the adapter names a wrapper; `"messy": false` turns it off.
- Changed (protocol, backward compatible): the hello line may name `"messy"` candidates; messy lines carry
  `"messy": true` and are sent with ASCII escapes. Results, case files and printed reports keep a lone surrogate as
  its JSON escape instead of failing.
- Changed: `/forge fuzz` and `/forge:build-setup` tell Claude to write wrappers that fail closed on a non-text id and
  to add the wrapper as a messy candidate; templates for TypeScript and Python.
- Tested: 58 cases (8 new: messy off finds nothing (negative control); the JS wrapper that throws on amount
  undefined/null is found as THROWS_ON_MESSY_INPUT and shrunk to {cap 0, spent 0, amount undefined}; the Python one
  that raises on None, only on that field; the wrappers that turn "5" into 5, in both languages; the fail-closed
  wrapper has no messy disagreement (negative control); every kind is tried and judged; saved messy cases replay).
  On a copy of that app (its real wrapper file, an adapter written for the test): the throw on the
  `undefined` id field is found, with 23 other throwing fields/kinds (ids that are not text, `limits` that
  is not a list, `entry` or `viewer` that is not an object); with a fail-closed patch of the wrapper, 2,776 messy
  inputs give 0 disagreements. Not tested: messy inputs for Go, Rust, Java, C# and "cmd" evaluators (the protocol
  allows them; no evaluator announces messy candidates yet).
- Added: `forge_check.py bypass` finds code that goes around a proved rule: lines outside the rule's own files that
  use the check's `"watchedFields"` (literal text or `/regex/`), with file:line, term, text and enclosing function.
  Verdicts `BYPASS_CLEAR` (exit 0), `BYPASS_FOUND` (exit 1), `NO_SCOPE_TERMS` (exit 0, nothing searched). Places the
  person allows are kept in `"allowedBypasses"` (in forge.json, so a person approves each) by the SHA-256 of the line,
  so an edited line is reported again. `bypass --rule NAME --set-terms …` and `--record FILE:LINE --note …` write
  them; `bypass --count [--rule NAME --set-terms …]` previews lines per term and writes nothing.
- Added: a hard gate in `check`: a check with `"watchedFields"` whose proof passes is `UNAPPROVED_BYPASS` (exit 1)
  while an unallowed line outside the rule matches; the detail lists the lines. A failed proof still wins; a check
  without `"watchedFields"` behaves as before. The lines also go under `"outside"` in the JSON and on one report line.
  If the search itself fails, a check with `"watchedFields"` is `ERROR` (fail closed).
- Added: `/forge:bypass` (a conversation): proposes SPECIFIC watched fields (property access, comparisons) from the
  approved spec and decision record, shows the measured count per term before saving, explains each place, and
  asks per place: route it through the proved rule, it is fine (record why), or a new rule. `/forge approve yes`
  prints the count; bare `/forge` offers `/forge:bypass` for a check without watched fields.
- Changed: the example has watched fields; its six shipped legacy functions are allowed bypasses (they are the code
  the fuzz compares). forge.json re-approved, INVARIANTS.md regenerated. The planted second spender
  (`gift_card.py`, `can_send_gift`) lives in the tests, so the example stays VERIFIED under the gate.
- Fixed (found in CI): `check` crashed where git is not installed; the search now walks the folder.
- Measured: on a copy of the real app that motivated it (205 files searched, about 0.3 s): the two bare field
  names match 290 lines in 25 files; the specific set (a property access with its dot, and a comparison on the id
  field from each side) matches 19 lines in 10 files and still names the three places found by hand.
- Tested: 18 new cases (found; own files never reported; UNAPPROVED_BYPASS for a new reader; allowed and re-approved
  → VERIFIED; edited allowed line → UNAPPROVED_BYPASS again; a failed proof still FAILED; negative control without
  watchedFields → VERIFIED; no other use → clear; no terms; bad regex; review of a non-hit refused; no git on PATH;
  git untracked/ignored/node_modules; `--count` preview; an app-shaped TS/Java/Go/Rust project). Not tested:
  the `/forge:bypass` conversation and the register.ts wiring inside Claude Code.

## 0.12.2 — 2026-11-22
- Fixed (fuzzer): different bugs that give the same wrong answer were reported as one. The fuzzer kept one example
  per kind of wrong answer and stopped at 5 per candidate, so on a real app's original code three real fail-open bugs
  (an unknown visibility treated as shared; limits whose key fields are not text covering nothing) stayed
  hidden behind already-known ones until they were masked by hand. Now each kind of wrong answer keeps up to 3
  examples of different input shape (empty, zero, null, unknown text, list sizes), up to 40 per candidate, grouped in
  the report. Re-run on the same app code without masking: the two limit bugs appear in the random search and the
  visibility bug as a failing decision.
- Fixed: the JavaScript evaluator split input lines at U+2028 and U+2029 (Node's readline); it now splits at "\n" only.
- Tested: 50 cases (1 new: two bugs with the same wrong answer, zero and empty text, are both reported).

- Added: the MCP airlock in the same plugin (`airlock/`, `/forge:airlock`, `python3 airlock/forge_airlock.py up --
  <command>`): `forge.yaml` drafted by Claude (secrets by NAME only), a box whose workspace has the hidden paths
  absent (also from git history), default-deny MCP tools, caps decided by the compiled #91 escrow verifier (PROVED
  model), a breach canary (fail closed), an egress allowlist, `--pass-env NAME` with a warning, and a diff
  sync-back applied only after y. Ported from Forge's private proposal #97. No existing command changed; only
  `up`, `tools` and `check` need Docker.
- Changed (airlock): gated calls go to ONE persistent verifier process with a memo of its pure SHA-256 and key
  validation; every call still verifies the whole log. Measured: see `prove_release.sh` gate G6.
- Fixed (airlock, the two known gaps of #97): symlink swap between the check and the open (the workspace is
  mounted `nosymfollow` for the airlock and every upstream); mid-call crash, timeout and airlock kill (steps end
  ABORTED and stay spent; the exact total survives restarts; a crashed stdio upstream is started again).
- Added: `prove_release.sh`, 8 release gates, exit 0 only if all pass.
- Added (airlock): `llm` in `forge.yaml`, the airlock's model endpoint. The box gets `ANTHROPIC_BASE_URL` and no
  key; the airlock adds the key (named by `key_env`), forwards through the egress proxy (an airlock-only target),
  streams SSE back, reserves `max_tokens` + estimated input before forwarding and settles to the reported usage
  after (the full reservation stays spent when usage is unknown). Caps per session, per UTC day and per request,
  decided by the same compiled #91 verifier (PROVED model); estimate, settlement and proxy are NATIVE CHECK.
  `up` needs no `--pass-env` for the model key and refuses it. Tested: gate G8 (fake provider, fake key).
- Not tested: a real model session in the box; Linux and Windows hosts; real paid upstreams.
## 0.12.1 — 2026-11-22
- Fixed: `/forge approve` (and bare `/forge`, `/forge spec`) showed each spec shortened to its clause lines, so some
  multi-line clauses were cut off, while the approval covers the complete file by hash. It now shows every file in
  full. Found when the owner approved two rules of a real app.

## 0.12.0 — 2026-11-22
- Changed: bare `/forge` runs whatever comes next, so a beginner needs one command: nothing set up → the read-only
  scan (put in the prompt box); a scan exists → `/forge:add <number>`; not approved → shows exactly what to approve;
  approved → proves, then fuzzes the shipped code (if set up) and lists the disagreements, or offers the fuzz set-up.
- Changed: `/forge:scan` numbers its picks into `.forge/candidates.md`; `/forge:add 3` starts from pick 3 and reads it
  back for the person to confirm or reword.
- Changed: audit mode first. Until the person approves and asks to switch, set-up never modifies existing app files
  (only new files under spec/, impl/, diff/, fuzz/, .forge/). The shipped code is fuzzed as it is; "usedBy" is added
  only when the app is switched to the proved code. (A tester's run had edited app code before approval.)
- Fixed: on Linux systems without the ICU library (minimal images, many servers), the downloaded Dafny stopped at
  start ("Couldn't find a valid ICU package"); it now runs with invariant globalization. Found in a clean
  python:3.12-slim container with no .NET and no ICU; a CI job now covers that case.
- Changed: README top: one sentence, two commands, real output.
- Tested: bare `/forge` in headless sessions at three stages (empty project → scan offered; not approved → contract
  shown; approved → VERIFIED, then fuzz results in 5 languages, app files unchanged); 49 cases.

## 0.11.0 — 2026-11-22
- Added: `INVARIANTS.md`, every approved rule in one reviewable place (`forge_check.py invariants`,
  `/forge invariants`, regenerated by `/forge approve yes`): the owner's words, plain-words guarantees, decisions,
  defaults, what is not guaranteed, the code, compile targets and fuzz targets, the trust base and what is not
  covered. Generated only from approved files and forge.json, with no times or verdicts, so it is deterministic.
  `invariants --check` (Action input `invariants: "true"`) fails when the committed file is stale.
- Tested: 49 cases (4 new): a fresh generation equals the committed example; a changed decision makes it stale;
  after regeneration the change is visible and the rule is marked not approved as written.
- Changed: `/forge:scan` also ranks the code that acts on a decision, small predicates that decisions depend on, and
  retry/backoff/timing rules. From a blind evaluation of 0.10.2 on 10 FRESH bugs in 5 repositories never used before
  (PyrateLimiter, cron-parser, dinero.js, node-jsonwebtoken, unleash-client-node): recall 7 of 10 in the top 5 (3 of
  4 for fixes made after the model's training cutoff), top-5 hit rate 25 of 25 at HEAD, median scan 56 s. The three
  misses were exactly these three kinds of code. n = 10, one scan per checkout, grading by one agent, not blind.
  This set has now shaped the instructions too; the next measurement needs new bugs.

## 0.10.2 — 2026-11-22
- Changed: `/forge:scan` ranks the code that decides which rule applies next to the rule it selects.
- Evaluation of 0.10.1 (13 blind headless scans, same 8 known bugs, graded by one agent, not blind): top-5 hit rate
  25 of 25; recall 5 of 8 in the top 5 and 8 of 8 in the top 7; 3 top-3 rows with a suspected-bug note (14 in
  0.9.1); median scan 61 s (81 s in 0.9.1, 43 s in 0.9.0). The two missed top-5 bugs were in rule-selection code,
  which this change addresses. Caution: these 8 bugs have now shaped three rounds of changes; the next evaluation
  needs fresh bugs.
- Not tested: this change.

## 0.10.1 — 2026-11-22
- Changed: `/forge:scan` ranking, from a second blind evaluation of the v0.9.1 instructions: blind recall of 8
  known bugs rose from 3 to 6 in the top 5 (7 in the top 7), but the top-5 hit rate fell from 96 % to 92 %, scans
  took about twice as long, and 14 of 15 top-3 rows were ranked on a suspected bug rather than on stakes (one
  high-stakes rule of the owner's app dropped out of the top 7). Now: rank by stakes first, a suspected bug only breaks
  ties; work quickly (search first, read only the functions being ranked); general wording instead of a
  test-specific example. n = 8 is small; each scan ran once.
- Not tested: these changes; the evaluation should be run again.

## 0.10.0 — 2026-11-22
- Added: Java and C# evaluators for the differential fuzz (`"lang": "java"`, `"lang": "cs"`), so all six languages
  Dafny compiles to are covered. Each ships a protocol program with a small exact JSON reader/writer (Java) or
  System.Text.Json (C#); Dafny integers are BigInteger in both. The code under test is listed in "java_sources" or
  "cs_sources". Rebuilt only when a source's content changes (javac and dotnet have no quick no-op build).
- Tested (Java, locally): the shipped Java function's planted bugs are found: the exact-cap rule (D1) and, by
  itself, the long wraparound near 2^63 (the shipped code accepted the spend and set the balance to
  -9223372036854775808). About 220 ms per run once built. 45 cases.
- Tested (C#, CI only; this machine has no .NET): `tests/run_cs_diff.py` builds the C# target and requires the
  planted zero-amount bug (D3) and the long wraparound to be found.

## 0.9.1 — 2026-11-22
- Changed: `/forge:scan` instructions, from a blind evaluation (13 headless scans of the owner's app and 4 public
  repositories: golang/time, casl, node-rate-limiter-flexible, transitions). Top-5 hit rate was 96 % (24 of 25), but
  blind recall of 8 known past bugs was 3 of 8 in the top 5. The scan now: includes error and fallback branches
  that decide allow/deny (one missed bug was there, and the scan had called it glue); includes the code that
  decides which rule applies (type, tenant or role detection; two missed bugs); keeps a large core
  state-transition function instead of preferring small helpers (one missed bug); states the intended rule rather
  than paraphrasing the code, and ranks a suspected bug in the top 3; states the precision requirement of unit
  conversions; skips vendored and generated folders.
- Not tested: whether recall improves; the evaluation should be re-run with these instructions.

## 0.9.0 — 2026-11-22
- Added: Go and Rust evaluators for the differential fuzz (`"lang": "go"`, `"lang": "rs"`). The person writes a
  small adapter; Forge generates the protocol program (`cli/eval/go/forge_main.go`, `cli/eval/rust/forge_main.rs`),
  links the compiled proved code and the code under test, and builds it in `.forge/cache/` with go build or cargo
  (their own incremental caches decide what to rebuild). Exact big integers in both (Go json.Number and big.Int;
  Rust serde_json with arbitrary precision). A panic is reported as a disagreement.
- Fixed: Dafny's Go output under `verified/` would break the person's own `go build ./...`; it now gets a go.mod
  of its own. The Go adapter carries `//go:build forge` for the same reason.
- Tested: 44 cases (3 new). On the example: the shipped Go function's planted bugs (exact cap, zero, negative)
  fail decisions D1, D3, D4; the shipped Rust function fails D3 and its planted overflow is found as
  `panic: attempt to add with overflow`; values that do not fit int64/u64 are reported as panics. Timing as in the
  README. CI installs go and goimports.
- Not tested: Windows; Go modules with dependencies that need a download; Rust crates used through "rust_deps".

## 0.8.0 — 2026-11-22
Released on the owner's word before the first real-project test (the owner's app). Treat the differential fuzz as
early: report what it misses or gets wrong.

- Added: the differential fuzz: the person's real code (the shipped function, the wrapper) against the compiled
  proved rule on fresh inputs. One language-neutral core (`cli/forge_diff.py`): seeds, the person's recorded
  decisions as fixed inputs, replay of saved cases, shrinking that keeps the failure signature fixed (no sliding
  into another bug), one saved case per kind of failure under `.forge/regressions/`, a 2-second limit per call
  (hangs are reported), running totals since the last new failure (`.forge/diff-stats.json`). Each language only
  needs an evaluator that speaks a line protocol: shipped for JavaScript/TypeScript and Python; any other language
  as a `"cmd"` entry. Inputs are one Python file per rule, shared by every language.
- Added: automatic run at the end of each Claude turn in which a watched file changed (by modification time, so
  shell and editor edits count); `/forge add` sets the fuzz up when the rule already exists in code; `/forge fuzz`
  sets it up for other rules or does a deep run; `/forge diff` is the quick run; `/forge build` without a set-up
  puts the set-up command in the prompt box.
- Added: integers above 2^53 cross the line protocol exactly (the JavaScript evaluator reads them as BigInt and
  writes BigInt and bignumber.js values with all their digits; plain JSON.parse would round 2^63 - 1). The example
  inputs now include values near 2^53 and 2^63; the fuzz found, unplanted, that the shipped TypeScript function
  (plain numbers) computes a wrong balance above 2^53.
- Tested: 41 cases (13 new). On the example: the planted bugs in the shipped Python function (exact cap rejected,
  zero accepted, negative accepted) and in the shipped TypeScript function are found through the decisions and the
  random search; the wrapper agrees; fixing the shipped code makes every decision agree; an outdated build is
  detected; a `"cmd"` evaluator works through the protocol alone; a hanging function is reported within seconds.
  A run takes under a second for both languages. Headless Claude Code sessions (before the core refactor):
  `/forge diff`, an Edit-tool edit and a shell edit each trigger the automatic run; a turn without change does not.
- Not tested: a real project (the owner's app is next); evaluators for Go, Rust, Java and C#; TypeScript syntax Node
  cannot strip; Windows (the core uses `select` on pipes, which Windows does not support for pipes).

## 0.7.2 — 2026-11-22
- Fixed (regression in 0.7.1): the hint commands `commands/approve.md`, `check.md`, `build.md`, `status.md`
  shadowed the fixed steps: in a headless session `/forge approve yes` printed the hint instead of approving.
  Removed. A static test now fails if a Markdown command takes the name of a `/forge` step.
- Fixed (found by a tester): `/forge approve yes` failed when it was typed before `/forge approve`, or after the
  plugin reloaded between the two steps. What `/forge approve` shows is now kept in
  `.forge/pending-approval.json` (24 hours; Claude's edit tools cannot write it). "yes" with nothing shown shows
  it and approves nothing; "yes" after a file changed shows the new version and approves nothing.
- Tested: each step in a separate headless session: yes first, yes again, check, change in between, yes again;
  28 cases.

## 0.7.1 — 2026-11-22
- Fixed (usability): `/forge:approve` (with a colon) answered "Unknown command"; a tester typed it. Now
  `/forge:approve`, `/forge:check`, `/forge:build` and `/forge:status` each print the right command (with a
  space). They change nothing and Claude cannot run them on its own (`disable-model-invocation`).
- Fixed: `build --check` reported `STALE_BUILD` after the app had simply run the built Python code, because
  Python's cache files (`__pycache__`) appeared inside the emitted folder. They are now skipped. Found by
  the test suite after the example was run.
- Tested: `/forge:approve` in a headless Claude Code session prints the two lines; 28 cases (1 new).

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
