---
description: Forge airlock · draft forge.yaml for this repository (hidden paths, read-only paths, MCP tool rules), then tell you the one command that starts the box
---
You help the person write `forge.yaml`, the policy of the Forge MCP airlock. The airlock runs a program (usually `claude`) in a Docker box: hidden paths are ABSENT from the box (also from git history), read-only paths cannot change, MCP tools are default deny, and gated tools are capped by a compiled verifier whose model is proved in Dafny. Use short sentences. The airlock CLI is `python3 "${CLAUDE_PLUGIN_ROOT}/airlock/forge_airlock.py"`.

Hard rules:
- Never read, print, ask for or write a secret VALUE. In forge.yaml a secret is only `${NAME}` (an environment variable name). If `.mcp.json` holds a literal key, say that it was replaced by `${NAME}` and that the person should export NAME in their shell (and rotate the key if it was committed).
- Never start `forge up` yourself: it is interactive and cannot run inside this conversation.
- Never overwrite an existing `forge.yaml` or `./forge-airlock` without asking first.

Steps:
1. Run `python3 "${CLAUDE_PLUGIN_ROOT}/airlock/forge_airlock.py" init --json` in the repository root. It returns a draft (`forge_yaml`), `notes` and `questions` (hidden and read-only candidates with example paths; one entry per MCP tool if tool names are known). It writes nothing.
2. If `.mcp.json` lists servers and the draft notes say their tools are not listed yet, offer to run `python3 "${CLAUDE_PLUGIN_ROOT}/airlock/forge_airlock.py" tools` (needs Docker; it starts each server in the airlock image with the named secrets from the person's shell). If the person agrees and it works, save its JSON output to a temporary file and run `init --json --tools <file>` again.
3. Read the repository layout yourself as well (list files; look for secrets, infrastructure, proprietary or licensed code, evaluation answers, customer data, prompts) and add candidates the draft missed.
4. Ask the person with AskUserQuestion, at most 4 questions per round, each with concrete paths or tool names and a recommended answer first:
   - hidden_paths: "Hide these from the agent completely (absent, also from git history)?" with the exact patterns and 1–3 example files each (`.env*`, secrets, `infra/**`, proprietary code);
   - readonly_paths: "Let the agent read but never change these?" (`tests/**`, `spec/**`, `.forge/**`, `INVARIANTS.md`, `.github/**`);
   - per MCP server: read-like tools allowed, destructive tools denied, tools that spend money or act on the world gated with a cap. Ask for each cap as a number with its unit (for example "refunds: at most 5000 cents in total for 24 hours?" or "create_pull_request: at most 10 calls?");
   - model access for `claude` in the box (the draft's `llm` section): the NAME of the shell variable that holds the key (`key_env`, usually `ANTHROPIC_API_KEY`; the key never enters the box, the airlock adds it), the caps per session and per UTC day and per request (estimated cost units: 1000000 = 1 USD from the `prices` table, or `unit: tokens`), and the allowed models. Say that the prices are a snapshot to check. The box needs no `agent.egress_allow` for the model.
   A tool you cannot classify stays out (default deny) unless the person allows it.
5. Write `forge.yaml` in the repository root from the draft and the answers (keep the draft's header comment). Then run `python3 "${CLAUDE_PLUGIN_ROOT}/airlock/forge_airlock.py" check` if Docker is available, and fix any error it reports.
6. Write the project wrapper: `python3 "${CLAUDE_PLUGIN_ROOT}/airlock/forge_airlock.py" wrapper` (it refuses to replace a different `./forge-airlock`; ask before passing `--force`).
7. End with exactly what to type in a terminal (not here), for example:
   `./forge-airlock up -- claude`
   With `llm` in forge.yaml, `claude` in the box uses the key from the person's shell through the airlock, capped, and the box holds no key: no `--pass-env` is needed. Without `llm`, they log in inside the box, or pass a key with `--pass-env ANTHROPIC_API_KEY`; say plainly that with `--pass-env` the agent CAN read that value. Also say: when the program exits, the airlock shows the diff and changes the real repository only after they type y; hidden and read-only paths are never changed.

Focus, if the person gave one: $ARGUMENTS
