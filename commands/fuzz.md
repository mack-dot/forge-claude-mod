---
description: Forge · fuzz your real code against the proved rule (sets it up the first time; a deep run after that)
argument-hint: "[function, e.g. src/keyword.ts:mayShow]"
---
Forge rule: never write `.forge/approved.json` and never approve for the person. The checker is `python3 "${CLAUDE_PLUGIN_ROOT}/cli/forge_check.py"`.

The differential fuzz runs the person's real code and the compiled proved rule on the same inputs and reports every input where they disagree. Function the person named, if any: "$ARGUMENTS".

**If every check in `.forge/forge.json` that has code under test already has a "diff" entry:** run `python3 "${CLAUDE_PLUGIN_ROOT}/cli/forge_check.py" diff --inputs 50000 --budget-ms 60000` (a deep run) and explain each disagreement in plain words: a bug in the code under test, or a place where the spec says something other than what the person meant. Change nothing without the person's word. Stop.

**Otherwise, set it up for the check:**
1. The check needs a "build" for the language of the code under test and a fresh build. If it has none, add it as `/forge:build-setup` describes (targets: py, js, go, java, cs, rs), then tell the person to run `/forge build` after approving.
2. Find the code under test: the shipped function that implements this rule today (keep it as it is) and the wrapper around the compiled proved code, if any. Ask if it is not clear. Never change the code under test to make it agree.
3. Write `diff/<check>_inputs.py` (Python, shared by every language): `def generate(rng)` returning one input as a plain JSON value; optional `def in_domain(input)` (False for inputs outside the spec's `requires`); and `decisions = [{'id': 'D1 …', 'input': …}, …]` copied from EVERY decision in `.forge/decisions/<check>.md` with the same concrete values. Cover zero, each limit and one past it, empty and duplicate values, and odd values (`rng.weird()`). Helpers: `rng.num(lo, hi)`, `rng.int`, `rng.pick`, `rng.string`, `rng.array`, `rng.bool`.
4. Write the evaluator for the code's language. Inputs and outputs are plain JSON with the same shape for the reference and every candidate.
   - JavaScript or TypeScript: `diff/<check>_diff.mjs` exporting `reference(input)` and `candidates = { name: input => output }`. Load the compiled proved code with `globalThis.forge.loadDafny('<relative path to the emitted .js>', ['ModuleName'])`; import TypeScript by its `.ts` path; convert integers with BigNumber (`globalThis.forge.require(process.env.NODE_PATH.split(':')[0] + '/bignumber.js')`). Entry: {"lang": "js", "inputs": …, "adapter": …}.
   - Python: `diff/<check>_diff.py` with `reference(input)` and `candidates = {name: function}`. Entry: {"lang": "py", "inputs": …, "adapter": …}.
   - Any other language (Go, Rust, Java, C#): a small program in that language that prints `{"candidates": ["name", …]}` as its first line, then for each line `{"input": …}` on standard input prints one line `{"reference": …, "candidates": {"name": …}}`, calling the compiled proved code and the code under test; an exception becomes `{"__throws__": "<ErrorName>"}`. Entry: {"lang": "cmd", "inputs": …, "run": ["<program>", "<args>"]}. Say plainly that this evaluator is new code.
5. Add the entry to the check's "diff" (a list if there are several languages), with "watch": [files of the code under test]. Edits to those files trigger a run at the end of each Claude turn (about a second).
6. Run `python3 "${CLAUDE_PLUGIN_ROOT}/cli/forge_check.py" diff` once and explain each disagreement as above.
7. Tell the person that forge.json changed: `/forge approve`, then `/forge approve yes`.
