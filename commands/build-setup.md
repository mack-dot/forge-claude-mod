---
description: Forge · set up /forge build (languages, fuzz harness, wiring into your app)
argument-hint: "[languages, e.g. py js]"
---
Forge rule: the person approves a short spec once; after that, code may change only if Dafny proves it still meets the spec. Use short sentences. Never write `.forge/approved.json`, never run `forge_check.py approve`, and never type `/forge approve yes` for the person: approval is their act. The checker is `python3 "${CLAUDE_PLUGIN_ROOT}/cli/forge_check.py"`.

Set up `build` for the checks in `.forge/forge.json` that have no "build" entry. Languages the person named: "$ARGUMENTS". If empty, ask with AskUserQuestion which languages their app needs (py, js, go, java, cs, rs; several allowed).

1. Write a fuzz harness in Dafny for each check: a file with a module whose name differs from the file name and that has `method Main()`. It calls the proved method on boundary values (0, 1, each limit and its neighbours, 2^31, 2^32, 2^53, 2^63, 2^64) and about 2000 pseudo-random inputs from a linear congruential generator, repeats each ensures clause as an `expect`, and prints one line per case.
2. Add to the check: "build": {"targets": [...], "out": "verified", "fuzz": "<harness>"} (no "usedBy" until the app is switched over).
3. Write a thin wrapper in the app's language that converts plain values to the generated types, as a NEW file next to the app code. Do NOT change any existing app file and do NOT add "usedBy" yet: the app keeps running its own code, and the fuzz compares it with the proved rule. Say plainly that the wrapper is unproved. Tell the person the switch is a separate step they can ask for later ("switch my app to the proved rule"): then change the call site, add "usedBy", and they approve again.
4. Tell the person that forge.json changed, so they run `/forge approve`, then `/forge approve yes`, then `/forge build`.
