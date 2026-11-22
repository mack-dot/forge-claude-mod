# Contributing

## Layout

- `cli/forge_check.py` — the checker (`check`, `build`, `approve`, `spec`). Python 3.8+, standard
  library only. Every verdict is decided here, for the mod and for CI.
- `hooks/register.ts` — the Claude Code mod: commands, status line, guard. It calls the checker; it must
  not decide a verdict itself.
- `action.yml` — the GitHub Action. `examples/wallet-cap` — a complete small project.
- `tests/run_tests.py` — one case per verdict. `PLAN.md` — what is built and what is not.

## Before a pull request

```
python3 tests/run_tests.py          # needs Dafny 4.11 (PATH or FORGE_DAFNY), node and npm
claude plugin validate .            # needs Claude Code
```

## Rules

1. A new way to pass that should fail is a soundness bug. Add a test case that fails first, then fix it.
2. Every new verdict gets a test case and a row in the README table.
3. Do not round claims up. PROVED means Dafny proved a statement about the model. Compiled code is a
   NATIVE CHECK. A fuzz run is a FINITE TEST. Say what was not tested.
4. No new runtime dependency for the checker.
5. Never edit `examples/wallet-cap/verified/` by hand; run `build`.
