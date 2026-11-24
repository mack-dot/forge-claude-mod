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
forge ✓ VERIFIED 1/1 (model PROVED; wrappers and other app code NOT proved)
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
| `/forge:airlock` box only | Docker with Compose v2 | Docker Desktop; nothing else in Forge needs Docker |
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
| Prove it | `/forge check` | Your answers are checked against the spec (`EXAMPLES_FAILED` if the spec says otherwise or leaves one open), then the code against the spec. The status line says what a green check covers, for example `forge ✓ wallet-cap: model PROVED · fuzzed 1,500 cases, wrapper NOT proved · records current`, or what failed. |
| Find bugs in your real code | automatic (set up by `/forge add` when the rule already exists in your code), or `/forge fuzz` | Runs your shipped code and the proved rule on fresh random inputs at the end of each turn that changed them (about a second), saves each disagreement as a small repeatable case. `/forge fuzz` on its own is a deep run. See **Fuzz** below. |
| Find code around the rule | `/forge:bypass` | Claude proposes the fields the rule decides on, searches for code outside the rule that uses them, and asks you about each place. See **Bypass** below. |
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

## The workflow in one place

1. `/forge:scan`, then `/forge:add <n>`: state a rule; Claude asks, writes the spec and your answers as checks.
2. `/forge approve`: you approve it. `/forge check` proves it; `/forge fuzz` runs your real code against it.
3. `INVARIANTS.md`: every approved rule in plain words, regenerated on approval, checked in CI.
4. `/forge:airlock`: Claude drafts `forge.yaml` for an agent box (hidden paths, read-only paths such as `spec/`,
   `.forge/` and `INVARIANTS.md`, and a rule for each MCP tool), asks you, and writes `./forge-airlock`.
5. In a terminal: `./forge-airlock up -- claude`. Claude Code works inside the box; when it exits you see the
   diff, and your repository changes only after you type y. Hidden and read-only paths are never changed.

Steps 1 to 3 need no Docker. Step 5 needs Docker. Details of the box: `airlock/README.md`.

## Airlock: an agent box for your repository

`python3 airlock/forge_airlock.py up -- <command>` (or `./forge-airlock up -- claude` after `/forge:airlock`)
reads `forge.yaml`, generates the Docker configuration in a temporary folder, stages your repository, builds the
box's workspace with the hidden paths absent (also from git history), starts the airlock with your MCP servers
(they hold the keys; secrets are named in `forge.yaml` as `${NAME}` only), runs a breach canary in the box (fail
closed), and runs the command interactively. MCP tools are default deny; tools that spend money or act on the
world can be gated with a cap, decided by the compiled escrow verifier of Forge's #91 (its model is PROVED in
Dafny), kept running as one process. The box reaches only the airlock and the hosts in `agent.egress_allow`.
With `llm` in `forge.yaml`, `claude` in the box needs NO key: it reaches the model through the airlock, which adds
the key from your shell and caps the spending per session and per day (no `--pass-env`; `airlock/README.md`).

Honest labels:

