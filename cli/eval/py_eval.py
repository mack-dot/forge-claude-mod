"""Forge diff evaluator for Python: speaks the line protocol of cli/forge_diff.py.
Usage: python3 py_eval.py <adapter.py>
The adapter defines reference(input) and candidates = {name: function}. Test code: NOT proved.

Messy inputs (the unproved wrapper that turns app/JSON values into Dafny values): the adapter may define
wrapper(inp) or wrappers = {name: function}, which call the app's REAL wrapper with the raw values, and
fail_closed(inp), the spec's fail-closed answer for a value the proved rule does not take (None when it takes it).
Messy inputs go only to the wrappers."""
import copy
import importlib.util
import json
import os
import sys

path = os.path.abspath(sys.argv[1])
sys.path.insert(0, os.path.dirname(path))
spec = importlib.util.spec_from_file_location('forge_diff_adapter', path)
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)


def _trace_mode(spec):
    """Trace mode (cli/forge_trace.py): the adapter defines trace = {'reference': machine, 'candidates': {name:
    machine}}, machine = an object or dict with init(), step(state, event, now) -> {'ok': state} | {'reject': reason},
    view(state) -> JSON, and optionally save(state) / load(saved). A rejected step keeps the state it got (so a step
    that changes it before it rejects shows up). "restart" runs load(save(state)) through JSON text; without
    save/load it changes nothing."""
    def get(m, k):
        return m.get(k) if isinstance(m, dict) else getattr(m, k, None)

    machines = dict(spec.get('candidates') or {})
    tnames = list(machines)
    machines[None] = spec['reference']

    def run(m, steps):
        out = []
        try:
            s = get(m, 'init')()
        except Exception as e:  # noqa: BLE001
            return [{'__throws__': type(e).__name__}]
        save, load, view, step = get(m, 'save'), get(m, 'load'), get(m, 'view'), get(m, 'step')
        for st in steps:
            try:
                if st.get('restart'):
                    if save and load:
                        s = load(json.loads(json.dumps(save(s))))
                    v = 'restart'
                else:
                    r = step(s, copy.deepcopy(st['event']), st['now'])
                    if isinstance(r, dict) and 'ok' in r:
                        s, v = r['ok'], 'ok'
                    elif isinstance(r, dict) and 'reject' in r:
                        v = 'reject:%s' % r['reject']
                    else:
                        raise TypeError('step returned neither {"ok": ...} nor {"reject": ...}')
                out.append({'v': v, 's': json.loads(json.dumps(view(s)))})
            except Exception as e:  # noqa: BLE001
                out.append({'__throws__': type(e).__name__})
                break
        return out

    sys.stdout.write(json.dumps({'candidates': tnames, 'trace': True}) + '\n')
    sys.stdout.flush()
    for line in sys.stdin:
        if not line.strip():
            continue
        results = [{'reference': run(machines[None], steps), 'candidates': {n: run(machines[n], steps) for n in tnames}}
                   for steps in json.loads(line)['traces']]
        sys.stdout.write(json.dumps({'results': results}) + '\n')
        sys.stdout.flush()
    sys.exit(0)


if getattr(adapter, 'trace', None):
    _trace_mode(adapter.trace)

wrappers = dict(getattr(adapter, 'wrappers', None) or ({'wrapper': adapter.wrapper} if hasattr(adapter, 'wrapper') else {}))
every = dict(getattr(adapter, 'candidates', None) or {}, **wrappers)
names = list(every)


def call(fn, inp):
    try:
        out = fn(copy.deepcopy(inp))
        return {'__none__': True} if out is None else out
    except Exception as e:  # noqa: BLE001
        return {'__throws__': type(e).__name__}


sys.stdout.write(json.dumps({'candidates': names, 'messy': list(wrappers)}) + '\n')
sys.stdout.flush()
for line in sys.stdin:
    if not line.strip():
        continue
    msg = json.loads(line)
    inp = msg['input']
    if msg.get('messy'):
        ref = call(adapter.fail_closed, inp) if hasattr(adapter, 'fail_closed') else {'__none__': True}
        if ref == {'__none__': True}:
            ref = call(adapter.reference, inp)
        out = {'reference': ref, 'candidates': {n: call(wrappers[n], inp) for n in wrappers}}
        sys.stdout.write(json.dumps(out) + '\n')  # ASCII escapes: a lone surrogate stays valid
        sys.stdout.flush()
        continue
    out = {'reference': call(adapter.reference, inp), 'candidates': {n: call(every[n], inp) for n in names}}
    sys.stdout.write(json.dumps(out, ensure_ascii=False) + '\n')
    sys.stdout.flush()
