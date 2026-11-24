# Forge in CI

The GitHub Action (`action.yml`) runs `cli/forge_ci.py`, which calls `cli/forge_check.py` for each gate. Every
verdict comes from `forge_check.py`; `forge_ci.py` only chooses what to run, prints the normal reports (one log
group per gate) and writes the job summary and a JSON report.

[Back to the README](../README.md)

## Quick start

The same checker runs in CI with no configuration. Commit `.forge/` (and the emitted code and `INVARIANTS.md`), then
copy [`examples/workflows/forge.yml`](../examples/workflows/forge.yml) to `.github/workflows/forge.yml`:

```yaml
name: forge
on:
  pull_request:
  push:
    branches: [main]
  merge_group:
permissions:
  contents: read
jobs:
  forge:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: mack-dot/forge-claude-mod@main   # pin the commit hash of the release you reviewed
```

With no inputs, the Action reads `.forge/forge.json`, installs Dafny and only the toolchains the checks use (Node
for `js` or JS/TS app files, Go for `go`; Java, Rust and .NET come from the runner), and fails the job if any gate fails:

| Gate | Runs | Fails with |
| --- | --- | --- |
| `check` | always | the proof (`FAILED`), `NOT_APPROVED`, `SPEC_CHANGED`, `CONFIG_CHANGED`, `BINDING_BROKEN`, `UNSOUND_CONSTRUCT` (included files too), `EXAMPLES_FAILED`, `UNAPPROVED_BYPASS`, `UNVERIFIED_CALLER` |
| callers | when a check has `"callers"` (its own row in the summary) | `UNVERIFIED_CALLER`, the analysis did not run |
| approvals against the base | when the event has a base: the pull request's base branch, the merge group's base commit, or the commit before a push | `APPROVAL_CHANGED` |
| `build --check` | when a check has a `build` entry | `STALE_BUILD`, `APP_SYNTAX_ERROR`, `NOT_CALLED`, a target's fuzz |
| serviceTest | when a check has a `build` `serviceTest` (run by `build --check`; its own row in the summary) | `NOT_CALLED`, `SERVICE_TEST_FAILED` |
| `invariants --check` | always | `INVARIANTS_STALE` |
| diff sweep | when a check has `diff` entries: 50,000 inputs per entry, a 600 s budget per entry, seed = the workflow run ID | `DISAGREE`, `THROWS_ON_MESSY_INPUT`, `TOOLCHAIN_MISSING` |

The job summary has one status line per rule in the same scope-honest words as the Claude Code status line
(`model PROVED · fuzzed 50,000 cases, wrapper NOT proved · 0 bypasses (6 allowed by a person) · build BUILT ·
INVARIANTS.md current`), a table with each gate's verdicts, the bypass count, the fuzz counts and time, and the
exact commands (base commit and seed included) that reproduce the run on your machine. A re-run of the same
workflow run uses the same seed. On an event without a base (for example `workflow_dispatch`), the summary says
`APPROVAL_CHANGED` was NOT checked.

Inputs, all optional: `root`, `base` (a git ref, or `none` only for repositories whose approvals are test
fixtures), `build`/`diff` (`auto` or `false`), `invariants` (`true` or `false`), `diff-inputs`,
`diff-budget-seconds`, `diff-seed`, `diff-tier` (`ci` or `nightly`: input count, budget and steering from the
tier in `forge.json`), `dafny-version`, `node-version`, `go-version`. Outputs: `result` (`pass` or
`fail`) and `report` (a JSON file with every verdict). More below.

`APPROVAL_CHANGED` means a person with the right to approve specs must review the change. To make this a lock,
require the job in branch protection and protect `.forge/` and `.github/` with CODEOWNERS.

Without Claude Code, from a shell (Python 3.8+, Dafny on PATH or `FORGE_DAFNY`):

```
python3 cli/forge_check.py setup     # find Dafny or download the pinned release
python3 cli/forge_check.py check     # exit 0 only if every check is VERIFIED
python3 cli/forge_check.py spec      # show what an approval covers
python3 cli/forge_check.py approve   # a person approves, at a terminal
python3 cli/forge_check.py build     # compile, fuzz in every target, emit (see below)
python3 cli/forge_check.py mutate    # plant small bugs, count how many the proof catches
```

`examples/wallet-cap` is a small complete project. `tests/run_tests.py` drives every verdict.

## Zero configuration

Copy [`examples/workflows/forge.yml`](../examples/workflows/forge.yml) to `.github/workflows/forge.yml`. With no
inputs:

1. **Plan.** `forge_ci.py plan` reads `.forge/forge.json`: build targets, diff languages, the app files the checks
   touch and `serviceTest` / `cmd` programs. Node is installed only for `js` or JS/TS app files (or a Node
   program), Go (with goimports) only for `go` or Go app files. Java, Rust and .NET are not installed: the
   GitHub-hosted Ubuntu runner has them. On a runner without one, the gate that needs it gives
   `TOOLCHAIN_MISSING` and the job fails.
2. **Base.** `pull_request` / `pull_request_target`: the base branch; `merge_group`: the base commit; `push`: the
   commit before the push. The base is fetched (`--depth=1` is enough: only the protected files are compared).
   A push that creates a branch, and other events, have no base: the summary says `NOT checked`.
