---
description: Forge · set up /forge build (languages, fuzz harness, wiring into your app)
argument-hint: "[languages, e.g. py js]"
---
Forge rule: the person approves a short spec once; after that, code may change only if Dafny proves it still meets the spec. Use short sentences. Never write `.forge/approved.json`, never run `forge_check.py approve`, and never type `/forge approve yes` for the person: approval is their act. The checker is `python3 "${CLAUDE_PLUGIN_ROOT}/cli/forge_check.py"`.

Set up `build` for the checks in `.forge/forge.json` that have no "build" entry. Languages the person named: "$ARGUMENTS". If empty, ask with AskUserQuestion which languages their app needs (py, js, go, java, cs, rs; several allowed).

1. Write a fuzz harness in Dafny for each check: a file with a module whose name differs from the file name and that has `method Main()`. It calls the proved method on boundary values (0, 1, each limit and its neighbours, 2^31, 2^32, 2^53, 2^63, 2^64) and about 2000 pseudo-random inputs from a linear congruential generator, repeats each ensures clause as an `expect`, and prints one line per case.
2. Add to the check: "build": {"targets": [...], "out": "verified", "fuzz": "<harness>"} (no "usedBy" until the app is switched over).
3. Write a thin wrapper in the app's language that converts plain values to the generated types, as a NEW file next to the app code. Do NOT change any existing app file and do NOT add "usedBy" yet: the app keeps running its own code, and the fuzz compares it with the proved rule. Say plainly that the wrapper is unproved. Tell the person the switch is a separate step they can ask for later ("switch my app to the proved rule"): then change the call site, add "usedBy", and they approve again.
   For JavaScript or TypeScript (`js`): do NOT write a loader. Never use `new Function`, `eval`, `vm`, `readFileSync` of the emitted code or `require('bignumber.js')`. `/forge build` writes one ES module, `<out>/<check>/js/<name>.mjs` (with `<name>.d.mts`), that has real exports, its own pinned copy of bignumber.js, and the helpers `toDafnyString`, `fromDafnyString`, `toDafnyInt`, `fromDafnyInt`, `toDafnySeq`, `fromDafnySeq`, `toDafnySet`, `fromDafnySet`. Import it and convert with those helpers. Template (`<check>` is the check name, `<name>` the name with `-` as `_`; change the path to fit where the file is):
   ```ts
   /** THIS FILE IS NOT PROVED: it only converts values. The rule runs in the module that `/forge build` writes. */
   import * as P from "../verified/<check>/js/<name>.mjs";
   export function provedRule(id: string, tags: string[], amount: number | bigint): boolean {
     return P.Rule(P.toDafnyString(id), P.toDafnySet(tags, P.toDafnyString), P.toDafnyInt(amount)) === true;
   }
   ```
   Results: `P.fromDafnyString(x)`, `P.fromDafnyInt(x)` (a number when it is a safe integer, else a BigInt), `P.fromDafnySeq(x, each)`; a datatype value has `is_<Ctor>` and `dtor_<field>`, and is built with `P.<Type>.create_<Ctor>(...)`. A function that is not exported by its bare name (shown in a comment at the end of the module) is `P.<Module>.__default.<Name>`. Never keep a Dafny value from a call in a place that the app changes. Run the wrapper once under `node --disallow-code-generation-from-strings` to show it works where `new Function` is forbidden (strict CSP, Cloudflare Workers).
   If the app has a test that goes through the app's own entry point to the wrapper (a service or end-to-end test), propose `"serviceTest": {"command": [...], "cwd": "."}` in the build entry, added together with "usedBy" at the switch: `build` then fails with NOT_CALLED when that test never calls the proved code.
4. Tell the person that forge.json changed, so they run `/forge approve`, then `/forge approve yes`, then `/forge build`.
