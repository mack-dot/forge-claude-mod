---
description: Forge · rank the rules in this codebase worth proving (changes nothing)
---
Forge rule: the person approves a short spec once; after that, code may change only if Dafny proves it still meets the spec. Use short sentences. Never write `.forge/approved.json`, never run `forge_check.py approve`, and never type `/forge approve yes` for the person: approval is their act. The checker is `python3 "${CLAUDE_PLUGIN_ROOT}/cli/forge_check.py"`.

Survey this codebase and recommend what is worth proving. Do NOT write or change any file.

Work quickly: list the files, search for decision words (limit, cap, allow, deny, permission, role, refund, status, retry, expire, quota), and read only the functions you consider ranking. Aim to finish in about a minute.

1. Read the layout, then the code that decides money, limits, quotas, permissions and access, state changes (status fields, lifecycles), retries and idempotency, parsing of untrusted input, and scoring or ranking rules. Skip vendored and generated folders (node_modules, dist, build, vendor, .venv). Also include:
   - error and fallback branches that decide allow or deny when a dependency fails or a limit is hit (fail-open versus fail-closed). These are rules, not glue;
   - the code that decides WHICH rule applies: subject, type, tenant or role detection from caller-supplied data. It has the same stakes as the rule it selects: rank it next to that rule, not below it.
2. Rank at most 7 candidates in one table: rank | file:line | function | the rule in one plain sentence | what goes wrong if it breaks | kind (pure rule: provable / rule mixed with I/O: split the rule out / judgment: not provable) | effort (small, medium, large).
   - State the INTENDED rule, from docs, names and tests, not a paraphrase of what the code does. If the code looks wrong against that intent, add "suspected bug: <one line>" to the row.
   - Rank by what goes wrong if the rule breaks (money, privacy, safety, access first). A suspected bug moves a row up only past rows of similar stakes; it never pushes a higher-stakes rule out of the top 5.
   - For unit conversions (time, money, tokens, sizes), state the precision requirement (rounding, truncation, overflow) and mark the row diff-fuzzable.
3. Prefer pure rules with a high cost of failure. Do not drop the core decision or state-transition function because it is large: if it is the riskiest code, rank it and mark the effort large. Say plainly if the codebase has none.
4. Name up to 3 things that look important but should NOT be proved, and why (UI, ML output, glue). Never put an allow/deny or fail-open/fail-closed decision on this list.
5. End with: to start on one, type `/forge:add` and say the rule in your own words; the table is a suggestion, not the rule.

Focus, if the person gave one: $ARGUMENTS