3. **`check --base`.** The proof and every approval verdict, the bypass gate included. See the README.
4. **`build --check`** when a check has a build.
5. **`invariants --check`.** On by default: `/forge approve yes` writes `INVARIANTS.md`; commit it.
6. **Diff sweep** when a check has diff entries: `--inputs 50000 --budget-ms 600000 --seed <run ID>`. The sweep
   stops at the input count or the budget per entry, whichever comes first; the summary says so when the budget
   cut it short (that is not a failure). Saved cases under `.forge/regressions/` replay first.

All gates run even after one fails, so one run shows every finding. The job fails if any gate fails.

## Replay a failure

The summary ends with the commands to run in your clone, for example:

```sh
# FORGE = a clone of mack-dot/forge-claude-mod at the ref this workflow uses
git fetch --no-tags --depth=1 origin <base commit>
python3 "$FORGE"/cli/forge_check.py check --root . --base <base commit>
python3 "$FORGE"/cli/forge_check.py diff --no-write --root . --inputs 50000 --budget-ms 600000 --seed <seed>
```

The same seed gives the same inputs only with the same inputs module, the same saved cases and the same input
count; a run that the time budget cut short may stop at a different input on a slower or faster machine.

## Negative controls (this repository's `selftest.yml`)

Job `action-controls` builds a stand-alone fixture repository from `examples/wallet-cap`
(`.github/ci-fixture/make.sh`): Python target only, and the diff fuzzes only the app's wrapper (`app.py`, correct),
because the example's legacy code keeps planted bugs for its own tests. Its base commit is `main` on a local
`origin`. Each matrix entry changes it like a pull request (`.github/ci-fixture/plant.sh`), runs the Action with no
inputs, and `.github/ci-fixture/expect.py` requires the outcome and verdict:

| Entry | Change | Required |
| --- | --- | --- |
| `clean` | none | pass; 50,000 inputs, `DIFF_OK`; callers gate passes (the fixture's `"callers"`: `app.py:spend`, replaced `legacy_wallet.py:legacy_spend`) |
| `spec-weakened` | W1 removed from the spec | fail, `APPROVAL_CHANGED` |
| `spec-weakened-nobase` | same, with `base: none` | fail, `SPEC_CHANGED` |
| `bypass` | a new `refund.py` that compares `spent + amount <= cap` itself | fail, `UNAPPROVED_BYPASS` |
| `unverified-caller` | a new `fast_path.py` that calls the replaced `legacy_spend` through an import alias | fail, `UNVERIFIED_CALLER`, callers gate names `fast_path.py:5` |
| `stale-invariants` | a hand edit of `INVARIANTS.md` | fail, `INVARIANTS_STALE` |
| `logic-bug` | `app.py` rejects an exact-cap spend when `cap % 250 == 27` | fail, `DISAGREE` |

Measured on GitHub-hosted `ubuntu-latest`, selftest run 45113316698 (one run):

- Python fixture, 50,000 inputs per run: 2,049 ms (`clean`), 1,500 ms (`logic-bug`), 3,958 ms (`bypass`), 4,054 ms
  (`stale-invariants`). The whole Action step for `clean` (Dafny proof, build fuzz, invariants, sweep): 15 s.
- `logic-bug`, first version (cap ends in 777): 1,500 inputs found it in 2 of 20 seeded runs, 50,000 inputs in 10
  of 10. In the next run (45113930328, seed 2164257368) the 50,000-input sweep missed it: about 0.9 inputs in
  10,000 hit it, a miss chance near 1% per run. It was replaced by `cap % 250 == 27` (about 4 in 10,000; counted
  over 2,000,000 generated inputs on one machine); in run 45114616292,
  the Action's sweep (seed 2164943332) found it in 2,198 ms; 1,500 inputs found it in 8 of 20 seeded runs (seeds
  1–20), 50,000 inputs in 10 of 10 (seeds 1–10).
- Run 45114616292: `clean` sweep 2,158 ms; the example's five languages 3,829–5,389 ms per language, 31 s wall
  time; `action-on-example` sets up no toolchain itself, so the Action installed Node and Go for it.
- The example, 50,000 inputs in each of its five languages (its legacy code disagrees, so this includes grouping
  and shrinking): py 2,813 ms, js 2,418 ms, go 2,204 ms, rs 2,192 ms, java 1,901 ms; 17 s wall time for the step,
  evaluator builds included.

Job `checker-tests` also tests the base detection per event and `--base` with the project in a subfolder.

## Limits

- The diff sweep is a FINITE TEST. A bug that 50,000 inputs of the inputs module do not reach is not found. The
  `logic-bug` control is one rare bug; the numbers below are about that bug only. With the seed taken from the run
  ID, a run can miss it: the first version of the bug was missed once (below).
- The bypass gate is a text search (see the README), not a data-flow analysis.
- Java, Rust and .NET toolchains are the runner's own; their versions are not pinned by the Action.
- Only GitHub-hosted Ubuntu runners are tested. macOS and Windows runners are not tested with this Action.
- A push event's base must still exist on `origin` (a force-push can remove it): the fetch then fails and the job
  fails with the reason in the summary.
