# Build: from proof to code your app runs

How `/forge build` compiles the proved rule for your languages, and the generated JavaScript module.

[Back to the README](../README.md)

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
| `FUZZ_BUILD_FAILED` | A target's compiler failed on the fuzz harness (a toolchain problem, not a wrong result). This verdict and `TOOLCHAIN_MISSING` give way to a later finding about the code, such as `STALE_BUILD`; every failure is listed in the notes |
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

## JavaScript and TypeScript: one ES module, no loader

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
