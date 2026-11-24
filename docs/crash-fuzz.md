# CRASH-CHECK: `/forge crash-fuzz` (no spec)

Optional, not part of the day-1 loop. A finite test with messy inputs: it shows crashes; it never proves anything.

[Back to the README](../README.md)

`/forge crash-fuzz` (or `python3 cli/forge_crash.py --root .`) needs no spec and no `forge.json`. It finds the
functions where outside data enters your code, calls each with messy JSON, and lists every error that escapes one,
with the smallest input that still gives it:

```
CRASH-CHECK: 3 handlers crash with unhandled errors: SyntaxError on a missing value (undefined), empty text; TypeError on a missing value (undefined), null
  4 boundary function(s), 199 calls, seed 1, 973 ms. CRASH-CHECK only: it shows crashes; it proves nothing, and 0 crashes is not a proof of no crash.
  ✗ js/handlers.ts:8 createOrder (express): 1 crash(es) in 34 calls
      CRASH TypeError at js/handlers.ts:9 on item = undefined: Cannot read properties of undefined (reading 'quantity')
        same crash also on: item = null, item = removed, whole input = undefined, whole input = null, whole input = empty-text, whole input = not-json-text
        repro: {"item": {"__undefined__": true}}
  ✗ js/handlers.ts:14 POST (fetch): 4 crash(es) in 33 calls
      CRASH TypeError at js/handlers.ts:17 on user = null: Cannot read properties of null (reading 'name')
        same crash also on: user = number, user = boolean, user = empty-text, user = lone-surrogate, user = long-text, user = array
        repro: {"user": null}
      CRASH SyntaxError at js/handlers.ts:15 on whole input = undefined: Unexpected end of JSON input
        repro: {"__undefined__": true}
  ...
  ✓ js/handlers.ts:27 updateLimit (express): 0 crashes in 120 calls
  skipped 1 function(s):
    js/handlers.ts:35 normalizeName: private helper: not exported, so no caller outside its module can pass it data; a crash there shows up through the boundary function that calls it
```

- **Which functions.** JS/TS: exported functions that look like a boundary: request/response or event parameters
  (Express/Fastify `(req, res)`, fetch-style `(request)` and `GET`/`POST` route handlers, Lambda `(event, context)`),
  handler/webhook/parse-like names, package entry points (`main`/`exports`/`bin`), files under `routes/`, `api/`,
  `handlers/`, `webhooks/`. Python: route/view decorators (`@app.route`, `@router.post`, `@api_view`,
  `@csrf_exempt`, ...), `__all__`, handler/webhook/parse-like names, a first parameter `request` or `event`.
  **Skipped, with the reason said:** private helpers (not exported, or `_name`: only a boundary function can pass
  them data, and the boundary function is what is called), other exports (internal module code), and functions that
  cannot be called directly: route handlers that are not exported, factories (`createHandler(deps)`), Flask views
  with no parameters, FastAPI handlers with typed parameters (the framework validates first).
  `--target FILE:FUNCTION` (or `.forge/crash-fuzz.json` `{"targets": [...]}`) checks exactly the functions you name;
  `--include-private` adds private Python helpers.
- **Which inputs.** The input shape is inferred from the fields the function reads (`req.body.item.quantity`,
  `payload["user"]`, destructuring, `JSON.parse(raw)`); then every field, and the whole input, gets each messy
  kind: undefined (JS), null, NaN, a number, a boolean, `""`, very long text, a lone surrogate, `[]`, `{}`, the
  number as text, text that is not JSON, removed; then random pairs. 120 calls per function by default
  (`--inputs`).
- **What counts.** A runtime error that escapes (TypeError, SyntaxError, KeyError, AttributeError, ...; a rejected
  promise or an unhandled rejection too) is a CRASH; a call with no answer in 1 s is a HANG. An error the code throws
  on purpose (`throw`/`raise` on that line, or a non-built-in error class) is listed apart: check that your
  framework answers 4xx for it. If the inferred input itself crashes, the shape guess is probably wrong: the
  function is NOT judged and none of its results count.
- Exit 1 when something crashed, 0 when not. **CRASH-CHECK, never PROVED:** 0 crashes is a finite test, not a proof.

Measured on `tests/crash_fixture/` (seed 1): per language, the three planted crashes (a missing field, a null
deref, `JSON.parse` / `json.loads` of a value that is not text) are found and shrunk (`{"item": undefined}`,
`{"user": null}`, `{"payload": {"user": null}}`); the fail-closed handlers have 0 crashes in 120 calls each; the
private helpers that throw on undefined / None are not reported (with `--include-private` the Python one is, so
the skip is a choice, not a blind spot). About 1 s for 8 functions.
