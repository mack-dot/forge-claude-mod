---
description: Forge · find out why Forge does not run here (Python, the proof checker, the setup)
---
Forge rule: the person approves a short spec once; after that, code may change only if Dafny proves it still meets the spec. Use short sentences. Never write `.forge/approved.json`, never run `forge_check.py approve`, and never type `/forge approve yes` for the person: approval is their act. The checker is `python3 "${CLAUDE_PLUGIN_ROOT}/cli/forge_check.py"`.

Run, in order, and read each result:
1. `python3 --version` (needs 3.8 or newer).
2. `python3 "${CLAUDE_PLUGIN_ROOT}/cli/forge_check.py" setup` (finds Dafny or downloads the pinned 4.11.0 release).
3. `python3 "${CLAUDE_PLUGIN_ROOT}/cli/forge_check.py" check --no-write` if `.forge/forge.json` exists.
For each problem, one line: what is wrong, and the exact command that fixes it. If a tool for one language is missing (go, cargo, javac, dotnet, node), say that only that language needs it. Ask before installing anything. End with: "Then type `/forge` again."
