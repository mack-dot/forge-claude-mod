# Forge commands

Every command, the whole workflow, and the files Forge keeps. Day 1 needs only `/forge` and `/forge approve yes`;
`/forge more` in Claude Code prints this list in short.

[Back to the README](../README.md)

# Use

Type `/forge` in any repository: it tells you the next step. Commands with a **colon** (`/forge:scan`) start a
conversation with Claude; commands with a **space** (`/forge approve`) run a fixed step. The steps:

| Step | You type | What happens |
| --- | --- | --- |
| Find candidates (optional) | `/forge:scan` | Claude ranks up to 7 rules in your code worth proving, and names what should not be proved. Changes nothing. |
| State a rule | `/forge:add <the rule in your own words>` | A conversation: Claude says whether the rule can be proved, confirms safe defaults, asks edge-case questions with concrete numbers (as many rounds as needed), probes the spec with Dafny for cases nobody asked about, then writes the spec, **your answers as Dafny checks**, a decision record and the implementation. |
| Read it | `/forge:explain` | Claude explains every clause in plain words, checks that each of your answers is covered, and lists what is not guaranteed. |
| Approve it | `/forge approve`, then `/forge approve yes` | Shows exactly what you approve (spec, your answers, decision record, `forge.json`); "yes" approves exactly that, even after a restart. Typing "yes" first only shows it. If a file changed in between, it shows the new version instead. Only you can do this. After a "yes", Forge proves the rule, compiles it if the fuzz needs that, fuzzes your shipped code against it, writes `INVARIANTS.md` and prints one status line, by itself. |
| Prove it | `/forge check` | Your answers are checked against the spec (`EXAMPLES_FAILED` if the spec says otherwise or leaves one open), then the code against the spec. The status line says what a green check covers, for example `forge ✓ wallet-cap: model PROVED · fuzzed 1,500 cases, wrapper NOT proved · records current`, or what failed. |
| Find bugs in your real code | automatic (set up by `/forge add` when the rule already exists in your code), or `/forge fuzz` | Runs your shipped code and the proved rule on fresh random inputs at the end of each turn that changed them (about a second), saves each disagreement as a small repeatable case. `/forge fuzz` on its own is a deep run. See [fuzz.md](fuzz.md). |
| Find code around the rule | `/forge:bypass` | Claude proposes the fields the rule decides on, searches for code outside the rule that uses them, and asks you about each place. See [bypass.md](bypass.md). |
| Measure it | `/forge mutate` | Plants up to 30 small bugs in the proved code, one at a time in a temporary copy, and counts how many the proof catches. A survivor is a change your spec allows: harmless, or a gap in the spec. Changes nothing. |
| Something failed | `/forge:fix` | Claude explains the verdict and proposes a fix to the code; it never weakens the spec without your word. |
| Use it in your app | `/forge:build-setup`, then `/forge build` | Compiles the proved code to your languages (`py`, `js`, `go`, `java`, `cs`, `rs`), fuzzes the compiled code in every language (identical output required), and checks that your app imports it. |
| It does not run | `/forge:doctor` | Claude checks Python, Dafny and the config, and explains the fix. |

Dafny (the proof checker) is set up for you: the `"dafny"` path in `.forge/forge.json` if set, else `dafny`
on PATH, else `FORGE_DAFNY`, else a pinned download (macOS arm64/x64, Linux x64, Windows x64).

After that, let Claude (or any agent) change the covered code freely. When an edit touches a covered file,
the status line says "code changed" until you run `/forge check` again. `/forge status` and `/forge spec`
show the last verdicts and the approved contract.

Files it keeps in your project: `.forge/forge.json` (checks), `.forge/approved.json` (your approvals),
`.forge/last-check.json` (last verdicts).

# The workflow in one place

1. `/forge:scan`, then `/forge:add <n>`: state a rule; Claude asks, writes the spec and your answers as checks.
2. `/forge approve`: you approve it. `/forge check` proves it; `/forge fuzz` runs your real code against it.
3. `INVARIANTS.md`: every approved rule in plain words, regenerated on approval, checked in CI.
4. `/forge:airlock`: Claude drafts `forge.yaml` for an agent box (hidden paths, read-only paths such as `spec/`,
   `.forge/` and `INVARIANTS.md`, and a rule for each MCP tool), asks you, and writes `./forge-airlock`.
5. In a terminal: `./forge-airlock up -- claude`. Claude Code works inside the box; when it exits you see the
   diff, and your repository changes only after you type y. Hidden and read-only paths are never changed.

Steps 1 to 3 need no Docker. Step 5 needs Docker. Details of the box: [airlock.md](airlock.md) and `airlock/README.md`.
