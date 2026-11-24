# Callers: does every caller go through the proved code?

Optional: set `"callers"` on a check in `.forge/forge.json` (ask Claude). It adds `UNVERIFIED_CALLER` to `check` and
the drift reasons to the status line.

[Back to the README](../README.md)

A check may name its governed decision (the function the app calls for this question), the wrapper that calls the
proved code, and the old logic the rule replaced:

```json
"callers": {"decision": "src/pricing.ts:canDiscount", "wrapper": "src/pricing-proved.ts:provedCanDiscount",
            "replaced": ["src/legacy/discount.ts:oldCanDiscount"]}
```

(`"wrapper"` is optional: without it the decision itself is the wrapper. `"callers"` may be a list, for example one
TypeScript and one Python decision.) For JavaScript/TypeScript and Python, `forge_check.py callers` builds a static
call graph (`cli/forge_callgraph.py`: a JS/TS tokenizer and parser in Python, Python's `ast`; no Node, no
dependencies). It follows named, default and namespace imports, import aliases (`import { a as b }`, `from m import a
as b`, `import m as n`), re-exports (`export { a as b } from`, `export *`), `require`, and `const f = g` aliases. It
lists as **UNVERIFIED**, with file:line and the enclosing function:

- a call of (or a reference to) a `"replaced"` function, under any name;
- a call by the decision's name (or a replaced function's name) that resolves to another function that does not
  reach the wrapper, to a member it cannot resolve, or to nothing in the project (a re-implementation path);
- the decision itself, when it no longer reaches the wrapper in the call graph.

`check` gives `UNVERIFIED_CALLER` (exit 1) when the proof passes but an unverified caller is not in the check's
`"allowedCallers"` (`{file, line_text_sha256, note}`, written by `callers --rule NAME --record FILE:LINE --note WHY`,
approved with `forge.json` like `allowedBypasses`). A failed proof still wins. Test files are counted, not gated. A
decision in another language says `callers not checked` (`NOT_CHECKED`, never a pass).

**Feeders: the next rule to prove.** The same graph traces the unproved code that feeds the decision's parameters:
each direct caller that computes an argument itself (not a plain pass-through of its own parameter), and each project
function whose return value flows into an argument inside that caller (through local variables). They are ranked by
leverage: 4 per parameter fed, 3 per decision fed, the number of callers (up to 10), 3 if a branch line names a
`watchedFields` term, 1 if the function branches at all. `check` puts the top one in `next_rule` (and `/forge
status` shows it): `prove checkout() (shop/checkout.ts:5, direct caller): feeds 3 of 3 parameters (amount, cap,
spent), 3 callers, branches on watched fields (2 lines)`.

**The status line.** `forge_check.py status` gives the one line the mod shows. Green: `forge ✓ model PROVED · 0
bypasses · fuzz 1,500 ok · callers 3 ok · records current`. Yellow, naming the exact reason, when the proof still
passes but something it rests on moved: a decision record describes code that changed (`decision note stale: …`), a
wrapper file (the `"callers"` wrapper or a diff adapter) changed since the last approval or the last diff run with no
disagreement (`wrapper changed since last approval/fuzz: …`), or a new unverified caller (`unverified caller
file:line (check exits 1)`). Red `forge ✗` for any other failed verdict or a fuzz disagreement. It never says
"end-to-end": the compilers, the runtime and the wrapper are not proved.

Limits: a static analysis of the source, not a proof and not a type checker. It does not see dynamic dispatch
(`obj[name]()`), `eval`, a function passed through a data structure and called later, tsconfig path aliases, package
imports, or callers in other languages. A method call `x.decision()` on an object it cannot resolve is listed as
unverified (fail closed), so an unrelated object method with the same name needs an `allowedCallers` entry. Data flow
is traced one function deep (the direct caller and the functions it calls for the arguments), not through control
flow. Measured on a copy of a private TypeScript app (135 tracked JS/TS and Python files, about 2.6 s): every caller found by
hand was found, with no false hit; see the pull request for the numbers.
