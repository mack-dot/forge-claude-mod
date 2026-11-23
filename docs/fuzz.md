# Fuzz: your shipped code against the proved rule

The differential fuzz, how to judge it (coverage and `mutate --app`), and messy inputs for wrappers.

[Back to the README](../README.md)

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

## How good is the fuzz? Coverage and `mutate --app`

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

## Messy inputs: the wrapper must fail closed, never throw

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

See `PLAN.md` for what is built and what is not.
