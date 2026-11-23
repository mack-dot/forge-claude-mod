# Forge for Claude Code

**Approve the rules your code must never break, in plain words; Forge proves them and fuzzes your real code
against them, in Python, TypeScript, Go, Rust, Java and C#.**

```
/plugin install forge --marketplace mack-dot/forge-claude-mod
/forge
```

That is all you need to remember: `/forge` runs whatever comes next (a read-only scan, your approval, the proof,
the fuzz). Nothing in your app changes until you approve. What it looks like on the included example:

```
forge ✓ VERIFIED 1/1
✗ wallet-cap:py: 3 failing decision(s) in 1500 random inputs (177 ms)
  your recorded decisions: 1 of 4 agree; failing: D1 exact cap allowed, D3 zero rejected, D4 negative rejected
✗ wallet-cap:java: … legacy-java says {"ok": true, "spent": -9223372036854775808}   (a long overflow, found by itself)
Every disagreement above is a bug in your shipped code or a gap in the rule. Your app files are unchanged.
```

Proved means: the rule's Dafny model is proved. Compiled code, your wrapper and the fuzz are tested, not proved.
Details, limits and every command are below.

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
| Find bugs in your real code | automatic (set up by `/forge add` when the rule already exists in your code), or `/forge fuzz` | Runs your shipped code and the proved rule on fresh random inputs at the end of each turn that changed them (about a second), saves each disagreement as a small repeatable case. `/forge fuzz` on its own is a deep run. See **Fuzz** below. |
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

## Fuzz: your real code against the proved rule

A proof shows that the Dafny model is right. Your app may still run its own, older version of the rule. The
differential fuzz runs your **real code** (the shipped function, and the wrapper around the proved code) and the
compiled proved rule on the same fresh inputs, and reports every input where they disagree:

```
✗ wallet-cap:py: 3 failing decision(s), 0 other disagreement(s) in 1500 random inputs (225 ms)
  your recorded decisions: 1 of 4 agree; failing: D1 exact cap allowed (legacy), D3 zero rejected (legacy), D4 negative rejected (legacy)
  searched so far: 3,000 inputs in 2 runs; nothing new for 1 run
```

Each disagreement is a bug in that code, or a place where your spec says something other than what you meant.

- **Turned on for you:** when the rule already exists in your code, `/forge add` sets the fuzz up next to the spec.
  For other rules: `/forge fuzz <function>`. After that it runs by itself at the end of every Claude turn in
  which a watched file changed (by any means: Claude's edits, a shell command, your editor): about 1,500 fresh
  inputs per language, under a second on the example, result in the status line. A turn without a change runs
  nothing. `/forge fuzz` again is a deep run (50,000 inputs); `/forge diff` is the quick run on demand.
- **Your decisions, always:** every decision recorded in `/forge add` runs as a fixed input on every run.
- **How much it has searched:** inputs and runs so far, and since the last new failure. A long stretch with
  nothing new is evidence, not proof, that the easy bugs are gone.
- **Smallest cases, kept:** each disagreement is shrunk to a small input that fails the same way, saved under
  `.forge/regressions/`, and replayed first next time. Commit them: they are your new tests.
- **Hangs:** a function that does not answer within 2 seconds is reported, not waited on.
- **Big integers:** values above 2^53 reach JavaScript and TypeScript code exactly (as BigInt); on the example the
  fuzz found that the shipped TypeScript code, which uses plain numbers, gets the balance wrong above 2^53.

**Any language.** One Python core does the search: seeds, your decisions, shrinking, saved cases. Each language
only needs an evaluator: a process that reads one input per line (`{"input": …}`) and prints the proved rule's
answer and the code's answer (`{"reference": …, "candidates": {"name": …}}`), after a first line naming the
candidates. Forge ships evaluators for all six languages Dafny compiles to: **JavaScript/TypeScript** (Node
22.18+ imports `.ts` directly), **Python**, **Go**, **Rust**, **Java** and **C#**. For the compiled languages you
write only a small adapter; Forge generates the protocol program, links the compiled proved code and your code,
and builds it in `.forge/cache/` (add that folder to your `.gitignore`): `go build` and `cargo` decide
themselves what to rebuild; Java and C# are rebuilt only when a source file's content changes. Any other language
plugs in as `{"lang": "cmd", "run": [program, args…]}`: a small program that speaks the protocol. The inputs
(`diff/<check>_inputs.py`) are shared by every language. Integers stay exact in every language (BigInt, big.Int,
BigInteger, arbitrary-precision JSON).

Measured on the example (one machine): Python, TypeScript, Go and Rust in one run take 0.9–1.3 s once built
(120–400 ms per language; Java about 220 ms); after an edit to Rust code, cargo's rebuild makes it about 2 s; the first run that compiles the Go and
Rust evaluators takes about 7 s.

Labels: the inputs, the evaluators and the core are test code (not proved); a run is a FINITE TEST. It compares
returned values only; side effects of the code under test are not checked.

## INVARIANTS.md: every approved rule in one place

`/forge invariants` (and every `/forge approve yes`) writes `INVARIANTS.md`: one table of all approved rules, then
one section per rule with the owner's own words, the guarantees in plain words (the spec's numbered clause
comments), the decisions, what the rule does not guarantee, the code it covers, the languages it is compiled to
and the shipped code it is fuzzed against, plus the trust base every rule rests on. It is generated only from the
approved files, so it cannot drift from them. With `invariants: "true"` in the Action, a pull request fails when
the file is out of date, so any change to a rule shows up in review as a readable diff. See
`examples/wallet-cap/INVARIANTS.md`.

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
      - uses: mack-dot/forge-claude-mod@v0.12.2   # better: pin the commit hash, tags can move
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
### Messy inputs: the wrapper must fail closed, never throw

