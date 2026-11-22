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
3. Read the spec. Claude explains it in plain words. Type `/forge approve` when it says what you want.
4. `/forge check` runs Dafny. The status line shows `forge ✓ VERIFIED n/n` or what failed.
5. When a later edit touches a covered file, the status line says "code changed" until you check again.

Other commands: `/forge status`, `/forge add`.

Files it keeps in your project: `.forge/forge.json` (checks), `.forge/approved.json` (your approvals),
`.forge/last-check.json` (last verdicts).

## Limits

- This is a feedback loop, not a security boundary. The mod blocks Claude's Edit and Write tools on
  approved specs and on `.forge/approved.json`, but a shell command can still change them.
- A spec covers only what it says. Files without a spec are not checked.
- Dafny runs can take seconds to minutes.

## License

MIT. See `LICENSE`.
