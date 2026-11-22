---
description: Forge · explain what an approval covers, in plain words (changes nothing)
---
Forge rule: the person approves a short spec once; after that, code may change only if Dafny proves it still meets the spec. Use short sentences. Never write `.forge/approved.json`, never run `forge_check.py approve`, and never type `/forge approve yes` for the person: approval is their act. The checker is `python3 "${CLAUDE_PLUGIN_ROOT}/cli/forge_check.py"`.

Run `python3 "${CLAUDE_PLUGIN_ROOT}/cli/forge_check.py" spec` and read its output: `.forge/forge.json`, each spec, and where present the person's answers (examples file) and decision record.

1. For each spec: a short table, clause | guarantee in plain words.
2. If there is a decision record: check that every answer in it appears as a D-method in the examples file. List any that is missing.
3. List what is NOT guaranteed (code no check lists, the spec being the right one, the compiler, whether the app calls the proved code, anything named "out of scope").
4. Do not approve. End with: if this is what you want, type `/forge approve` and then `/forge approve yes`.
