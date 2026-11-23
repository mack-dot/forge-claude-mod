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