| Part | Label |
| --- | --- |
| The verifier's verdict on one gate log: cap, money chain, sequence, duplicate id, expiry (#91) | PROVED, about the Dafny model only |
| The compiled verifier as it runs, its CLI glue, the persistent process | NATIVE CHECK (compiled or hand-written, not proved) |
| The memo of its pure SHA-256 and key validation (the speed-up: about 300 ms to about 20 ms per gated call) | NATIVE CHECK; "same verdict with and without it" is a FINITE TEST, not proved |
| Policy engine, path checks, workspace filter, `nosymfollow` mount, canary, egress proxy, sync-back filter | NATIVE CHECK |
| Every run of `prove_release.sh` | FINITE TEST (one machine, listed inputs) |
| Isolation | container tier: the box shares the host kernel (on macOS, Docker Desktop's Linux VM); a kernel or runtime escape is out of scope |
| `llm` model proxy: verifier's verdict on the spending log | PROVED (Dafny model); the token estimate, the reading of the provider's usage report and the proxy are NATIVE CHECK |
| `--pass-env NAME` | the agent CAN READ that value (and can send it to the allowed hosts); use a separate, spend-capped key |
| End to end | `end_to_end=false`: tested with mocks, fake keys and the real server-filesystem, not with a real paid service |

`./prove_release.sh` runs all 8 release gates (checker tests, plugin validation and scans; hidden paths incl. a
concurrent symlink swap; gated MCP incl. crash, timeout and restart; planted breaches; diff sync-back; gate
latency; this README; the `llm` model proxy against a fake provider) and exits 0 only if all pass. Not tested: see "Not tested" in `airlock/README.md`
(no real model session in the box, no Linux or Windows host, no real paid upstream).

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

`"usedBy"` is a text check: "imported but never called" passes it. For a live check, add
`"serviceTest": {"command": ["node", "--test", "test/service.test.ts"], "cwd": "."}` to `build`. `build` and
`build --check` run the command with `FORGE_TRACE` set to a temporary file; the generated js module (below) writes
one line per process on its first call of a function of the check. The build records the number of processes that
called it; `NOT_CALLED` if none did. With `FORGE_TRACE` unset, the module does nothing extra. Only the js module is
traced (Node 22.3 or newer): an app that loads the emitted `.js` with its own loader, or the py target, is not seen.

`build --check` writes nothing and requires the emitted code on disk to equal a fresh build, so a hand
edit of emitted code gives `STALE_BUILD`. Commit the emitted code and run `build --check` in CI
(the Action does this by default when a check has a build).

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
| `SERVICE_TEST_FAILED` | The check's `serviceTest` command failed |
| `NOT_CALLED` | The `serviceTest` command passed, but no process called the built code |

Tools per target: `py` python3; `js` node and npm (bignumber.js is fetched once into
`~/.cache/forge-check`; the app needs neither, see below); `go` go and goimports; `java` javac and java; `cs` dotnet; `rs` cargo.

Honest labels: the Dafny model is PROVED. The emitted code is a NATIVE CHECK, because Dafny's compilers are
not verified. The fuzz run is a FINITE TEST. The wrapper that your app writes around the emitted code is
ordinary unproved code, so keep it thin. `examples/wallet-cap` shows all of it, with `app.py` as the app.

### JavaScript and TypeScript: one ES module, no loader

Dafny's JavaScript output is a script with no exports that calls `require('bignumber.js')`, so an app needed a
loader (`new Function`, which a strict Content Security Policy and Cloudflare Workers forbid) and bignumber.js from
npm. For the `js` target, `build` also writes, next to the emitted `<name>.js`:

- `<name>.mjs`: one ES module, made at build time from the emitted code, a pinned copy of bignumber.js 9.3.1
  (SHA-256 checked, private to the module) and real `export`s: each Dafny module, each function and datatype of
  your module by its bare name, `_dafny`, `BigNumber`, and helpers. No `new Function`, no `eval`, no file reading,
  no npm install in the app.
- `<name>.d.mts`: TypeScript declarations (Dafny values are typed `any`; the helpers are typed).

```ts
import * as P from "../verified/wallet-cap/js/wallet_cap.mjs";
const [next, outcome] = P.Spend(P.State.create_Wallet(P.toDafnyInt(cap), P.toDafnyInt(spent)), P.toDafnyInt(amount));
const result = { ok: outcome.is_Ok, spent: P.fromDafnyInt(next.dtor_spent) };   // a number, or a BigInt above 2^53
```

Helpers: `toDafnyString` / `fromDafnyString`, `toDafnyInt` / `fromDafnyInt`, `toDafnySeq` / `fromDafnySeq`,
`toDafnySet` / `fromDafnySet`, `setStringCacheLimit`. `toDafnyString` keeps up to 4,096 converted strings (two
generations, one Map lookup per hit) and returns them frozen; the runtime's own string literals use the same cache,
so equal strings are often the same object and compare at once. This cannot change a result: the Dafny runtime
never changes a seq in place, and a change to a frozen one would throw, not answer differently. Ints stay exact:
`toDafnyInt` takes a safe integer, a BigInt or a digit string, and refuses anything else.

The module is deterministic and listed in `forge-build.json`, so a hand edit gives `STALE_BUILD`. At every build
the fuzz harness also runs through its own generated module under `node --disallow-code-generation-from-strings`,
and must print exactly what the plain emitted script prints. After upgrading, run `build` once: a committed build
without the module is `STALE_BUILD`.

Measured (one machine, Node 24.3.0, warm, median of 5 passes):

| Case (one pass) | old loader (`new Function` / `Module._compile`) | generated module |
| --- | --- | --- |
| wallet-cap `Spend`, 2,000 entries x 10 calls, ints only | 6.34–6.42 ms | 6.48–6.73 ms |
| a string-heavy rule from a real app (10 limits per call), 2,000 calls | 62.6–81.9 ms | 26.7–41.3 ms |
| wallet variant with a bounded `newtype` instead of `int` (not the approved spec) | — | 0.40–0.46 ms |

Ranges are the medians of 3 runs. Same answers on every call compared (20,000 wallet calls, and every call of the app rule).
The gain comes from the string cache; code that uses only `int` is not faster, because bignumber.js does the work
(`tests/js_module/bench_wallet.mjs` reproduces the wallet row).

What still uses bignumber.js: every Dafny `int` (and `nat`, `real`, seq lengths and indices inside the emitted
code). That is Dafny's choice for exact unbounded integers and is kept. A newtype with a bound in the safe range
(for example `newtype cents = x | -0x1F_FFFF_FFFF_FFFF <= x <= 0x1F_FFFF_FFFF_FFFF`) is already emitted by Dafny as
a plain JS number, and the helpers pass it through; changing a spec to such a type is your decision, through
approval. Labels: the generated module and its helpers are UNPROVED code (tested); the emitted code inside is a
NATIVE CHECK. Compiling the Rust output to WebAssembly is a possible later option; it is not built.

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

### How good is the fuzz? Coverage and `mutate --app`

A green diff says only that the inputs found no disagreement. Two measures say how much the inputs tried:

- **`forge_check.py diff --coverage`** (off by default): which lines and branches of each watched file the run
  reached, and the name of every branch it never took (`file:line`, what was never taken, the source line).
  Python uses coverage.py if the Python that runs Forge can import it, else a standard-library tracer with the
  same counts on the example; JavaScript/TypeScript uses Node's built-in V8 coverage (`NODE_V8_COVERAGE`, no
  dependency). V8 lists a block only when it ran a different number of times than the code around it, so for JS
  the report names blocks never run but gives no branch total. Go, Rust, Java and C# are not measured.
