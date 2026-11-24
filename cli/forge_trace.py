"""Stateful trace fuzzing for `forge_check.py diff`: a diff entry with a "trace" block.

The proved model is a state machine, step(State, Event, now) -> Ok(State) | Err(Reject). The shipped code is the
app's own step function over its own state. One call per input cannot find a bug that needs two or more events (a
retry applied twice, a field lost on restart, a boundary that moves after a pause). This core therefore generates
TRACES: random multi-event sequences with time advancing and with injected faults, runs the model and every
candidate over the same trace, compares the verdict and the state after EVERY step, and shrinks a failing trace to
a minimal event sequence (delta debugging: drop chunks of events, then make the remaining fields simpler, then drop
again). Same seed, same traces: the report prints a command that runs the failing trace again.

  events module (Python, shared by every language; the diff entry's "inputs"):
      event(rng, ctx) -> one event (a plain JSON value). `ctx` is a dict kept for one trace (ids so far, ...).
      advance(rng)    -> optional: how many ticks the clock moves before an event (default 0..25, some 0s).
      start_time(rng) -> optional: the first `now` (default 0).
      event_in_domain(event) -> optional: False rejects a shrunk event (for example an unknown kind).
      trace_in_domain(steps) -> optional: False rejects a whole trace.

  injected faults (rates in the "trace" block; each a probability per generated event):
      retry    the same event again (a client that lost the answer and sent it again), same time or a bit later
      replay   an older event of this trace again, at the current time (a queue that redelivers)
      reorder  the last two events delivered in the other order (each keeps its time slot)
      restart  the machine's state saved and loaded again (a process restart from the serialized state)

  evaluator (trace mode; cli/eval/node_eval.mjs and cli/eval/py_eval.py when the adapter exports `trace`):
      hello line:  {"candidates": [...], "trace": true}
      per line in: {"traces": [[step, ...], ...]}, step = {"event": <json>, "now": <int>} or {"restart": true}
      per line out: {"results": [{"reference": [out, ...], "candidates": {"name": [out, ...]}}, ...]}
      out = {"v": "ok" | "reject:<reason>" | "restart", "s": <the state's JSON view>}, or {"__throws__": "<Name>"}
      as the last element when a step threw (the rest of that trace is not run for that machine).

Test code: NOT proved. A trace fuzz is a finite test of the shipped code against the compiled model.
"""
import copy
import datetime
import hashlib
import json
import os
import random
import time

import forge_diff
from forge_diff import canon, signature, shrinks, Evaluator, EvaluatorError, load_inputs_module

DEFAULTS = {
    'count': 2000,  # traces per run
    'budget_ms': 10000,  # time budget per run (generation stops at the first of count and budget)
    'min_len': 1,
    'max_len': 10,  # steps per trace (injected steps count)
    'retry': 0.15,
    'replay': 0.08,
    'reorder': 0.08,
    'restart': 0.06,
    'batch': 200,  # traces per evaluator round trip
    'time_backwards': False,  # True: shrinking may make `now` go down between steps
}
INJECTIONS = ('retry', 'replay', 'reorder', 'restart')
SHRINK_BUDGET = 3000  # evaluator round trips per shrink
MAX_PER_CANDIDATE = 20


def options(block):
    """The "trace" block of a diff entry with the defaults filled in; raises ValueError on a bad value."""
    block = {} if block is True else dict(block or {})
    unknown = sorted(set(block) - set(DEFAULTS))
    if unknown:
        raise ValueError('unknown "trace" key(s): %s; known: %s' % (', '.join(unknown), ', '.join(DEFAULTS)))
    out = dict(DEFAULTS, **block)
    for k in ('count', 'budget_ms', 'min_len', 'max_len', 'batch'):
        if not isinstance(out[k], int) or isinstance(out[k], bool) or out[k] < (0 if k == 'count' else 1):
            raise ValueError('"trace.%s" must be a positive whole number' % k)
    if out['min_len'] > out['max_len']:
        raise ValueError('"trace.min_len" is above "trace.max_len"')
    for k in INJECTIONS:
        if not isinstance(out[k], (int, float)) or isinstance(out[k], bool) or not 0 <= out[k] <= 1:
            raise ValueError('"trace.%s" must be a rate from 0 to 1' % k)
    return out


def _default_advance(rng):
    return rng.pick([0, 0, 1, 1, 2, 3, 5, rng.num(0, 25)])


