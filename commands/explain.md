---
description: Forge · explain a rule and what your approval covers, in plain words (changes nothing)
---
Forge rule: the person approves a short spec once; after that, code may change only if Dafny proves it still meets the spec. Use short sentences. Never write `.forge/approved.json`, never run `forge_check.py approve`, and never type `/forge approve yes` for the person: approval is their act. The checker is `python3 "${CLAUDE_PLUGIN_ROOT}/cli/forge_check.py"`.

TALK PLAINLY: the person may be new to Forge. With them, do not use the words Dafny, spec, module, refines, binding, ensures, requires, lemma, harness, adapter, evaluator or candidate, and do not show a verdict code without one plain sentence. Say "the rule", "the proof checker", "your answers" and "the test that runs your code against the proved rule". File names are fine when you list what you wrote.

Run `python3 "${CLAUDE_PLUGIN_ROOT}/cli/forge_check.py" spec` and read its output: `.forge/forge.json`, each spec, and where present the person's answers (examples file) and decision record.

1. For each rule: a short table, line of the rule | what it guarantees, in plain words.
2. If there is a decision record: check that every answer in it appears as a D-method in the examples file. List any that is missing.
3. List what is NOT guaranteed (code no check lists, the spec being the right one, the compiler, whether the app calls the proved code, anything named "out of scope").
4. Do not approve. End with exactly one step: "If this is what you want, type `/forge approve yes` (if Forge has not shown it to you in the last day, it shows it first)."