- **`forge_check.py mutate --app`**: plants one small bug at a time (the `mutate` list: `<=` to `<`, `&&` to `||`,
  `+ 1` removed, …; for Python also `and`/`or`, `True`/`False`) in the watched files, in a temporary copy (never in
  your project), and runs that diff entry on each mutant with a fixed seed and input count. **Caught**: the diff
  report changes (for code that agrees with the proved rule: it now disagrees). **Survived**: the report is the
  same. A survivor on a line the fuzz never reached is an input gap; on a reached line it is either an input gap or
  a mutant **equivalent to the original under the spec** (harmless: no input can tell them apart). Forge cannot
  tell which; read each one. Python and JS/TS by default; `--langs all` adds Go, Rust, Java and C#, which rebuild
  the evaluator per mutant. `--max N` caps the mutants (default 40; `/forge mutate app` in Claude Code runs it with 30).

```
forge mutate --app: 8 of 21 planted bugs in the shipped code caught by the diff (13 survived, 1 not run); seed 1, 4,163 ms
  SURVIVED legacy_wallet.py:2  <= to <  in: if 0 < amount and spent + amount <= cap:  [line reached by the fuzz: equivalent, or the inputs miss the case]
  SURVIVED legacy_wallet.py:4  False to True  in: return False, spent  [line NEVER reached by the fuzz]
```

Measured on the example (one machine, macOS arm64, Python 3.10, Node 24.3; the shipped Python and TypeScript code
fixed so that it has no planted bug):

| Inputs module | `legacy_wallet.py` lines / branches | `legacy_wallet.ts` lines / blocks never run | `mutate --app` caught |
| --- | --- | --- | --- |
| the example's own (`diff/wallet_inputs.py`) | 4/4, 2/2 | 3/3, 0 | 19 of 21; both survivors in `app.py` (`sys.path.insert(0, …)` to 1, and a demo line under `__main__`): equivalent |
| a deliberately weak one (amount 1–100, spent 0–100, cap 10000, no decisions) | 3/4, 1/2: `legacy_wallet.py:2 condition never False` | 2/3, 1: `legacy_wallet.ts:3 block never run` | 8 of 21; every `<=`/`<`/`&&` mutant of the cap comparison survives |

Time cost (medians of 7 runs, 1,500 inputs): `--coverage` takes a Python diff from 156 to 241 ms with the stdlib
tracer (148 to 342 ms with coverage.py 7.16) and a TypeScript diff from 196 to 281 ms. `mutate --app` costs one
diff run per mutant: on the example 150–220 ms (Python), 210–430 ms (TypeScript), about 0.4 s (Go), 0.6 s (Rust,
warm cargo cache) and 1.4 s (Java) per mutant. Labels: both measures are test tooling (not proved); a mutation
score is a FINITE TEST of the fuzz, not of the code.

### Fuzz tiers and coverage-guided steering

The end-of-turn fuzz must stay fast; big volume belongs in CI. Three tiers, set in `.forge/forge.json` (all optional;
these are the defaults):

```json
"fuzz": {"tiers": {"local":   {"inputs": 1500,    "budget_ms": 1500,    "steer": false},
                   "ci":      {"inputs": 100000,  "budget_ms": 600000,  "steer": true},
                   "nightly": {"inputs": 1000000, "budget_ms": 3600000, "steer": true}}}
```

`diff` runs the local tier (a diff entry's own `inputs_per_run` / `budget_ms` still win there); `diff --tier ci` and
`diff --tier nightly` run the others; `--inputs` / `--budget-ms` win over every tier; `--steer` / `--no-steer` win
over the tier's steering. In the Action: `diff-tier: ci` (or `nightly` in a scheduled workflow).

**Steering** (`--steer`; py and js entries). Blind random inputs reach a narrow branch only by luck. A steered run
gets per-input coverage from the evaluator itself (Python: a tracer around the candidate calls only; JS/TS: V8
block coverage through `node:inspector`, read once per 16 inputs and per input only when the batch found something
new), keeps every input that reaches a new step of the shipped code as a seed and mostly tries small changes of the
seeds (an AFL-style corpus loop), and puts in constants read from the watched files' comparisons (`amount == 7777`
gives `amount` the values 7776, 7777, 7778: a dictionary). Steering changes which inputs are tried, never how they
are judged. It also names every line no input reached, plainly: `legacy_wallet.py:3 was never reached`. Go, Rust,
Java, C# and cmd entries are not steered (no per-input coverage) and say so.

Measured (`tests/bench_steer.py --seeds 20`, macOS arm64, Python 3.10, Node 24.3): the example with the shipped
code fixed and ONE bug planted behind `amount == 7777`, 1,500 inputs per run, seeds 1 to 20, the same input count
for every mode:

| Mode | Python: bug found | Python: `legacy_wallet.py` branches reached (median) | TS: bug found | TS: `legacy_wallet.ts` code lines reached (median) |
| --- | --- | --- | --- | --- |
| blind random (`--no-steer`) | 0 of 20 | 3 of 4 | 0 of 20 | 4 of 5 |
| corpus only (kept seeds, no constants) | 1 of 20 | 3 of 4 | 1 of 20 | 4 of 5 |
| dictionary only (constants, no seeds) | 20 of 20 | 4 of 4 | 20 of 20 | 5 of 5 |
| steered (`--steer`: both) | 20 of 20 | 4 of 4 | 20 of 20 | 5 of 5 |

On this bug the constants from the code do the work; the corpus alone adds little (1 of 20). A narrow branch
whose value is computed, not written in the code, is not reached this way.

Labels: tiers and steering are test tooling (not proved); every run is a FINITE TEST.

## CRASH-CHECK: `/forge crash-fuzz` (no spec)

`/forge crash-fuzz` (or `python3 cli/forge_crash.py --root .`) needs no spec and no `forge.json`. It finds the
functions where outside data enters your code, calls each with messy JSON, and lists every error that escapes one,
with the smallest input that still gives it:

