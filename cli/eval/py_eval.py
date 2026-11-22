"""Forge diff evaluator for Python: speaks the line protocol of cli/forge_diff.py.
Usage: python3 py_eval.py <adapter.py>
The adapter defines reference(input) and candidates = {name: function}. Test code: NOT proved."""
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
names = list(adapter.candidates)


def call(fn, inp):
    try:
        out = fn(copy.deepcopy(inp))
        return {'__none__': True} if out is None else out
    except Exception as e:  # noqa: BLE001
        return {'__throws__': type(e).__name__}


sys.stdout.write(json.dumps({'candidates': names}) + '\n')
sys.stdout.flush()
for line in sys.stdin:
    if not line.strip():
        continue
    inp = json.loads(line)['input']
    out = {'reference': call(adapter.reference, inp), 'candidates': {n: call(adapter.candidates[n], inp) for n in names}}
    sys.stdout.write(json.dumps(out, ensure_ascii=False) + '\n')
    sys.stdout.flush()
