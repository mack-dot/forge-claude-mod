---
description: Forge · rank the rules in this codebase worth proving (changes nothing)
---
Forge rule: the person approves a short spec once; after that, code may change only if Dafny proves it still meets the spec. Use short sentences. Never write `.forge/approved.json`, never run `forge_check.py approve`, and never type `/forge approve yes` for the person: approval is their act. The checker is `python3 "${CLAUDE_PLUGIN_ROOT}/cli/forge_check.py"`.

Survey this codebase and recommend what is worth proving. Do NOT write or change any file.

1. Read the layout, then the code that decides money, limits, quotas, permissions and access, state changes (status fields, lifecycles), retries and idempotency, parsing of untrusted input, and scoring or ranking rules.
2. Rank at most 7 candidates in one table: rank | file:line | function | the rule in one plain sentence | what goes wrong if it breaks | kind (pure rule: provable / rule mixed with I/O: split the rule out / judgment: not provable) | effort (small, medium, large).
3. Prefer small pure rules with a high cost of failure. Say plainly if the codebase has none.
4. Name up to 3 things that look important but should NOT be proved, and why (UI, ML output, glue).
5. End with: to start on one, type `/forge:add` and say the rule in your own words; the table is a suggestion, not the rule.

Focus, if the person gave one: $ARGUMENTS