```
CRASH-CHECK: 3 handlers crash with unhandled errors: SyntaxError on a missing value (undefined), empty text; TypeError on a missing value (undefined), null
  4 boundary function(s), 199 calls, seed 1, 973 ms. CRASH-CHECK only: it shows crashes; it proves nothing, and 0 crashes is not a proof of no crash.
  ✗ js/handlers.ts:8 createOrder (express): 1 crash(es) in 34 calls
      CRASH TypeError at js/handlers.ts:9 on item = undefined: Cannot read properties of undefined (reading 'quantity')
        same crash also on: item = null, item = removed, whole input = undefined, whole input = null, whole input = empty-text, whole input = not-json-text
        repro: {"item": {"__undefined__": true}}
  ✗ js/handlers.ts:14 POST (fetch): 4 crash(es) in 33 calls
      CRASH TypeError at js/handlers.ts:17 on user = null: Cannot read properties of null (reading 'name')
        same crash also on: user = number, user = boolean, user = empty-text, user = lone-surrogate, user = long-text, user = array
        repro: {"user": null}
      CRASH SyntaxError at js/handlers.ts:15 on whole input = undefined: Unexpected end of JSON input
        repro: {"__undefined__": true}
  ...
  ✓ js/handlers.ts:27 updateLimit (express): 0 crashes in 120 calls
  skipped 1 function(s):
    js/handlers.ts:35 normalizeName: private helper: not exported, so no caller outside its module can pass it data; a crash there shows up through the boundary function that calls it
```

- **Which functions.** JS/TS: exported functions that look like a boundary: request/response or event parameters
  (Express/Fastify `(req, res)`, fetch-style `(request)` and `GET`/`POST` route handlers, Lambda `(event, context)`),
  handler/webhook/parse-like names, package entry points (`main`/`exports`/`bin`), files under `routes/`, `api/`,
  `handlers/`, `webhooks/`. Python: route/view decorators (`@app.route`, `@router.post`, `@api_view`,
  `@csrf_exempt`, ...), `__all__`, handler/webhook/parse-like names, a first parameter `request` or `event`.
  **Skipped, with the reason said:** private helpers (not exported, or `_name`: only a boundary function can pass
  them data, and the boundary function is what is called), other exports (internal module code), and functions that
  cannot be called directly: route handlers that are not exported, factories (`createHandler(deps)`), Flask views
  with no parameters, FastAPI handlers with typed parameters (the framework validates first).
  `--target FILE:FUNCTION` (or `.forge/crash-fuzz.json` `{"targets": [...]}`) checks exactly the functions you name;
  `--include-private` adds private Python helpers.
- **Which inputs.** The input shape is inferred from the fields the function reads (`req.body.item.quantity`,
  `payload["user"]`, destructuring, `JSON.parse(raw)`); then every field, and the whole input, gets each messy
  kind: undefined (JS), null, NaN, a number, a boolean, `""`, very long text, a lone surrogate, `[]`, `{}`, the
  number as text, text that is not JSON, removed; then random pairs. 120 calls per function by default
  (`--inputs`).
- **What counts.** A runtime error that escapes (TypeError, SyntaxError, KeyError, AttributeError, ...; a rejected
  promise or an unhandled rejection too) is a CRASH; a call with no answer in 1 s is a HANG. An error the code throws
  on purpose (`throw`/`raise` on that line, or a non-built-in error class) is listed apart: check that your
  framework answers 4xx for it. If the inferred input itself crashes, the shape guess is probably wrong: the
  function is NOT judged and none of its results count.
- Exit 1 when something crashed, 0 when not. **CRASH-CHECK, never PROVED:** 0 crashes is a finite test, not a proof.

Measured on `tests/crash_fixture/` (seed 1): per language, the three planted crashes (a missing field, a null
deref, `JSON.parse` / `json.loads` of a value that is not text) are found and shrunk (`{"item": undefined}`,
`{"user": null}`, `{"payload": {"user": null}}`); the fail-closed handlers have 0 crashes in 120 calls each; the
private helpers that throw on undefined / None are not reported (with `--include-private` the Python one is, so
the skip is a choice, not a blind spot). About 1 s for 8 functions.

## Bypass: code that goes around a proved rule

A proof is only as strong as the guarantee that nothing goes around it. On a real app, a proved read rule decided that
one field counts only on one kind of row, but two other functions still trusted that field on any row. A person
found that by hand. `bypass` finds such places.

Add `"watchedFields"` to a check: the texts that name what the rule decides on (field names such as `body.owner` or
`groupId`, a comparison, function names). A text between slashes is a regular expression
(`"/spent\\s*\\+\\s*amount[^;{]*<=?\\s*cap/"`); anything else is literal. `/forge:bypass` proposes them from the approved
spec and decision record. Then:

```
python3 cli/forge_check.py bypass [--root DIR] [--json]
```

searches the project's files (git's tracked and untracked-but-not-ignored files; without git, a walk) for each term.
It skips the rule's own files (spec, implementation and its includes, your answers, decision record, fuzz harness,
emitted code, diff adapters and inputs, `INVARIANTS.md`), vendored and build folders (`node_modules`, `dist`,
`build`, `verified`, `vendor`, `target`, `.forge`, …), documentation and lockfiles. It reports every remaining line:
file:line, the term, the line, and the enclosing function when a cheap heuristic finds it (Python, TS/JS, Go, Java,
C#, Rust). `/forge:bypass` explains each place and asks you: route it through the proved rule, it is fine (record
why), or make it part of a new rule. A "fine" answer is stored in the check's `"allowedBypasses"` list as
`{file, line_text_sha256, note}`, so an edited line shows up again. Both lists are in `forge.json`, so you approve them
like any other change: every allowed bypass is approved by a person.

