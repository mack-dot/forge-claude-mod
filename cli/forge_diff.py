"""The language-neutral core of `forge_check.py diff`.

It compares the code under test (the shipped function, the wrapper around the proved code) with the compiled
proved rule, in any language, through a small line protocol:

  inputs module (Python, one per rule, shared by every language):
      generate(rng) -> one input (a plain JSON value)
      in_domain(input) -> optional; False skips the input (compiled Dafny code does not check `requires`)
      decisions -> optional [{'id': 'D1 ...', 'input': ...}]: the person's recorded decisions, run every time

  evaluator (one process per language, started once per run):
      first line out:  {"candidates": ["name", ...]}
      then, per line in: {"input": <json>}
      per line out:    {"reference": <json>, "candidates": {"name": <json>, ...}}
      An exception in a function is reported as {"__throws__": "<ErrorName>"}.
  Forge ships evaluators for JavaScript/TypeScript (cli/eval/node_eval.mjs) and Python (cli/eval/py_eval.py).
  Any other language (Go, Rust, Java, C#, ...) plugs in as a "cmd" entry: a program that speaks the protocol.

This core does the rest: fresh seeds, the decisions, replay of saved cases, shrinking (the failure signature
stays fixed, so a disagreement cannot slide into a different bug), one saved case per kind of failure, and a
time limit per call (a hang is reported, not waited on). Test code: NOT proved.
"""
import copy
import datetime
import hashlib
import importlib.util
import json
import os
import random
import select
import subprocess
import sys
import time

CALL_TIMEOUT_S = 2.0


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
        """Small numbers most of the time, boundaries sometimes."""
        if self.r.random() < 0.25:
            return self.pick([v for v in (lo, hi, lo + 1, hi - 1, 0, 1, -1) if lo <= v <= hi])
        return self.r.randint(lo, hi)

    def string(self, max_len=8, alphabet='ab c-_.A1é\U0001F600'):
        return ''.join(self.pick(alphabet) for _ in range(self.r.randint(0, max_len)))

    def array(self, gen, max_len=5): return [gen() for _ in range(self.r.randint(0, max_len))]
    def weird(self): return self.pick(self.WEIRD)


def canon(v): return json.dumps(v, sort_keys=True, ensure_ascii=False)


def signature(a, b):
    """How two outputs differ, without the numbers: which fields differ, booleans and short strings as values."""
    def leaf(x):
        return json.dumps(x) if x is None or isinstance(x, bool) or (isinstance(x, str) and len(x) <= 20) \
            else type(x).__name__
    if isinstance(a, dict) and isinstance(b, dict):
        return ','.join('%s:%s' % (k, signature(a.get(k), b.get(k))) for k in sorted(set(a) | set(b))
                        if canon(a.get(k)) != canon(b.get(k)))
    return '%s>%s' % (leaf(a), leaf(b))


def shrinks(v):
    """Smaller variants of a JSON value, smallest first."""
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
        d = abs(v) // 2  # toward 0 by v/2, v/4, ..., 1
        while d >= 1:
            yield v - d if v > 0 else v + d
            d //= 2
    elif isinstance(v, dict):
        for k in list(v):
            for s in shrinks(v[k]):
                yield dict(v, **{k: s})


class EvaluatorError(Exception):
    pass


class Evaluator:
    """One evaluator process, spoken to line by line."""

    def __init__(self, cmd, cwd, env):
        self.p = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, universal_newlines=True, bufsize=1, encoding='utf-8')
        hello = self._read(30.0)
        if not isinstance(hello, dict) or not isinstance(hello.get('candidates'), list):
            raise EvaluatorError('the evaluator did not start: %s' % self._stderr())
        self.candidates = hello['candidates']

    def _stderr(self):
        try:
            self.p.kill()
            return (self.p.stderr.read() or '')[-800:]
        except Exception:  # noqa: BLE001
            return ''

    def _read(self, timeout):
        ready, _, _ = select.select([self.p.stdout], [], [], timeout)
        if not ready:
            return None
        line = self.p.stdout.readline()
        if not line:
            raise EvaluatorError('the evaluator stopped: %s' % self._stderr())
        return json.loads(line)

    def run(self, inp):
        self.p.stdin.write(json.dumps({'input': inp}, ensure_ascii=False) + '\n')
        self.p.stdin.flush()
        out = self._read(CALL_TIMEOUT_S)
        if out is None:
            raise EvaluatorError('no answer within %.0f s for input %s (a hang?)' % (CALL_TIMEOUT_S, canon(inp)[:200]))
        return out

    def close(self):
        try:
            self.p.stdin.close()
            self.p.wait(timeout=5)
        except Exception:  # noqa: BLE001
            self.p.kill()


