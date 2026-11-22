# Forge for Claude Code

A Claude Code mod that brings the [Forge](https://github.com/mack-dot/forge) proof gate into your session.
You approve a short spec once. After that, code may change only if Dafny proves it still meets the spec.

## Install

At the prompt of a Claude Code terminal session:

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

## Use

Type `/forge` in any repository. Each time, it shows the next step and Claude walks you through it:

1. Install Dafny (the proof checker). The mod uses the `"dafny"` path in `.forge/forge.json` if set,
   then `dafny` on PATH, then the `FORGE_DAFNY` environment variable.
2. Pick one small, critical function (money, limits, permissions). Claude writes the spec, the
   implementation and `.forge/forge.json`.
3. Type `/forge approve`. It shows the contract and `.forge/forge.json`, and Claude explains each clause in
   plain words. Type `/forge approve yes` when it says what you want. If a file changes between reading and
   "yes", the approval is refused.
4. `/forge check` runs Dafny. The status line shows `forge ✓ VERIFIED n/n` or what failed.
5. When a later edit touches a covered file, the status line says "code changed" until you check again.

Other commands: `/forge spec` (show the contract again), `/forge status`, `/forge add`.

### Verdicts

| Verdict | Meaning |
| --- | --- |
| `VERIFIED` | Dafny proved the files against the approved spec |
| `FAILED` | Dafny found a proof failure |
| `NOT_APPROVED` | The spec was never approved |
| `SPEC_CHANGED` | The spec changed after approval |
| `CONFIG_CHANGED` | `.forge/forge.json` changed after approval (a check, spec path, file list or binding) |
| `BINDING_BROKEN` | The implementation no longer starts with the approved `include` and `refines` lines |
| `UNSOUND_CONSTRUCT` | A file uses `assume`, `{:verify false}`, `{:axiom}`, `{:extern}` or `{:only}` (text scan) |
| `ERROR` | Dafny did not run correctly |

Why `binding`: in Dafny 4.11 an implementation can switch to refining a weaker spec while the approved spec
file stays unchanged, and Dafny still reports success. A check's optional `"binding": {"file", "prefix"}`
pins the exact first lines of the implementation, for example
`include "../spec/LedgerSpec.dfy"\n\nmodule Ledger refines LedgerSpec {`.

Files it keeps in your project: `.forge/forge.json` (checks), `.forge/approved.json` (your approvals),
`.forge/last-check.json` (last verdicts).

## Limits

- This is a feedback loop, not a security boundary. The mod blocks Claude's Edit and Write tools on
  approved specs and on `.forge/approved.json`, but a shell command can still change them.
- A spec covers only what it says. Files without a spec are not checked.
- Dafny runs can take seconds to minutes.

## License

MIT. See `LICENSE`.