**A hard gate.** For a check with `watchedFields`, `check` gives `UNAPPROVED_BYPASS` (exit 1) when its proof passes
but a line outside the rule matches and is not in its approved `allowedBypasses`; the detail lists the lines. A failed
proof (or any other verdict) still wins. A check without `watchedFields` behaves as before.

**Specific terms.** Because it is a gate, a term must name the forms that decide, not every mention:
`.body.owner` (a property access), `/\\bgroupId\\s*(===|!==|==|!=)/` (a comparison), not a bare `groupId`.
`bypass --count --rule NAME --set-terms TERM…` previews the number of lines per term and writes nothing.

| Bypass verdict | Meaning | Exit |
| --- | --- | --- |
| `BYPASS_CLEAR` | No unreviewed line outside the rule uses its watched fields | 0 |
| `BYPASS_FOUND` | At least one such line | 1 |
| `NO_SCOPE_TERMS` | No check has `watchedFields`: nothing was searched (not a pass) | 0 |
| `UNAPPROVED_BYPASS` (a `check` verdict) | The proof passed, but an unallowed line outside the rule matches | 1 |

`check` runs the same search: the unreviewed places also go under `"outside"` in its JSON and on one line of its
report (`3 places decide with body.owner outside policy-view: src/policy.ts:8 canCancel, …`).
`/forge approve yes` prints the count; bare `/forge` offers `/forge:bypass` for a check without watched fields.

Measured on that app (a copy, 205 files searched, about 0.3 s): the two bare field names match 290 lines in 25
files (275 of them for the id field): too many for a gate. The specific terms (the property access with its dot, and
a comparison on the id field from each side, the forms shown above) match 19 lines in 10 files, and still name the
three places found by hand.

**Decision reads only (AST), in TypeScript / JavaScript and Python.** In these files a line counts only where the
watched field is READ in a decision. Each hit names its kind: `if-condition`, `early-return-guard`, `loop-condition`,
`comparison` (`===`, `!==`, `==`, `!=`, `<`, `<=`, `>`, `>=`, `instanceof`), `in`, `includes` (also `.has`,
`.indexOf`, `.startsWith`, ...), `ternary`, `switch`, `case`, `logical-guard` (the left side of `&&` / `||`, not an
`x || ''` default), `filter-predicate` / `find-predicate` / `predicate` (`filter`, `find`, `some`, `every`; Python
comprehension `if`, `filter`, `any`, `all`), `assert`, `sql-where` (a parameter of a query whose SQL text has a
`WHERE`), and `alias` (`const v = row.f` where `v` is read in a decision in the same function). Type and interface
declarations, type annotations, imports, comments, string literals, plain assignment, object construction and
logging are not hits. TS / JS files are parsed with the TypeScript compiler API (TypeScript 5.6.3, downloaded once
into the cache and checked against its pinned SHA-256, as Dafny is; it only parses, it never runs the app); Python
files with `ast`. Every other file (Go, Rust, Java, C#, data files) keeps the text search per line, labelled
`text search, not AST`. A TS / JS / Python file that does not parse, or a machine without Node or without the
TypeScript package (offline, `FORGE_NO_DOWNLOAD=1`), falls back to the text search for that file and the report
says `FALLBACK to text search, not AST` with the reason: fail closed, never skipped. An approved `allowedBypasses`
entry stays allowed by its line hash. `bypass --json` lists the text hits that are not decision reads under
`"not_decisions"`.

Measured on a copy of the same app with the bare terms (290 text lines): 76 hits remain, 46 decision reads found by
the AST (all 46 checked by hand: each is a decision site) and 30 lines in data files (text search, not AST); the
three places found by hand are still found. Of the 214 dropped lines, 85 were checked by hand: 81 are not decisions;
4 feed a decision that the search cannot see (2 checks through a computed key, 2 inputs to a policy engine).

Limits: a syntactic search, not a data-flow analysis (a FINITE TEST of the listed terms). It misses a value passed
to another function or returned to a caller that decides, dynamic access (`body[key]`), and code that decides the
same question under other names. The alias rule is name-based. The function name is a heuristic on indentation.

## INVARIANTS.md: every approved rule in one place

`/forge invariants` (and every `/forge approve yes`) writes `INVARIANTS.md`: one table of all approved rules, then
one section per rule with the owner's own words, the guarantees in plain words (the spec's numbered clause
comments), the decisions, what the rule does not guarantee, the code it covers, the languages it is compiled to
and the shipped code it is fuzzed against, plus the trust base every rule rests on. It is generated only from the
approved files, so it cannot drift from them. The Action checks it by default: a pull request fails when
the file is out of date, so any change to a rule shows up in review as a readable diff. See
`examples/wallet-cap/INVARIANTS.md`.

## Signed approvals: only a person with a hardware key can approve (optional)

Without this, an approval is a file. An agent with a shell can write `.forge/approved.json` itself. With signed
approvals, `check` says `VERIFIED` only for an approval that a listed key signed, and the list of keys is read from
a place the agent cannot write.

Set up once (the owner, in a terminal; do not let an agent do it):

