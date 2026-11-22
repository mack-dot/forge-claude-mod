# Forge for Claude Code

A Claude Code mod that brings the Forge proof gate into your session. (Forge's research record is a separate,
private repository; this plugin needs nothing from it.)
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

Type `/forge` in any repository: it tells you the next step. Commands with a **colon** (`/forge:scan`) start a
conversation with Claude; commands with a **space** (`/forge approve`) run a fixed step. The steps:

| Step | You type | What happens |
| --- | --- | --- |
| Find candidates (optional) | `/forge:scan` | Claude ranks up to 7 rules in your code worth proving, and names what should not be proved. Changes nothing. |
| State a rule | `/forge:add <the rule in your own words>` | A conversation: Claude says whether the rule can be proved, confirms safe defaults, asks edge-case questions with concrete numbers (as many rounds as needed), probes the spec with Dafny for cases nobody asked about, then writes the spec, **your answers as Dafny checks**, a decision record and the implementation. |
| Read it | `/forge:explain` | Claude explains every clause in plain words, checks that each of your answers is covered, and lists what is not guaranteed. |
| Approve it | `/forge approve`, then `/forge approve yes` | Shows exactly what you approve (spec, your answers, decision record, `forge.json`); "yes" approves exactly that, even after a restart. Typing "yes" first only shows it. If a file changed in between, it shows the new version instead. Only you can do this. |
| Prove it | `/forge check` | Your answers are checked against the spec (`EXAMPLES_FAILED` if the spec says otherwise or leaves one open), then the code against the spec. The status line shows `forge ✓ VERIFIED n/n` or what failed. |
| Measure it | `/forge mutate` | Plants up to 30 small bugs in the proved code, one at a time in a temporary copy, and counts how many the proof catches. A survivor is a change your spec allows: harmless, or a gap in the spec. Changes nothing. |
| Something failed | `/forge:fix` | Claude explains the verdict and proposes a fix to the code; it never weakens the spec without your word. |
| Use it in your app | `/forge:build-setup`, then `/forge build` | Compiles the proved code to your languages (`py`, `js`, `go`, `java`, `cs`, `rs`), fuzzes the compiled code in every language (identical output required), and checks that your app imports it. |
| It does not run | `/forge:doctor` | Claude checks Python, Dafny and the config, and explains the fix. |

Dafny (the proof checker) is set up for you: the `"dafny"` path in `.forge/forge.json` if set, else `dafny`
on PATH, else `FORGE_DAFNY`, else a pinned download (macOS arm64/x64, Linux x64, Windows x64).

After that, let Claude (or any agent) change the covered code freely. When an edit touches a covered file,
the status line says "code changed" until you run `/forge check` again. `/forge status` and `/forge spec`
show the last verdicts and the approved contract.

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
      - uses: mack-dot/forge-claude-mod@v0.7.2   # better: pin the commit hash, tags can move
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
python3 cli/forge_check.py mutate    # plant small bugs, count how many the proof catches
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
