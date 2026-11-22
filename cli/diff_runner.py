"""forge diff runner for Python. Started by `forge_check.py diff`; prints one JSON line.

The adapter module (written once per check, usually by Claude with /forge:diff-setup) defines:
  generate(rng)     -> one input (plain JSON value); rng helpers below; rng.weird() gives odd values
  reference(input)  -> the output of the compiled PROVED rule for that input (plain JSON value)
  candidates        -> {name: function(input) -> output}: the code under test
  in_domain(input)  -> optional; False skips the input (compiled Dafny code does not check `requires`)
Every candidate output is compared with the reference output (as JSON). A disagreement is shrunk to a
small input that still disagrees and saved as a regression case; saved cases run first next time.
The adapter and this runner are test code: NOT proved.
"""
import copy
import datetime
import hashlib
import importlib.util
import json
import os
import random
import sys
import time

adapter_path, seed_arg, max_inputs_arg, budget_ms_arg, reg_dir = sys.argv[1:6]
started = time.time()
sys.path.insert(0, os.path.dirname(os.path.abspath(adapter_path)))


class Rng:
    WEIRD = ['', ' ', '  x ', 'X', 'x', 'é', 'é', '\U0001F600', '\x00', 'None', 'null', '0', '-0',
             'NaN', 'a' * 300]

    def __init__(self, seed):
        self.r = random.Random(seed)

    def float(self): return self.r.random()
    def bool(self): return self.r.random() < 0.5
    def int(self, lo, hi): return self.r.randint(lo, hi)
    def pick(self, seq): return seq[self.r.randrange(len(seq))]

    def num(self, lo, hi):
        if self.r.random() < 0.25:
            return self.pick([v for v in (lo, hi, lo + 1, hi - 1, 0, 1, -1) if lo <= v <= hi])
        return self.r.randint(lo, hi)

    def string(self, max_len=8, alphabet='ab c-_.A1é\U0001F600'):
        return ''.join(self.pick(alphabet) for _ in range(self.r.randint(0, max_len)))

    def array(self, gen, max_len=5): return [gen() for _ in range(self.r.randint(0, max_len))]
    def weird(self): return self.pick(self.WEIRD)


def canon(v): return json.dumps(v, sort_keys=True, ensure_ascii=False)


def call(fn, inp):
    try:
        out = fn(copy.deepcopy(inp))
        return {'__none__': True} if out is None else out
    except Exception as e:  # noqa: BLE001
        return {'__throws__': type(e).__name__}


def shrinks(v):
    if isinstance(v, list):
        if v:
            yield []
        for i in range(len(v)):
            yield v[:i] + v[i + 1:]
        for i in range(len(v)):
            for s in shrinks(v[i]):
                yield v[:i] + [s] + v[i + 1:]
    elif isinstance(v, str):
        if v:
            yield ''
        if len(v) > 1:
            yield v[:len(v) // 2]
        for i in range(min(len(v), 16)):
            yield v[:i] + v[i + 1:]
    elif isinstance(v, bool):
        if v:
            yield False
    elif isinstance(v, int) and v != 0:
        yield 0
        if v < 0:
            yield -v
        d = abs(v) // 2  # move toward 0 by v/2, v/4, ..., 1
        while d >= 1:
            yield v - d if v > 0 else v + d
            d //= 2
    elif isinstance(v, dict):
        for k in list(v):
            for s in shrinks(v[k]):
                yield dict(v, **{k: s})


spec = importlib.util.spec_from_file_location('forge_diff_adapter', os.path.abspath(adapter_path))
adapter = importlib.util.module_from_spec(spec)
result = {'inputs': 0, 'out_of_domain': 0, 'replayed': 0, 'disagreements': [], 'seed': int(seed_arg)}
try:
    spec.loader.exec_module(adapter)
    in_domain = getattr(adapter, 'in_domain', lambda _: True)
    names = list(adapter.candidates)
    result['candidates'] = names

    def disagrees(name, inp):
        if not in_domain(inp):
            return None
        ref, got = call(adapter.reference, inp), call(adapter.candidates[name], inp)
        return None if canon(ref) == canon(got) else (ref, got)

    def signature(a, b):
        # How two outputs differ, without the numbers; shrinking keeps it fixed (no sliding into another bug).
        def leaf(x):
            return json.dumps(x) if x is None or isinstance(x, bool) or (isinstance(x, str) and len(x) <= 20) \
                else type(x).__name__
        if isinstance(a, dict) and isinstance(b, dict):
            return ','.join('%s:%s' % (k, signature(a.get(k), b.get(k))) for k in sorted(set(a) | set(b))
                            if canon(a.get(k)) != canon(b.get(k)))
        return '%s>%s' % (leaf(a), leaf(b))

    def shrink(name, inp):
        first = disagrees(name, inp)
        sig = signature(*first)
        best, steps, improved = inp, 0, True
        while improved and steps < 300:
            improved = False
            for s in shrinks(best):
                steps += 1
                if steps > 300:
                    break
                d = disagrees(name, s)
                if d and signature(*d) == sig:
                    best, improved = s, True
                    break
        return best

    seen = set()
    kinds = set()

    def record(name, inp, is_new):
        d = disagrees(name, inp)
        if not d:
            return
        key = name + canon(inp)
        if key in seen:
            return
        seen.add(key)
        fname = '%s-%s.json' % (name, hashlib.sha256(key.encode()).hexdigest()[:10])
        path = os.path.join(reg_dir, fname)
        if is_new and not os.path.exists(path):
            os.makedirs(reg_dir, exist_ok=True)
            with open(path, 'w', encoding='utf-8') as f:
                f.write(json.dumps({'candidate': name, 'input': inp, 'reference': d[0], 'got': d[1],
                                    'seed': int(seed_arg), 'found_at': datetime.datetime.now(
                                        datetime.timezone.utc).isoformat(timespec='seconds')},
                                   indent=2, ensure_ascii=False) + '\n')
        result['disagreements'].append({'candidate': name, 'input': inp, 'reference': d[0], 'got': d[1],
                                        'file': fname, 'new': is_new})

    if os.path.isdir(reg_dir):
        for f in sorted(os.listdir(reg_dir)):
            if f.endswith('.json'):
                case = json.load(open(os.path.join(reg_dir, f), encoding='utf-8'))
                if case.get('candidate') in names:
                    result['replayed'] += 1
                    record(case['candidate'], case['input'], False)
                    d = disagrees(case['candidate'], case['input'])
                    if d:
                        kinds.add(case['candidate'] + '|' + signature(*d))
    rng = Rng(int(seed_arg))
    max_inputs, budget = int(max_inputs_arg), int(budget_ms_arg) / 1000.0
    while result['inputs'] < max_inputs and time.time() - started < budget:
        inp = adapter.generate(rng)
        result['inputs'] += 1
        if not in_domain(inp):
            result['out_of_domain'] += 1
            continue
        for name in names:
            if sum(d['candidate'] == name for d in result['disagreements']) >= 5:
                continue
            d = disagrees(name, inp)
            if not d:
                continue
            kind = name + '|' + signature(*d)
            if kind in kinds:  # one saved case per kind of failure is enough
                continue
            kinds.add(kind)
            record(name, shrink(name, inp), True)
    result['per_candidate'] = {n: sum(d['candidate'] == n for d in result['disagreements']) for n in names}
except Exception as e:  # noqa: BLE001
    import traceback
    result['error'] = traceback.format_exc()[-800:]
result['ms'] = int((time.time() - started) * 1000)
print(json.dumps(result, ensure_ascii=False))