```
ssh-keygen -t ed25519-sk -C forge-approval -f ~/.ssh/forge_approval_sk   # FIDO2 key, for example a YubiKey; touch it
mkdir -p ~/.config/forge
echo "you@example.com namespaces=\"forge-approval,forge-call-approval\" $(cut -d' ' -f1,2 ~/.ssh/forge_approval_sk.pub)" \
  | tee .forge/allowed_signers > ~/.config/forge/allowed_signers
export FORGE_APPROVAL_KEY=~/.ssh/forge_approval_sk FORGE_ALLOWED_SIGNERS=~/.config/forge/allowed_signers
```

On macOS the system `ssh-keygen` has no FIDO2 support (measured on macOS 27.0.1: "No FIDO SecurityKeyProvider
specified"). Use Homebrew's OpenSSH (`brew install openssh`; set `FORGE_SSH_KEYGEN=/opt/homebrew/bin/ssh-keygen`
or put it first in PATH), or try Apple's provider: `SSH_SK_PROVIDER=/usr/lib/ssh-keychain.dylib ssh-keygen -t
ecdsa-sk ...` (the library exports the security-key API and refers to LocalAuthentication; whether it keeps the key
in the Secure Enclave behind Touch ID is NOT tested here).

Then in `.forge/forge.json`:

```json
"require_signed_approval": true,
"signed_files": ["forge.yaml", "INVARIANTS.md"],
"signer_key_types": ["sk-ssh-ed25519@openssh.com", "sk-ecdsa-sha2-nistp256@openssh.com"]
```

and approve with `/forge approve yes --hardware` (or `forge_check.py approve --hardware`). The key blinks; touch it.

- **What is signed:** the canonical content of `approved.json` (sorted keys, no spaces, ASCII), with
  `ssh-keygen -Y sign -n forge-approval`, into `.forge/approved.json.sig`. In signed mode `approved.json` also holds
  the SHA-256 of `.forge/allowed_signers` and of every `signed_files` entry (byte for byte; `INVARIANTS.md` is
  generated at approval, then covered).
- **What is verified:** `check` runs `ssh-keygen -Y find-principals` and `-Y verify` against a **trust anchor that is
  not the working tree**: `--allowed-signers FILE` or `FORGE_ALLOWED_SIGNERS` (a file outside the project; one inside
  is refused), else `.forge/allowed_signers` at the `--base` git ref (CI on a pull request: the protected branch).
  Then every covered file must match its signed hash. Anything else is `APPROVAL_UNSIGNED`, **exit 2**: no anchor,
  missing or broken `.sig`, a key that is not in the anchor, a key type outside `signer_key_types`, or any covered
  file changed. The requirement holds if the working `forge.json`, **or the `--base` ref's `forge.json`**, or an
  anchor on the command line asks for it, so a pull request cannot switch it off.
- **Keys:** a FIDO2 `sk-ssh-ed25519@openssh.com` key (OpenSSH checks the touch flag in every signature unless the
  signer line says `no-touch-required`; add `verify-required` to also demand the PIN). On macOS, a Secure Enclave key
  in an ssh-agent (for example the open-source Secretive app) uses the same path (`ssh-keygen -Y sign -f <its .pub>`
  with `SSH_AUTH_SOCK` set; NOT tested here). Such a key is an ordinary `ecdsa-sha2-nistp256` key to the verifier:
  the signature does not show that a person was present, so with `signer_key_types` set to `sk-` types only, it is
  refused. A PIN prompt needs a terminal: use `forge_check.py approve --hardware` there, not the mod.
- **CI:** the Action runs `check --base <base>` on pull requests, so the anchor is the base branch. A push run has no
  base: pass `FORGE_ALLOWED_SIGNERS`, or run `check --base HEAD` on the protected branch. `build` and `mutate` do not
  check the signature (they run after `check`). To turn signing on in a repository with CI, merge
  `.forge/allowed_signers` first (one pull request), then the signed approval with `"require_signed_approval": true`
  (a second one): the second is checked against the keys the first put on the base branch.

Exit gate (`tests/run_signed_approval.py`, also run by `tests/run_tests.py`): a rogue agent with full write access
to the project (`tests/rogue_agent.py`) rewrites the spec, `forge.yaml`, `INVARIANTS.md`, `approved.json` and the
`.sig`, deletes or replaces the `.sig`, switches the requirement off, and adds its own key to `allowed_signers`
and signs with it through the checker's own `approve --hardware`. Every attack gives `APPROVAL_UNSIGNED`, exit 2,
with the anchor outside the project and with the anchor at `--base`; the owner's signed approval gives `VERIFIED`,
exit 0. LABEL: FINITE TEST with **software** ed25519 keys made in a temp folder at test time (no hardware in CI, no
key in the repository); a FIDO2 key goes through the same `ssh-keygen -Y` code path, but its touch is not tested.

Limits (what S1 still trusts): OpenSSH's `ssh-keygen` (NATIVE CHECK, not proved); the anchor (a file outside the
project that the same OS user can still write, or branch protection on the base branch); the owner's key and how it
is kept (only `sk-` keys show a touch); the person reading what they sign. With no anchor and the requirement
switched off in the working tree, nothing is signed (the test measures this too). A signature has no date: an old
signed approval of old files verifies again (rollback); on a pull request `APPROVAL_CHANGED` still shows it.

## Use it in CI (GitHub Action)

The same checker runs in CI with no configuration. Commit `.forge/` (and the emitted code and `INVARIANTS.md`), then
copy [`examples/workflows/forge.yml`](examples/workflows/forge.yml) to `.github/workflows/forge.yml`:

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
| `check` | always | the proof (`FAILED`), `NOT_APPROVED`, `SPEC_CHANGED`, `CONFIG_CHANGED`, `BINDING_BROKEN`, `UNSOUND_CONSTRUCT` (included files too), `EXAMPLES_FAILED`, `UNAPPROVED_BYPASS` |
| approvals against the base | when the event has a base: the pull request's base branch, the merge group's base commit, or the commit before a push | `APPROVAL_CHANGED` |
| `build --check` | when a check has a `build` entry | `STALE_BUILD`, `APP_SYNTAX_ERROR`, `NOT_CALLED`, a target's fuzz |
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
`fail`) and `report` (a JSON file with every verdict). More in [docs/ci.md](docs/ci.md).

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
### Messy inputs: the wrapper must fail closed, never throw

The wrapper that turns your app's values (often JSON) into the proved code's values is not proved. Runtime JSON
does not follow your types: a field can be `undefined`, `null`, a number where text was expected, or missing. On a
real app, an id field set to `undefined` reached such a wrapper and it threw instead of answering "forbid". The plain
fuzz never sent that value, so it did not see it.

If the adapter (any of the six languages) exports a raw-value wrapper, the fuzz also sends **messy variants**
of its inputs to it: each field (nested objects and lists too, bounded) is in turn made `undefined` (JavaScript
only), `null`, a number, a boolean, `""`, a lone surrogate, a 20,000-character text, `[]`, `{}`, the number as
text (`"5"` for `5`), or removed. Big integers in the other fields stay exact.

```js
// diff/<check>_diff.mjs (Python: wrapper / wrappers, fail_closed, returning None for "the rule takes it")
export const wrapper = (i) => myRealWrapper(i)        // the app's REAL wrapper, raw values, no conversion here
// or: export const wrappers = { name: fn, ... }
export function failClosed(i) {                        // the spec's fail-closed answer for a value the
  return typeof i.groupId === 'string' ? undefined     // proved rule does not take; undefined: the rule
    : { effect: 'forbid', rule: 'other_group' }        // takes it, so the proved rule's answer is expected
}
```

The compiled languages take the raw JSON in their own types: Go `json.RawMessage` (`var wrappers =
map[string]func(json.RawMessage) any{…}`, `func failClosed(raw json.RawMessage) any`, nil: the rule takes it),
Rust `serde_json::Value` (`pub fn wrappers()`, `pub fn fail_closed(raw: &Value) -> Option<Value>`), Java the
evaluator's parsed value (Map / List / String / Boolean / BigInteger / BigDecimal / null; `wrappers()`,
`failClosed(Object)`), C# `JsonElement` (`Wrappers()`, `FailClosed(JsonElement)`). `undefined` is sent only to
JavaScript. Rust does not get the lone surrogate: serde_json rejects it while it parses, before any wrapper could run
(listed under `skipped_kinds`). A panic (Go, Rust) or an exception (Java, C#) is `THROWS_ON_MESSY_INPUT`.

Rules: a throw from the wrapper on any messy input is always a disagreement of kind `THROWS_ON_MESSY_INPUT`.
Otherwise its answer must equal `failClosed(input)`, or the proved rule's answer where `failClosed` returns
`undefined`. Messy disagreements are grouped, shrunk (the messy field and its kind stay; the rest of the input
shrinks) and saved like the others. `"messy": false` on a diff entry turns them off; an inputs module can list
fields to leave alone in `messy_skip = ['archived', 'limits[].label']`. A messy input that has no fail-closed
answer and that the proved rule cannot take is counted as "not judged" (only a throw is reported for it).

```
  NEW wrapper-throws: input {"cap": 0, "spent": 0, "amount": {"__undefined__": true}} → expected (fail-closed or proved) {"ok": false, "spent": null}, wrapper-throws says {"__throws__": "TypeError"}
    messy field amount = undefined: THROWS_ON_MESSY_INPUT (TypeError): the wrapper crashed instead of failing closed