def generate(rng, mod, opts, stats):
    """One trace: a list of steps. Each step carries "inj" (which fault made it) when it was injected."""
    advance = getattr(mod, 'advance', _default_advance)
    ctx = {}
    want = rng.int(opts['min_len'], opts['max_len'])
    now = mod.start_time(rng) if hasattr(mod, 'start_time') else 0
    steps, sent = [], []
    while len(steps) < want:
        now += advance(rng)
        e = mod.event(rng, ctx)
        steps.append({'event': e, 'now': now})
        sent.append(e)
        if len(steps) < want and rng.float() < opts['retry']:
            if rng.bool():
                now += advance(rng)
            steps.append({'event': copy.deepcopy(e), 'now': now, 'inj': 'retry'})
            stats['retry'] += 1
        if len(steps) < want and len(sent) > 1 and rng.float() < opts['replay']:
            steps.append({'event': copy.deepcopy(rng.pick(sent[:-1])), 'now': now, 'inj': 'replay'})
            stats['replay'] += 1
        if len(steps) >= 2 and 'event' in steps[-1] and 'event' in steps[-2] and rng.float() < opts['reorder']:
            a, b = steps[-2], steps[-1]
            steps[-2] = dict(a, event=b['event'], inj='reorder')
            steps[-1] = dict(b, event=a['event'], inj='reorder')
            stats['reorder'] += 1
        if len(steps) < want and rng.float() < opts['restart']:
            steps.append({'restart': True, 'inj': 'restart'})
            stats['restart'] += 1
    return steps[:want]


def wire(steps):
    """The steps as the evaluator gets them (without the "inj" notes)."""
    return [{'restart': True} if s.get('restart') else {'event': s['event'], 'now': s['now']} for s in steps]


def retag(steps):
    """Notes for a shrunk trace: after shrinking, a step is a "retry" when it repeats the event just before it, a
    "replay" when it repeats an older one (the generator's notes may no longer be true)."""
    out, events = [], []
    for s in steps:
        if s.get('restart'):
            out.append({'restart': True})
            continue
        s = {'event': s['event'], 'now': s['now']}
        if events and canon(events[-1]) == canon(s['event']):
            s['inj'] = 'retry'
        elif any(canon(e) == canon(s['event']) for e in events):
            s['inj'] = 'replay'
        events.append(s['event'])
        out.append(s)
    return out


def divergence(ref, got):
    """(index, reference step, candidate step) at the first step where they differ, else None."""
    for i in range(max(len(ref), len(got))):
        a = ref[i] if i < len(ref) else {'__missing__': True}
        b = got[i] if i < len(got) else {'__missing__': True}
        if canon(a) != canon(b):
            return i, a, b
    return None


def is_throw(v):
    return isinstance(v, dict) and '__throws__' in v


def describe(step):
    if step.get('restart'):
        return 'restart'
    return '%s@%s%s' % (json.dumps(step['event'], ensure_ascii=False, sort_keys=True), step['now'],
                        ' (%s)' % step['inj'] if step.get('inj') else '')


class TraceEvaluator(Evaluator):
    def run_traces(self, traces):
        n = sum(len(t) for t in traces)
        self.p.stdin.write(json.dumps({'traces': [wire(t) for t in traces]}) + '\n')
        self.p.stdin.flush()
        out = self._read(forge_diff.CALL_TIMEOUT_S + 0.005 * n)
        if out is None:
            self.hung = True
            raise EvaluatorError('no answer for %d traces within the time limit (a hang?); first trace: %s'
                                 % (len(traces), canon(wire(traces[0]))[:200]))
        if not isinstance(out.get('results'), list) or len(out['results']) != len(traces):
            raise EvaluatorError('the evaluator did not answer the traces: %s' % canon(out)[:200])
        return out['results']