The wrapper that turns your app's values (often JSON) into the proved code's values is not proved. Runtime JSON
does not follow your types: a field can be `undefined`, `null`, a number where text was expected, or missing. On a
real app, `householdId: undefined` reached such a wrapper and it threw instead of answering "forbid". The plain
fuzz never sent that value, so it did not see it.

If the JavaScript/TypeScript or Python adapter exports a raw-value wrapper, the fuzz also sends **messy variants**
of its inputs to it: each field (nested objects and lists too, bounded) is in turn made `undefined` (JavaScript
only), `null`, a number, a boolean, `""`, a lone surrogate, a 20,000-character text, `[]`, `{}`, the number as
text (`"5"` for `5`), or removed. Big integers in the other fields stay exact.

```js
// diff/<check>_diff.mjs (Python: wrapper / wrappers, fail_closed, returning None for "the rule takes it")
export const wrapper = (i) => myRealWrapper(i)        // the app's REAL wrapper, raw values, no conversion here
// or: export const wrappers = { name: fn, ... }
export function failClosed(i) {                        // the spec's fail-closed answer for a value the
  return typeof i.householdId === 'string' ? undefined // proved rule does not take; undefined: the rule
    : { effect: 'forbid', rule: 'other_household' }    // takes it, so the proved rule's answer is expected
}
```

Rules: a throw from the wrapper on any messy input is always a disagreement of kind `THROWS_ON_MESSY_INPUT`.
Otherwise its answer must equal `failClosed(input)`, or the proved rule's answer where `failClosed` returns
`undefined`. Messy disagreements are grouped, shrunk (the messy field and its kind stay; the rest of the input
shrinks) and saved like the others. `"messy": false` on a diff entry turns them off; an inputs module can list
fields to leave alone in `messy_skip = ['retracted', 'limits[].setBy']`. A messy input that has no fail-closed
answer and that the proved rule cannot take is counted as "not judged" (only a throw is reported for it).

```
  NEW wrapper-throws: input {"cap": 0, "spent": 0, "amount": {"__undefined__": true}} → expected (fail-closed or proved) {"ok": false, "spent": null}, wrapper-throws says {"__throws__": "TypeError"}
    messy field amount = undefined: THROWS_ON_MESSY_INPUT (TypeError): the wrapper crashed instead of failing closed
```

The example has three planted wrappers (`examples/wallet-cap/messy_wrappers.ts` and `.py`, adapters
`diff/wallet_messy.*`): one that throws on `amount` undefined/null, one that turns `"5"` into `5`, and a correct one
that fails closed (the negative control). They are not in the example's `forge.json`; the tests add them.

Time, measured on the example (one machine, warm, 1,500 inputs, median of 3 runs): the three wrappers with messy
off 324 ms (JS) and 216 ms (Python); with messy on (about 1,530 messy inputs, including shrinking the planted bugs)
1,192 ms and 1,236 ms. An entry without a wrapper does not change. With the default time budget (1.5 s per entry)
messy inputs share the budget, so fewer plain inputs run (584 instead of 1,500 for JS in one measured run).

Labels: the messy values are a fixed list, not every possible value; the fail-closed answer is the adapter's (the
person's) choice, not proved; a run is a FINITE TEST.

See `PLAN.md` for what is built and what is not.

## Limits

- This is a feedback loop, not a security boundary. The mod blocks Claude's Edit and Write tools on
  approved specs and on `.forge/approved.json`, but a shell command can still change them.
- A spec covers only what it says. Files without a spec are not checked.
- Dafny runs can take seconds to minutes.

## License

MIT. See `LICENSE`. Also: `CHANGELOG.md`, `SECURITY.md`, `CONTRIBUTING.md`, `PLAN.md`, `TRIAL.md`.