```

The example has three planted wrappers per language (`examples/wallet-cap/messy_wrappers.ts`, `.py`, `.rs`,
`messygo/`, `messyjava/`, `messycs/`; adapters `diff/wallet_messy.*`): one that throws or panics on a null, missing or
wrong-typed `amount`, one that turns `"5"` into `5`, and a correct one that fails closed (the negative control). They
are not in the example's `forge.json`; the tests add them (C# in `tests/run_cs_diff.py`, in CI).

Time, measured on the example (one machine, warm, 1,500 inputs, median of 3 runs): the three wrappers with messy
off 324 ms (JS) and 216 ms (Python); with messy on (about 1,530 messy inputs, including shrinking the planted bugs)
1,192 ms and 1,236 ms before M1.1b. After M1.1b (the messy step no longer writes the 20,000-character text once per
field and kind; same inputs for a seed), messy on, entry time: JS 473 ms, Python 393 ms, Go 429 ms, Rust 467 ms,
Java 519 ms; the whole `diff` command 0.49–0.62 s. An entry without a wrapper does not change. With the default time budget (1.5 s per entry)
messy inputs share the budget, so fewer plain inputs run (584 instead of 1,500 for JS in one measured run).

Labels: the messy values are a fixed list, not every possible value; the fail-closed answer is the adapter's (the
person's) choice, not proved; a run is a FINITE TEST.

### State machines: trace fuzz (many events per case)

Some bugs need two or more events: a retry applied twice, a field lost when the state is saved and loaded again, an
expiry check that moves after a pause. One call per input cannot find them. For a model of the form
`Step(State, Event, now) -> Ok(State) | Err(Reject)`, add a `"trace"` block to a `js` or `py` diff entry:

```json
{ "name": "js", "lang": "js", "inputs": "diff/subscription_events.py", "adapter": "diff/subscription_trace.mjs",
  "trace": { "count": 2000, "budget_ms": 10000, "max_len": 10,
             "retry": 0.15, "replay": 0.08, "reorder": 0.08, "restart": 0.06 } }
