# Forge in CI

The GitHub Action (`action.yml`) runs `cli/forge_ci.py`, which calls `cli/forge_check.py` for each gate. Every
verdict comes from `forge_check.py`; `forge_ci.py` only chooses what to run, prints the normal reports (one log
group per gate) and writes the job summary and a JSON report.

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
| `clean` | none | pass; 50,000 inputs, `DIFF_OK` |
| `spec-weakened` | W1 removed from the spec | fail, `APPROVAL_CHANGED` |
| `spec-weakened-nobase` | same, with `base: none` | fail, `SPEC_CHANGED` |
| `bypass` | a new `refund.py` that compares `spent + amount <= cap` itself | fail, `UNAPPROVED_BYPASS` |
| `stale-invariants` | a hand edit of `INVARIANTS.md` | fail, `INVARIANTS_STALE` |
| `logic-bug` | `app.py` rejects an exact-cap spend when the cap ends in 777 | fail, `DISAGREE` |

Job `checker-tests` also tests the base detection per event and `--base` with the project in a subfolder.

## Limits

- The diff sweep is a FINITE TEST. A bug that 50,000 inputs of the inputs module do not reach is not found. The
  `logic-bug` control is found by most, not all, 1,500-input runs being too few and 50,000 being enough on the
  measured seeds; it is not a guarantee for other bugs.
- The bypass gate is a text search (see the README), not a data-flow analysis.
- Java, Rust and .NET toolchains are the runner's own; their versions are not pinned by the Action.
- Only GitHub-hosted Ubuntu runners are tested. macOS and Windows runners are not tested with this Action.
- A push event's base must still exist on `origin` (a force-push can remove it): the fetch then fails and the job
  fails with the reason in the summary.
