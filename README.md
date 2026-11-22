# Forge for Claude Code

A Claude Code mod that brings the [Forge](https://github.com/mack-dot/forge) proof gate into your session.
You approve a short spec once. After that, code may change only if Dafny proves it still meets the spec.

## Install

You need:

| For | What | How (macOS) |
| --- | --- | --- |
| everything | Claude Code (terminal) | already installed |
| everything | Python 3.8 or newer as `python3` | already on most machines |
| proofs | Dafny 4.11 | nothing: downloaded automatically on first use (pinned 4.11.0, SHA-256 checked, about 60 MB, into `~/.cache/forge-check`). Or install it yourself (`brew install dafny`); set `FORGE_NO_DOWNLOAD=1` to forbid the download |
| `/forge build` only | the tools of each language you build for | `py`: python3; `js`: node + npm; `go`: go + goimports; `java`: a JDK; `cs`: dotnet; `rs`: cargo |

Then, at the prompt of a Claude Code terminal session:

```
/plugin install forge --marketplace mack-dot/forge-claude-mod
```

Answer `y` to add the marketplace, then pick a scope.

From a shell:

```
claude plugin install forge --marketplace mack-dot/forge-claude-mod
```

In the desktop app: click + next to the prompt box, then Plugins, Add marketplace with
`mack-dot/forge-claude-mod`, then Add plugin.

## Use

Type `/forge` in any repository. Each time, it shows the next step and Claude walks you through it:

1. Dafny (the proof checker) is set up for you: the `"dafny"` path in `.forge/forge.json` if set, else
   `dafny` on PATH, else `FORGE_DAFNY`, else a pinned download (macOS arm64/x64, Linux x64, Windows x64).
2. **Say the rule in your own words.** Claude does not guess it from your code. It tells you whether the
   rule can be proved, confirms a few safe defaults with you, then asks about the edge cases with concrete
   numbers ("cap $50.00, spent $40.00, spend $20.00: reject, or cut to $10.00?"), as many rounds as needed.
   It also probes the spec with Dafny for cases nobody asked about and brings those back as questions.
3. Claude writes three files: the spec, **your answers as Dafny checks** (`<Name>Examples.dfy`), and a
   decision record (`.forge/decisions/<check>.md`). Type `/forge approve` to read all of it, then
   `/forge approve yes`. Approval covers all three and `forge.json`.
4. `/forge check` runs Dafny. Your answers are checked against the spec: if the spec says otherwise, or
   leaves one of your answers open, the check gives `EXAMPLES_FAILED`. Then the code is checked against
   the spec. The status line shows `forge ✓ VERIFIED n/n` or what failed.
5. When a later edit touches a covered file, the status line says "code changed" until you check again.
6. `/forge build` puts the proved code into your app. It compiles it to the languages you list
   (`py`, `js`, `go`, `java`, `cs`, `rs`), runs a fuzz harness on the compiled code in every language and
   requires identical output from all of them, and checks that your app imports the result.

Other commands: `/forge spec` (show the contract again), `/forge status`, `/forge add`.

### Verdicts

| Verdict | Meaning |
| --- | --- |
| `VERIFIED` | Dafny proved the files against the approved spec |
| `FAILED` | Dafny found a proof failure |
| `EXAMPLES_FAILED` | The spec contradicts one of your recorded answers, or leaves it open |
| `NOT_APPROVED` | The spec was never approved |
| `SPEC_CHANGED` | The spec, your answers or the decision record changed after approval |
| `CONFIG_CHANGED` | `.forge/forge.json` changed after approval (a check, spec path, file list or binding) |
| `BINDING_BROKEN` | The implementation no longer starts with the approved `include` and `refines` lines |
| `UNSOUND_CONSTRUCT` | A file uses `assume`, `{:verify false}`, `{:axiom}`, `{:extern}` or `{:only}` (text scan) |
| `APPROVAL_CHANGED` | CI only: the pull request changes `forge.json`, `approved.json` or a spec |
| `ERROR` | Dafny did not run correctly |

Why `binding`: in Dafny 4.11 an implementation can switch to refining a weaker spec while the approved spec
file stays unchanged, and Dafny still reports success. A check's optional `"binding": {"file", "prefix"}`
pins the exact first lines of the implementation, for example
`include "../spec/LedgerSpec.dfy"\n\nmodule Ledger refines LedgerSpec {`.

Files it keeps in your project: `.forge/forge.json` (checks), `.forge/approved.json` (your approvals),
`.forge/last-check.json` (last verdicts).

## Build: from proof to code your app runs

A proof of a Dafny model does nothing until your app runs the proved code. Add a `build` entry to a check:

```json
"build": {
  "targets": ["py", "js"],
  "out": "verified",
  "fuzz": "fuzz/WalletFuzz.dfy",
  "usedBy": [{ "file": "app.py", "contains": "'verified', 'wallet-cap', 'py', 'wallet_cap-py'" }]
}
```

`python3 cli/forge_check.py build` (or `/forge build`) then:

1. runs `check`; every check must be `VERIFIED`;
2. verifies the fuzz harness with Dafny, compiles it to every target, runs it, and requires the same
   output from every target. The harness repeats the spec's clauses as runtime `expect` checks on boundary
   values and pseudo-random inputs. This is what catches a miscompile in one backend;
3. emits the code under `<out>/<check>/<target>/` with a `forge-build.json` manifest of hashes;
4. requires each `usedBy` file to contain its text (a text check that the app imports the code).

`build --check` writes nothing and requires the emitted code on disk to equal a fresh build, so a hand
edit of emitted code gives `STALE_BUILD`. Commit the emitted code and run `build --check` in CI
(`build: "true"` in the Action).

| Build verdict | Meaning |
| --- | --- |
| `BUILT` | Every target passed the fuzz run with identical output, was emitted, and is imported |
| `NO_FUZZ` | No fuzz harness: the compiled code would be untested |
| `FUZZ_NOT_VERIFIED` | Dafny did not verify the harness |
| `FUZZ_FAILED` | A target failed a runtime `expect`, crashed or timed out |
| `TARGETS_DIFFER` | Two targets printed different results |
| `TOOLCHAIN_MISSING` | A listed target's tools are not on PATH |
| `EMIT_FAILED` | Dafny could not emit a target |
| `STALE_BUILD` | The emitted code on disk differs from a fresh build |
| `NOT_WIRED` | A `usedBy` file does not contain its import text |

Tools per target: `py` python3; `js` node and npm (bignumber.js is fetched once into
`~/.cache/forge-check`); `go` go and goimports; `java` javac and java; `cs` dotnet; `rs` cargo.

Honest labels: the Dafny model is PROVED. The emitted code is a NATIVE CHECK, because Dafny's compilers are
not verified. The fuzz run is a FINITE TEST. The wrapper that your app writes around the emitted code is
ordinary unproved code, so keep it thin. `examples/wallet-cap` shows all of it, with `app.py` as the app.

## Use it in CI (GitHub Action)

The same checker runs in CI. Commit `.forge/forge.json` and `.forge/approved.json`, then add:

```yaml
# .github/workflows/forge.yml
name: forge
on: [pull_request]
permissions:
  contents: read
jobs:
  forge:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: mack-dot/forge-claude-mod@v0.5.0   # better: pin the commit hash, tags can move
```

The job fails unless every check is `VERIFIED`. On a pull request, any change to `forge.json`,
`approved.json` or a spec gives `APPROVAL_CHANGED`: a person with the right to approve specs must
review that change. To make this a lock, require the job in branch protection and protect `.forge/`
and `.github/` with CODEOWNERS.

Without Claude Code, from a shell (Python 3.8+, Dafny on PATH or `FORGE_DAFNY`):

```
python3 cli/forge_check.py setup     # find Dafny or download the pinned release
python3 cli/forge_check.py check     # exit 0 only if every check is VERIFIED
python3 cli/forge_check.py spec      # show what an approval covers
python3 cli/forge_check.py approve   # a person approves, at a terminal
python3 cli/forge_check.py build     # compile, fuzz in every target, emit (see below)
```

`examples/wallet-cap` is a small complete project. `tests/run_tests.py` drives every verdict.
See `PLAN.md` for what is built and what is not.

## Limits

- This is a feedback loop, not a security boundary. The mod blocks Claude's Edit and Write tools on
  approved specs and on `.forge/approved.json`, but a shell command can still change them.
- A spec covers only what it says. Files without a spec are not checked.
- Dafny runs can take seconds to minutes.

## License

MIT. See `LICENSE`. Also: `CHANGELOG.md`, `SECURITY.md`, `CONTRIBUTING.md`, `PLAN.md`, `TRIAL.md`.