```

- The events module (Python, shared by every language) defines `event(rng, ctx)` and optionally `advance(rng)`
  (clock ticks before an event), `start_time(rng)`, `event_in_domain(e)` and `trace_in_domain(steps)`.
- The adapter exports `trace = { reference, candidates: { name: machine } }`; a machine has `init()`,
  `step(state, event, now)` returning `{ ok: state }` or `{ reject: reason }`, `view(state)` (the JSON compared after
  each step) and optionally `save(state)` / `load(saved)` (what a restart goes through). A rejected step keeps the
  state object it got, so code that changes the state before it rejects is found.
- The core (`cli/forge_trace.py`) generates seeded traces with the clock moving forward and injects faults: `retry`
  (the same event again), `replay` (an older event again), `reorder` (two events swapped) and `restart` (save, then
  load). It runs the model and every candidate over the same trace, compares verdict and state after every step,
  and shrinks a failing trace by delta debugging: it drops chunks of events, then makes times and event fields
  simpler, then drops again. Each failure prints its minimal trace, the step where it differs and an `again:`
  command (same seed, `--inputs` = trace index + 1) that finds it again. Failing traces are saved and replay first.
- `--inputs` sets the number of traces and `--budget-ms` the time budget for a run.

Example: `examples/subscription-machine` (a subscription with Start, Renew, Pause, Resume and Cancel, and expiry by
time). Its Dafny model is proved against `spec/SubscriptionSpec.dfy` (clauses M1–M7). The correct shipped code is
`app/subscription.ts` and `.py`. Four planted copies (`app/subscription_planted.*`) each have one bug that needs two or
more events. Measured on one machine (seed 7, 2,000 traces), each bug is caught in JS and Python, and each shrinks to
its minimal trace:

| Planted bug | Shrunk trace (steps) |
| --- | --- |
| `replay-off-by-one`: duplicate check `<` instead of `<=` (an immediate retry applies again) | 2 |
| `pause-expiry`: after a Resume the expiry check is `>` instead of `>=` | 4 (Start, Pause, Resume, Pause at the expiry tick) |
| `restart-drops-id`: `save()` leaves out `lastId` | 2 (Start, restart) |
| `reject-leaks`: Renew changes the state before it checks expiry; the rejected step keeps the change | 2 |

The correct code had 0 disagreements over 50,000 traces (about 275,000 steps) in each language. Wall time for
50,000 traces, correct code only, one machine: JS 11.6–21.4 s, Python 11.2–17.0 s (five runs, one in a clean clone; the highest JS time
was measured while other heavy work ran on the machine). Negative controls in `tests/run_tests.py`: with `max_len: 1`, no
planted bug is found; with `restart: 0`, the restart bug is not found.

Covered: JavaScript/TypeScript and Python evaluators, and any `"cmd"` program that speaks the trace protocol (see
the top of `cli/forge_trace.py`). Not covered yet: the built Go, Rust, Java and C# evaluators. Labels: the traces
are random and bounded (`max_len`), so a run is a FINITE TEST, not a proof about every sequence.

See `PLAN.md` for what is built and what is not.

## Limits

- Without the airlock this is a feedback loop, not a security boundary. The mod blocks Claude's Edit and Write tools on
  approved specs and on `.forge/approved.json`, but a shell command can still change them. Signed approvals (above) make
  such a change fail `check` with `APPROVAL_UNSIGNED`, if the trust anchor is outside the project.
- A spec covers only what it says. Files without a spec are not checked.
- Dafny runs can take seconds to minutes.

## License

MIT. See `LICENSE`. Also: `CHANGELOG.md`, `SECURITY.md`, `CONTRIBUTING.md`, `PLAN.md`, `TRIAL.md`.

## What a green check covers (and what it does not)

- **Status line:** `model PROVED` (the Dafny model meets the approved spec) · `fuzzed N cases, wrapper NOT proved` (a finite test of the code people run) · bypass count, once a rule names its fields · `records current` or `decision record may be stale`.
- **Stale decision records:** at approval Forge remembers the code a decision record names (file paths, and functions in backticks defined in at most two non-test files). When that code changes, `check` prints `DECISION_NOTE_STALE` (a warning; the proof approval stands).
- **Notes do not need re-approval:** an approval hashes the binding part of a decision record (the rule in words, the defaults, the choices). Sections headed Out of scope, Notes, Background or History can be fixed freely. Approvals written before 0.13 hold the full-file hash; approve once more to get this.
- **Next rule:** `check` names the first Out-of-scope item that points at code in your repo, as the next `/forge:add`.
- **Build sanity step:** `build` first parses every app file a check touches and every JS/TS file that imports them, directly or not (`APP_SYNTAX_ERROR`). `check` reports the same as a warning. Optional `"typecheck": ["npx", "--no-install", "tsc", "--noEmit"]` in a check's build entry runs a real type check. Parsing is not proving: it catches a missing brace, not a wrong type.
- **Shorter approval screen:** `/forge approve` shows a digest: every spec clause in full (predicate and function bodies included; lemma and method bodies left out), the answers by id, the whole decision record, and files that are already approved and unchanged as one line. `/forge approve --full` prints every file. The approval always covers the complete files by hash.
