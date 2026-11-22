---
description: Forge · set up the differential fuzz: your real code against the proved rule
argument-hint: "[which function, e.g. src/keyword.ts:mayShow]"
---
Forge rule: never write `.forge/approved.json` and never approve for the person. The checker is `python3 "${CLAUDE_PLUGIN_ROOT}/cli/forge_check.py"`.

Set up `diff` for a check in `.forge/forge.json`. It runs the person's real code and the compiled proved rule on the same random inputs and reports every disagreement. Function the person named: "$ARGUMENTS".

1. The check needs a "build" entry and a fresh build (`/forge build`). If it has none, say so and point to `/forge:build-setup`.
2. Find the code under test: the shipped function that implements this rule today (for example the original in `src/`, kept as it is), and the wrapper around the compiled proved code, if there is one. Ask the person if it is not clear which ones. Never change the code under test to make it agree.
3. Write ONE adapter per language (`diff/<check>_diff.mjs` for JavaScript or TypeScript, `diff/<check>_diff.py` for Python):
   - JavaScript: `export function generate(rng)`, `export function reference(input)`, `export const candidates = { name: input => output }`, optional `export const inDomain = input => boolean`. Load the compiled proved code with `globalThis.forge.loadDafny('<relative path to the emitted .js>', ['ModuleName'])`; import TypeScript files directly by their `.ts` path (Node 22.18+). Convert integers to and from BigNumber for the Dafny code (`globalThis.forge.require(process.env.NODE_PATH.split(':')[0] + '/bignumber.js')`).
   - Python: `def generate(rng)`, `def reference(input)`, `candidates = {name: function}`, optional `def in_domain(input)`; import the emitted module after adding its folder to `sys.path`.
   - Inputs and outputs must be plain JSON values with the same shape for the reference and every candidate. Convert both sides to the same plain form (for example `{"ok": true, "spent": 5}`).
   - `generate` must cover the edge cases of the decision record: zero, each limit and one past it, empty and duplicate values, and odd host values (`rng.weird()`: empty strings, spaces, Unicode, very long strings). Use `rng.num(lo, hi)`, `rng.int`, `rng.pick`, `rng.string`, `rng.array`, `rng.bool`.
   - `inDomain` / `in_domain` must return false for inputs outside the spec's `requires` (compiled Dafny code does not check them).
   - Copy EVERY decision from the check's decision record (`.forge/decisions/<check>.md`) into `decisions` (JavaScript: `export const decisions = [{ id: 'D1 …', input }]`; Python: `decisions = [{'id': 'D1 …', 'input': …}]`), with the same concrete values. They run on every run as fixed inputs, so the person's own edge cases are always checked.
4. Add to the check: "diff": {"lang": "js" | "py", "adapter": "<file>", "watch": ["<files of the code under test>"]} (a list of entries if there are several languages). Edits to `watch` files trigger an automatic run at the end of each Claude turn.
5. Run `python3 "${CLAUDE_PLUGIN_ROOT}/cli/forge_check.py" diff` once. Explain each disagreement in plain words: is it a bug in the code under test, or a place where the spec says something other than what the person meant? Do not fix anything without the person's word.
6. Tell the person that forge.json changed, so they run `/forge approve`, then `/forge approve yes`.
