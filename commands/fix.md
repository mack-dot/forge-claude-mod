---
description: Forge · explain what failed, in plain words, and propose a fix
---
Forge rule: the person approves a short spec once; after that, code may change only if Dafny proves it still meets the spec. Use short sentences. Never write `.forge/approved.json`, never run `forge_check.py approve`, and never type `/forge approve yes` for the person: approval is their act. The checker is `python3 "${CLAUDE_PLUGIN_ROOT}/cli/forge_check.py"`.

Run `python3 "${CLAUDE_PLUGIN_ROOT}/cli/forge_check.py" check --json --no-write` and read the verdicts and details.

TALK PLAINLY: the person may be new to Forge. With them, do not use the words Dafny, spec, module, refines, binding, ensures, requires, lemma, harness, adapter, evaluator or candidate, and do not show a verdict code without one plain sentence. Say "the rule", "the proof checker", "your answers" and "the test that runs your code against the proved rule". File names are fine when you list what you wrote. Each verdict in the JSON has a "help" line: one plain sentence and the next action. Start from it.

For each check that is not VERIFIED, explain in plain words why, then act by verdict:
- FAILED: the code breaks the spec. Find the clause and the input. Propose a fix to the implementation and apply it only if the person agrees. Never weaken the spec without the person's word.
- EXAMPLES_FAILED: the spec contradicts one of the person's recorded answers or leaves it open. Show which answer. Propose a spec change that matches the answer; the person must approve again.
- NOT_APPROVED, SPEC_CHANGED, CONFIG_CHANGED, APPROVAL_CHANGED: the person must read and approve; point them to `/forge:explain`, then `/forge approve`.
- UNAPPROVED_BYPASS: the proof passed, but code outside the rule uses its watched fields and is not allowed. Point the person to `/forge:bypass`: route each place through the proved rule, or allow it (they approve forge.json again). Never add an allowed place without their word.
- BINDING_BROKEN, UNSOUND_CONSTRUCT: say what was found and restore the approved form.
- ERROR: explain the tool error and the fix.
End with one step: "After the fix, type `/forge` again" (it proves the rule again and runs the fuzz).
