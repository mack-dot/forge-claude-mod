# State machines: trace fuzz (many events per case)

Optional: a `"trace"` block on a diff entry, for rules of the form `Step(State, Event, now)`.

[Back to the README](../README.md)

Some bugs need two or more events: a retry applied twice, a field lost when the state is saved and loaded again, an
expiry check that moves after a pause. One call per input cannot find them. For a model of the form
`Step(State, Event, now) -> Ok(State) | Err(Reject)`, add a `"trace"` block to a `js` or `py` diff entry:

```json
{ "name": "js", "lang": "js", "inputs": "diff/subscription_events.py", "adapter": "diff/subscription_trace.mjs",
  "trace": { "count": 2000, "budget_ms": 10000, "max_len": 10,
             "retry": 0.15, "replay": 0.08, "reorder": 0.08, "restart": 0.06 } }
```

- The events module (Python, shared by every language) defines `event(rng, ctx)` and optionally `advance(rng)`
  (clock ticks before an event), `start_time(rng)`, `event_in_domain(e)` and `trace_in_domain(steps)`.
- The adapter exports `trace = { reference, candidates: { name: machine } }`; a machine has `init()`,
  `step(state, event, now)` returning `{ ok: state }` or `{ reject: reason }`, `view(state)` (the JSON compared after
  each step) and optionally `save(state)` / `load(saved)` (what a restart goes through). A rejected step keeps the
  state object it got, so code that changes the state before it rejects is found.
- The core (`cli/forge_trace.py`) generates seeded traces with the clock moving forward and injects faults: `retry`
  (the same event again), `replay` (an older event again), `reorder` (two events swapped) and `restart` (save, then
  load). It runs the model and every candidate over the same trace, compares verdict and state after every step,
  and shrinks a failing trace by delta debugging: it drops chunks of events, then makes times and event fields
  simpler, then drops again. Each failure prints its minimal trace, the step where it differs and an `again:`
  command (same seed, `--inputs` = trace index + 1) that finds it again. Failing traces are saved and replay first.
- `--inputs` sets the number of traces and `--budget-ms` the time budget for a run.

Example: `examples/subscription-machine` (a subscription with Start, Renew, Pause, Resume and Cancel, and expiry by
time). Its Dafny model is proved against `spec/SubscriptionSpec.dfy` (clauses M1–M7). The correct shipped code is
`app/subscription.ts` and `.py`. Four planted copies (`app/subscription_planted.*`) each have one bug that needs two or
more events. Measured on one machine (seed 7, 2,000 traces), each bug is caught in JS and Python, and each shrinks to
its minimal trace:

| Planted bug | Shrunk trace (steps) |
| --- | --- |
| `replay-off-by-one`: duplicate check `<` instead of `<=` (an immediate retry applies again) | 2 |
| `pause-expiry`: after a Resume the expiry check is `>` instead of `>=` | 4 (Start, Pause, Resume, Pause at the expiry tick) |
| `restart-drops-id`: `save()` leaves out `lastId` | 2 (Start, restart) |
| `reject-leaks`: Renew changes the state before it checks expiry; the rejected step keeps the change | 2 |

The correct code had 0 disagreements over 50,000 traces (about 275,000 steps) in each language. Wall time for
50,000 traces, correct code only, one machine: JS 11.6–21.4 s, Python 11.2–17.0 s (five runs, one in a clean clone; the highest JS time
was measured while other heavy work ran on the machine). Negative controls in `tests/run_tests.py`: with `max_len: 1`, no
planted bug is found; with `restart: 0`, the restart bug is not found.

Covered: JavaScript/TypeScript and Python evaluators, and any `"cmd"` program that speaks the trace protocol (see
the top of `cli/forge_trace.py`). Not covered yet: the built Go, Rust, Java and C# evaluators. Labels: the traces
are random and bounded (`max_len`), so a run is a FINITE TEST, not a proof about every sequence.

See `PLAN.md` for what is built and what is not.