def run_trace_entry(cmd, cwd, env, events_path, seed, block, reg_dir, count=None, budget_ms=None):
    """Run one trace diff entry. Returns a result dict shaped like forge_diff.run_entry's (plus trace numbers)."""
    started = time.time()
    opts = options(block)
    count = opts['count'] if count is None else count
    budget_ms = opts['budget_ms'] if budget_ms is None else budget_ms
    result = {'mode': 'trace', 'inputs': 0, 'traces': 0, 'steps': 0, 'out_of_domain': 0, 'not_judged': 0,
              'replayed': 0, 'seed': seed, 'disagreements': [], 'options': opts,
              'injected': {k: 0 for k in INJECTIONS}, 'decisions': {'total': 0, 'agree': 0, 'failing': []}}
    ev = None
    try:
        mod = load_inputs_module(events_path)
        ev = TraceEvaluator(cmd, cwd, env)
        if not ev.trace_mode:
            raise EvaluatorError('the adapter exports no `trace` (reference and candidate machines); see cli/forge_trace.py')
        names = ev.candidates
        result['candidates'] = names
        cache = {}
        calls = [0]

        def outputs(steps):
            key = canon(wire(steps))
            if key not in cache:
                if len(cache) > 20000:
                    cache.clear()
                calls[0] += 1
                cache[key] = ev.run_traces([steps])[0]
            return cache[key]

        ev_ok = getattr(mod, 'event_in_domain', lambda _e: True)
        tr_ok = getattr(mod, 'trace_in_domain', lambda _s: True)

        def in_domain(steps):
            if not steps or not tr_ok(steps):
                return False
            last = None
            for s in steps:
                if s.get('restart'):
                    continue
                if not ev_ok(s['event']):
                    return False
                if not opts['time_backwards'] and last is not None and s['now'] < last:
                    return False
                last = s['now']
            return True

        def judge(name, res):
            """(index, ref, got, group) if candidate `name` disagrees on this result, else None. A trace the model
            itself cannot take (the reference throws first) is not judged."""
            got = res['candidates'].get(name) or []
            d = divergence(res['reference'], got)
            if d is None:
                return None
            i, a, b = d
            if is_throw(a) and not is_throw(b):
                return 'not-judged'
            return i, a, b, signature(a, b) if not is_throw(b) else 'THROWS %s' % b['__throws__']

        def fails(name, steps, group):
            """Index of the first differing step if `steps` still fails for `name` with the same group, else None."""
            if not in_domain(steps):
                return None
            d = judge(name, outputs(steps))
            return d[0] if d and d != 'not-judged' and d[3] == group else None

        def shrink(name, steps, group):
            calls[0] = 0
            i = fails(name, steps, group)
            steps = steps[:i + 1]

            def ddmin(steps):
                n = 2
                while len(steps) >= 2 and calls[0] < SHRINK_BUDGET:
                    chunk = max(1, len(steps) // n)
                    for start in range(0, len(steps), chunk):
                        cand = steps[:start] + steps[start + chunk:]
                        j = fails(name, cand, group)
                        if j is not None:
                            steps, n = cand[:j + 1], max(n - 1, 2)
                            break
                    else:
                        if chunk == 1:
                            break
                        n = min(len(steps), n * 2)
                return steps

            def simpler(steps):
                """Variants with one field simpler: all times shifted to start at 0, one time moved toward the step
                before, one event's fields shrunk (forge_diff.shrinks)."""
                times = [s['now'] for s in steps if not s.get('restart')]
                if times and times[0] != 0:
                    yield [s if s.get('restart') else dict(s, now=s['now'] - times[0]) for s in steps]
                prev = 0
                for k, s in enumerate(steps):
                    if s.get('restart'):
                        continue
                    for t in [prev] + [t for t in shrinks(s['now']) if t != prev]:
                        if t != s['now'] and (opts['time_backwards'] or t >= prev):
                            # Later times move by the same amount, so gaps after this step stay.
                            d = t - s['now']
                            yield steps[:k] + [x if x.get('restart') else dict(x, now=x['now'] + d) for x in steps[k:]]
                    prev = s['now']
                for k, s in enumerate(steps):
                    if s.get('restart'):
                        continue
                    for e in shrinks(s['event']):
                        yield steps[:k] + [dict(s, event=e)] + steps[k + 1:]

            steps = ddmin(steps)
            improved = True
            while improved and calls[0] < SHRINK_BUDGET:
                improved = False
                for cand in simpler(steps):
                    if calls[0] >= SHRINK_BUDGET:
                        break
                    j = fails(name, cand, group)
                    if j is not None:
                        steps, improved = cand[:j + 1], True
                        break
            return ddmin(steps)

        seen = set()
        groups = set()

        def record(name, steps, is_new, meta):
            res = outputs(steps)
            d = judge(name, res)
            if not d or d == 'not-judged':
                return
            i, a, b, group = d
            key = name + canon(wire(steps))
            if key in seen:
                return
            seen.add(key)
            groups.add((name, group))
            fname = '%s-trace-%s.json' % (name, hashlib.sha256(key.encode()).hexdigest()[:10])
            path = os.path.join(reg_dir, fname)
            entry = {'candidate': name, 'trace': steps, 'step': i, 'reference': a, 'got': b, 'group': group,
                     'kind': 'TRACE_THROWS' if group.startswith('THROWS') else 'TRACE_DISAGREE',
                     'shrunk_len': len(steps), 'file': fname, 'new': is_new}
            entry.update(meta)
            if is_new and not os.path.exists(path):
                os.makedirs(reg_dir, exist_ok=True)
                forge_diff.write_atomic(path, json.dumps(dict(
                    {k: entry[k] for k in ('candidate', 'trace', 'step', 'reference', 'got', 'group')}, seed=seed,
                    **meta, found_at=datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')),
                    indent=2, ensure_ascii=False) + '\n')
            result['disagreements'].append(entry)

        # 1. Saved failing traces run first.
        if os.path.isdir(reg_dir):
            for f in sorted(os.listdir(reg_dir)):
                if f.endswith('.json') and '-trace-' in f:
                    case = json.load(open(os.path.join(reg_dir, f), encoding='utf-8'))
                    if case.get('candidate') in names and isinstance(case.get('trace'), list):
                        result['replayed'] += 1
                        record(case['candidate'], case['trace'], False,
                               {k: case[k] for k in ('trace_index', 'original_len') if k in case})
        # 2. Fresh traces, in batches, within the count and the time budget.
        rng = forge_diff.Rng(seed)
        tried = set()
        index = 0
        while index < count and (time.time() - started) * 1000 < budget_ms:
            batch = []
            while len(batch) < opts['batch'] and index + len(batch) < count:
                t = generate(rng, mod, opts, result['injected'])
                batch.append(t)
            outs = ev.run_traces(batch)
            for k, (steps, res) in enumerate(zip(batch, outs)):
                cache.setdefault(canon(wire(steps)), res)
                result['traces'] += 1
                result['steps'] += len(steps)
                if not in_domain(steps):
                    result['out_of_domain'] += 1
                    continue
                judged = False
                for name in names:
                    d = judge(name, res)
                    if d == 'not-judged':
                        judged = True
                        continue
                    if not d or sum(x['candidate'] == name for x in result['disagreements']) >= MAX_PER_CANDIDATE:
                        continue
                    if (name, d[3]) in tried or (name, d[3]) in groups:
                        continue
                    tried.add((name, d[3]))
                    small = shrink(name, steps, d[3])
                    record(name, retag(small), True, {'trace_index': index + k, 'original_len': len(steps),
                                               'shrink_calls': calls[0]})
                result['not_judged'] += judged
            index += len(batch)
            if len(cache) > 20000:
                cache.clear()
        result['inputs'] = result['traces']
        result['per_candidate'] = {n: sum(d['candidate'] == n for d in result['disagreements']) for n in names}
    except (EvaluatorError, ValueError) as e:
        result['error'] = str(e)
    except Exception:  # noqa: BLE001
        import traceback
        result['error'] = traceback.format_exc()[-800:]
    finally:
        if ev:
            ev.close()
    result['ms'] = int((time.time() - started) * 1000)
    return result


def report_lines(name, r, repro):
    """Text lines for one trace entry (used by forge_check.diff_report)."""
    inj = ', '.join('%s %d' % (k, r['injected'][k]) for k in INJECTIONS)
    head = '%d traces, %d steps (injected: %s), %d saved traces replayed' % (
        r['traces'], r['steps'], inj, r['replayed'])
    if r['verdict'] == 'DIFF_OK':
        return ['✓ %s: trace fuzz: %s, 0 disagreements for %s, %d ms, seed %s'
                % (name, head, ', '.join(r.get('candidates') or []), r['ms'], r['seed'])]
    lines = ['✗ %s: trace fuzz: %s, %d ms, seed %s; disagreements per candidate: %s'
             % (name, head, r['ms'], r['seed'], ', '.join('%s %d' % kv for kv in (r.get('per_candidate') or {}).items()))]
    for d in sorted(r['disagreements'], key=lambda x: (x['candidate'], x['shrunk_len'])):
        lines.append('  %s%s: %d-step trace (shrunk from %s), differs at step %d:' % (
            'NEW ' if d.get('new') else '', d['candidate'], d['shrunk_len'], d.get('original_len', '?'), d['step'] + 1))
        for k, s in enumerate(d['trace']):
            lines.append('    %d. %s' % (k + 1, describe(s)))
        lines.append('    model says %s; %s says %s  [%s]' % (
            json.dumps(d['reference'], ensure_ascii=False)[:160], d['candidate'],
            json.dumps(d['got'], ensure_ascii=False)[:160], d['file']))
        if d.get('trace_index') is not None and d.get('new'):
            lines.append('    again: %s' % repro(d['trace_index'] + 1))
    lines.append('  Each one is a bug in that code, or a place where the model says something other than what you '
                 'meant. Saved traces run first next time.')
    return lines
