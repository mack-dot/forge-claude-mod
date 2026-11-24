# Bypass: code that goes around a proved rule

Optional: `/forge:bypass`.

[Back to the README](../README.md)

A proof is only as strong as the guarantee that nothing goes around it. On a real app, a proved read rule decided that
one field counts only on one kind of row, but two other functions still trusted that field on any row. A person
found that by hand. `bypass` finds such places.

Add `"watchedFields"` to a check: the texts that name what the rule decides on (field names such as `body.owner` or
`groupId`, a comparison, function names). A text between slashes is a regular expression
(`"/spent\\s*\\+\\s*amount[^;{]*<=?\\s*cap/"`); anything else is literal. `/forge:bypass` proposes them from the approved
spec and decision record. Then:

```
python3 cli/forge_check.py bypass [--root DIR] [--json]
```

searches the project's files (git's tracked and untracked-but-not-ignored files; without git, a walk) for each term.
It skips the rule's own files (spec, implementation and its includes, your answers, decision record, fuzz harness,
emitted code, diff adapters and inputs, `INVARIANTS.md`), vendored and build folders (`node_modules`, `dist`,
`build`, `verified`, `vendor`, `target`, `.forge`, …), documentation and lockfiles. It reports every remaining line:
file:line, the term, the line, and the enclosing function when a cheap heuristic finds it (Python, TS/JS, Go, Java,
C#, Rust). `/forge:bypass` explains each place and asks you: route it through the proved rule, it is fine (record
why), or make it part of a new rule. A "fine" answer is stored in the check's `"allowedBypasses"` list as
`{file, line_text_sha256, note}`, so an edited line shows up again. Both lists are in `forge.json`, so you approve them
like any other change: every allowed bypass is approved by a person.

**A hard gate.** For a check with `watchedFields`, `check` gives `UNAPPROVED_BYPASS` (exit 1) when its proof passes
but a line outside the rule matches and is not in its approved `allowedBypasses`; the detail lists the lines. A failed
proof (or any other verdict) still wins. A check without `watchedFields` behaves as before.

**Specific terms.** Because it is a gate, a term must name the forms that decide, not every mention:
`.body.owner` (a property access), `/\\bgroupId\\s*(===|!==|==|!=)/` (a comparison), not a bare `groupId`.
`bypass --count --rule NAME --set-terms TERM…` previews the number of lines per term and writes nothing.

| Bypass verdict | Meaning | Exit |
| --- | --- | --- |
| `BYPASS_CLEAR` | No unreviewed line outside the rule uses its watched fields | 0 |
| `BYPASS_FOUND` | At least one such line | 1 |
| `NO_SCOPE_TERMS` | No check has `watchedFields`: nothing was searched (not a pass) | 0 |
| `UNAPPROVED_BYPASS` (a `check` verdict) | The proof passed, but an unallowed line outside the rule matches | 1 |

`check` runs the same search: the unreviewed places also go under `"outside"` in its JSON and on one line of its
report (`3 places decide with body.owner outside policy-view: src/policy.ts:8 canCancel, …`).
`/forge approve yes` prints the count; bare `/forge` offers `/forge:bypass` for a check without watched fields.

Measured on that app (a copy, 205 files searched, about 0.3 s): the two bare field names match 290 lines in 25
files (275 of them for the id field): too many for a gate. The specific terms (the property access with its dot, and
a comparison on the id field from each side, the forms shown above) match 19 lines in 10 files, and still name the
three places found by hand.

**Decision reads only (AST), in TypeScript / JavaScript and Python.** In these files a line counts only where the
watched field is READ in a decision. Each hit names its kind: `if-condition`, `early-return-guard`, `loop-condition`,
`comparison` (`===`, `!==`, `==`, `!=`, `<`, `<=`, `>`, `>=`, `instanceof`), `in`, `includes` (also `.has`,
`.indexOf`, `.startsWith`, ...), `ternary`, `switch`, `case`, `logical-guard` (the left side of `&&` / `||`, not an
`x || ''` default), `filter-predicate` / `find-predicate` / `predicate` (`filter`, `find`, `some`, `every`; Python
comprehension `if`, `filter`, `any`, `all`), `assert`, `sql-where` (a parameter of a query whose SQL text has a
`WHERE`), and `alias` (`const v = row.f` where `v` is read in a decision in the same function). Type and interface
declarations, type annotations, imports, comments, string literals, plain assignment, object construction and
logging are not hits. TS / JS files are parsed with the TypeScript compiler API (TypeScript 5.6.3, downloaded once
into the cache and checked against its pinned SHA-256, as Dafny is; it only parses, it never runs the app); Python
files with `ast`. Every other file (Go, Rust, Java, C#, data files) keeps the text search per line, labelled
`text search, not AST`. A TS / JS / Python file that does not parse, or a machine without Node or without the
TypeScript package (offline, `FORGE_NO_DOWNLOAD=1`), falls back to the text search for that file and the report
says `FALLBACK to text search, not AST` with the reason: fail closed, never skipped. An approved `allowedBypasses`
entry stays allowed by its line hash. `bypass --json` lists the text hits that are not decision reads under
`"not_decisions"`.

Measured on a copy of the same app with the bare terms (290 text lines): 76 hits remain, 46 decision reads found by
the AST (all 46 checked by hand: each is a decision site) and 30 lines in data files (text search, not AST); the
three places found by hand are still found. Of the 214 dropped lines, 85 were checked by hand: 81 are not decisions;
4 feed a decision that the search cannot see (2 checks through a computed key, 2 inputs to a policy engine).

Limits: a syntactic search, not a data-flow analysis (a FINITE TEST of the listed terms). It misses a value passed
to another function or returned to a caller that decides, dynamic access (`body[key]`), and code that decides the
same question under other names. The alias rule is name-based. The function name is a heuristic on indentation.
