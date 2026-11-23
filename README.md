# Forge for Claude Code

**Forge proves the rules your code must never break, once you approve them in plain words, and tests your shipped
code against them.**

```
/plugin install forge --marketplace mack-dot/forge-claude-mod
/forge
```

What you get:

- `/forge` walks you through it: a read-only scan, 1 to 5 numbered picks, plain questions with real numbers, then
  your approval (`/forge approve yes`). Nothing in your app changes until you say yes.
- A proof of each approved rule, a fuzz of your shipped code against it (1,500 random inputs; 230 to 310 ms per
  language on the example, one Mac), `INVARIANTS.md` with every rule in plain words, and one honest status line.
- Python, TypeScript/JavaScript, Go, Rust, Java and C#.

> **Honest limits.** PROVED: only the Dafny model of each rule, against the spec you approved. NATIVE CHECK (not
> proved): Dafny's compilers and the compiled code. FINITE TEST: the fuzz of your shipped code and your wrapper; it
> finds disagreements, it cannot show there are none. Without the airlock this is a feedback loop, not a security
> boundary. `end_to_end=false`: the chain from your approval to the code your app runs is not proved end to end.

<!-- A short screen recording of the four steps would go here. -->

## The four steps

| Step | You type | What you see |
| --- | --- | --- |
| 1 | `/forge` | Claude reads your code (it changes nothing) and lists up to 5 rules worth protecting, numbered. |
| 2 | `/forge` again, then the number (it fills in `/forge:add` for you) | Claude says the rule back, asks edge-case questions with real numbers ("a 150% code on $20.00: price $0.00, or refuse the code?"), and writes down your answers. |
| 3 | `/forge`, then `/forge approve yes` | Your rule and answers in plain words, then the exact rule the proof uses. Only you can approve; Claude cannot. |
| 4 | nothing | Forge proves the rule, compiles it, fuzzes your shipped code against it, writes `INVARIANTS.md`, prints one status line and at most one next step. |

On the included example (`examples/wallet-cap`), step 4 prints, in part:

```
✓ wallet-cap: VERIFIED
✗ wallet-cap:py: your code disagrees with the proved rule on 3 of your 4 answers and 2 other input(s) (1500 random inputs, 270 ms, ...)
  your recorded answers: 1 of 4 agree; failing: D1 exact cap allowed (legacy), D3 zero rejected (legacy), D4 negative rejected (legacy)
✗ wallet-cap:go: ... legacy-go says {"ok": true, "spent": -9223372036854775808}   (a 64-bit overflow, found by itself)
Status: forge ✓ wallet-cap: model PROVED · fuzz ✗ 36 disagreement(s), /forge diff · 0 bypasses · records current
Next: your shipped code disagrees with the proved rule (each one is a bug, or a gap in the rule). Press Enter: Claude explains each one.
```

Every verdict code (for example `SPEC_CHANGED`) comes with one plain sentence and one next action. `/forge more`
lists every other command.

## Install

You need:

| For | What | How (macOS) |
| --- | --- | --- |
| everything | Claude Code (terminal) | already installed |
| everything | Python 3.8 or newer as `python3` | already on most machines |
| proofs | Dafny 4.11 | nothing: downloaded automatically on first use (pinned 4.11.0, SHA-256 checked, about 60 MB, into `~/.cache/forge-check`; macOS arm64/x64, Linux x64, Windows x64). Elsewhere (for example Linux arm64), or offline, install it yourself (`brew install dafny`); set `FORGE_NO_DOWNLOAD=1` to forbid the download |
| `/forge airlock` box only (optional) | Docker with Compose v2 | Docker Desktop; nothing else in Forge needs Docker |
| fuzzing your shipped code | the tools of your code's language (a missing one is named, with its install line) | `py`: python3; `js`: node + npm; `go`: go + goimports; `java`: a JDK; `cs`: dotnet; `rs`: cargo |

Then, at the prompt of a Claude Code terminal session:

```
/plugin install forge --marketplace mack-dot/forge-claude-mod
```

Answer `y` to add the marketplace, then pick a scope.

From a shell:

```
claude plugin install forge --marketplace mack-dot/forge-claude-mod
```

In the desktop app: click + next to the prompt box, then Plugins, Add marketplace with
`mack-dot/forge-claude-mod`, then Add plugin.

## More (optional, when you need it)

- [docs/commands.md](docs/commands.md): every command, the whole workflow, the files Forge keeps.
- [docs/fuzz.md](docs/fuzz.md): the fuzz in detail, its languages, coverage, `mutate --app`, messy inputs.
- [docs/build.md](docs/build.md): `/forge build`, the generated JavaScript module, measurements.
- [docs/bypass.md](docs/bypass.md): `/forge:bypass`, code that decides the same thing without the proved rule.
- [docs/approvals.md](docs/approvals.md): `INVARIANTS.md`, signed approvals with a hardware key, what a green check covers.
- [docs/airlock.md](docs/airlock.md): `/forge airlock`, an agent box with hidden paths and capped MCP tools (Docker); its honest labels and `prove_release.sh`.
- [docs/ci.md](docs/ci.md): the GitHub Action (zero configuration) and running the checker from a shell.

## Limits

- Without the airlock this is a feedback loop, not a security boundary. The mod blocks Claude's Edit and Write tools on
  approved specs and on `.forge/approved.json`, but a shell command can still change them. Signed approvals ([docs/approvals.md](docs/approvals.md)) make
  such a change fail `check` with `APPROVAL_UNSIGNED`, if the trust anchor is outside the project.
- A spec covers only what it says. Files without a spec are not checked.
- Dafny runs can take seconds to minutes.

## License

MIT. See `LICENSE`. Also: `CHANGELOG.md`, `SECURITY.md`, `CONTRIBUTING.md`, `PLAN.md`, `TRIAL.md`.
