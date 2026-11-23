---
description: Forge · set up /forge build (languages, fuzz harness, wiring into your app)
argument-hint: "[languages, e.g. py js]"
---
Forge rule: the person approves a short spec once; after that, code may change only if Dafny proves it still meets the spec. Use short sentences. Never write `.forge/approved.json`, never run `forge_check.py approve`, and never type `/forge approve yes` for the person: approval is their act. The checker is `python3 "${CLAUDE_PLUGIN_ROOT}/cli/forge_check.py"`.

Set up `build` for the checks in `.forge/forge.json` that have no "build" entry. Languages the person named: "$ARGUMENTS". If empty, ask with AskUserQuestion which languages their app needs (py, js, go, java, cs, rs; several allowed).

1. Write a fuzz harness in Dafny for each check: a file with a module whose name differs from the file name and that has `method Main()`. It calls the proved method on boundary values (0, 1, each limit and its neighbours, 2^31, 2^32, 2^53, 2^63, 2^64) and about 2000 pseudo-random inputs from a linear congruential generator, repeats each ensures clause as an `expect`, and prints one line per case.
2. Add to the check: "build": {"targets": [...], "out": "verified", "fuzz": "<harness>"} (no "usedBy" until the app is switched over).
3. Write a thin wrapper in the app's language that converts plain values to the generated types, as a NEW file next to the app code. The wrapper must NEVER throw on the app's values: runtime JSON breaks types. Any id or text field that is not text (undefined, null, a number, a boolean, a list, an object, missing) gets the spec's fail-closed answer (the forbidding / refusing answer; ask the person which one if the spec does not say), not an exception; numbers that are not whole numbers, or text such as "5" where a number is expected, are refused too, never coerced. Templates (adapt names and the fail-closed answer):

   ```ts
   // THIS FILE IS NOT PROVED. It converts raw values; it never throws on them.
   const FAIL_CLOSED: Decision = { effect: "forbid", rule: "other_household" };
   const text = (x: unknown): x is string => typeof x === "string";
   export function provedMayRead(viewer: unknown, entry: unknown): Decision {
     const v = (viewer ?? {}) as Record<string, unknown>, e = (entry ?? {}) as Record<string, unknown>;
     if (!text(v.actorId) || !text(v.householdId) || !text(e.householdId)) return FAIL_CLOSED;
     try { return fromDafny(G.__default.MayRead(toViewer(v), toEntry(e))); }
     catch { return FAIL_CLOSED; } // last line of defence: a crash becomes a refusal, never a permit
   }
   ```

   ```python
   # THIS FILE IS NOT PROVED. It converts raw values; it never raises on them.
   FAIL_CLOSED = {'effect': 'forbid', 'rule': 'other_household'}
   def proved_may_read(viewer, entry):
       v = viewer if isinstance(viewer, dict) else {}
       e = entry if isinstance(entry, dict) else {}
       if not all(isinstance(x, str) for x in (v.get('actorId'), v.get('householdId'), e.get('householdId'))):
           return dict(FAIL_CLOSED)
       return from_dafny(G.default__.MayRead(to_viewer(v), to_entry(e)))
   # whole numbers: type(x) is int (bool is not one); never int("5")
   ```

   Then add the wrapper to the check's fuzz as a messy candidate (`/forge fuzz`, step 4: `wrapper` and `failClosed` in the adapter, messy on). Do NOT change any existing app file and do NOT add "usedBy" yet: the app keeps running its own code, and the fuzz compares it with the proved rule. Say plainly that the wrapper is unproved. Tell the person the switch is a separate step they can ask for later ("switch my app to the proved rule"): then change the call site, add "usedBy", and they approve again.
4. Tell the person that forge.json changed, so they run `/forge approve`, then `/forge approve yes`, then `/forge build`.