def load_inputs_module(path):
    spec = importlib.util.spec_from_file_location('forge_diff_inputs', path)
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, os.path.dirname(path))
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.path.pop(0)
    return mod


def run_entry(cmd, cwd, env, inputs_path, seed, max_inputs, budget_ms, reg_dir):
    """Run one diff entry. Returns the result dict (same shape for every language)."""
    started = time.time()
    result = {'inputs': 0, 'out_of_domain': 0, 'replayed': 0, 'seed': seed, 'disagreements': [],
              'decisions': {'total': 0, 'agree': 0, 'failing': []}}
    ev = None
    try:
        gen = load_inputs_module(inputs_path)
        in_domain = getattr(gen, 'in_domain', lambda _: True)
        ev = Evaluator(cmd, cwd, env)
        names = ev.candidates
        result['candidates'] = names
        cache = {}

        def outputs(inp):
            key = canon(inp)
            if key not in cache:
                if len(cache) > 20000:
                    cache.clear()
                cache[key] = ev.run(copy.deepcopy(inp))
            return cache[key]

        def disagrees(name, inp):
            if not in_domain(inp):
                return None
            o = outputs(inp)
            ref, got = o['reference'], o['candidates'].get(name)
            return None if canon(ref) == canon(got) else (ref, got)

        def shrink(name, inp):
            sig = signature(*disagrees(name, inp))
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

        seen, kinds = set(), set()

        def record(name, inp, is_new):
            d = disagrees(name, inp)
            if not d:
                return
            key = name + canon(inp)
            if key in seen:
                return
            seen.add(key)
            kinds.add(name + '|' + signature(*d))
            fname = '%s-%s.json' % (name, hashlib.sha256(key.encode()).hexdigest()[:10])
            path = os.path.join(reg_dir, fname)
            if is_new and not os.path.exists(path):
                os.makedirs(reg_dir, exist_ok=True)
                with open(path, 'w', encoding='utf-8') as f:
                    f.write(json.dumps({'candidate': name, 'input': inp, 'reference': d[0], 'got': d[1], 'seed': seed,
                                        'found_at': datetime.datetime.now(datetime.timezone.utc).isoformat(
                                            timespec='seconds')}, indent=2, ensure_ascii=False) + '\n')
            result['disagreements'].append({'candidate': name, 'input': inp, 'reference': d[0], 'got': d[1],
                                            'file': fname, 'new': is_new})

        # 1. Saved cases.
        if os.path.isdir(reg_dir):
            for f in sorted(os.listdir(reg_dir)):
                if f.endswith('.json'):
                    case = json.load(open(os.path.join(reg_dir, f), encoding='utf-8'))
                    if case.get('candidate') in names:
                        result['replayed'] += 1
                        record(case['candidate'], case['input'], False)
        # 2. The person's recorded decisions (reported, not saved again; their kinds are then skipped below).
        for dec in getattr(gen, 'decisions', []):
            result['decisions']['total'] += 1
            bad = [n for n in names if disagrees(n, dec['input'])]
            if bad:
                result['decisions']['failing'].append({'id': dec['id'], 'candidates': bad})
            else:
                result['decisions']['agree'] += 1
            for n in bad:
                kinds.add(n + '|' + signature(*disagrees(n, dec['input'])))
        # 3. Fresh random inputs within the input and time budget.
        rng = Rng(seed)
        while result['inputs'] < max_inputs and (time.time() - started) * 1000 < budget_ms:
            inp = gen.generate(rng)
            result['inputs'] += 1
            if not in_domain(inp):
                result['out_of_domain'] += 1
                continue
            for name in names:
                if sum(d['candidate'] == name for d in result['disagreements']) >= 5:
                    continue
                d = disagrees(name, inp)
                if not d or (name + '|' + signature(*d)) in kinds:
                    continue
                record(name, shrink(name, inp), True)
        result['per_candidate'] = {n: sum(d['candidate'] == n for d in result['disagreements']) for n in names}
    except EvaluatorError as e:
        result['error'] = str(e)
    except Exception as e:  # noqa: BLE001
        import traceback
        result['error'] = traceback.format_exc()[-800:]
    finally:
        if ev:
            ev.close()
    result['ms'] = int((time.time() - started) * 1000)
    return result
