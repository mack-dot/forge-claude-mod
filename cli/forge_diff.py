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

  messy inputs (the wrapper that turns app/JSON values into Dafny values, which is not proved):
      the hello line may also name {"messy": ["wrapper", ...]}: candidates that take RAW host values; and
      {"undefined": true} if the language has `undefined` (JavaScript); {"skip": ["lone-surrogate", ...]}: messy kinds
      the language's JSON parser cannot carry (Rust: serde_json rejects a lone surrogate); these are not sent and
      are listed under "skipped_kinds" in the result.
      Then the core also sends {"input": <json>, "messy": true}: a generated input with ONE field replaced by an
      odd value (undefined, null, a number, a boolean, "", a lone surrogate, a very long text, an array, an object,
      the number as text) or removed. `undefined` travels as {"__undefined__": true}; the evaluator turns it into
      a real undefined. The answer has the same shape, with only the messy candidates. Its "reference" is the
      adapter's fail-closed answer (failClosed / fail_closed), or, where that returns undefined/None (the value
      is one the proved rule takes), the proved rule's answer. A throw from a messy candidate is always a
      disagreement of kind THROWS_ON_MESSY_INPUT.
  Forge ships evaluators for JavaScript/TypeScript (cli/eval/node_eval.mjs), Python (cli/eval/py_eval.py), and Go,
  Rust, Java and C# (cli/eval/<lang>/, built by eval_build.py); all of them take messy inputs.
  Any other language (Go, Rust, Java, C#, ...) plugs in as a "cmd" entry: a program that speaks the protocol.
  State machines (many events per case, with retries, replays, reordering and restarts) use the trace mode of the
  same evaluators: see cli/forge_trace.py.

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
MAX_PER_CANDIDATE = 40  # examples kept per candidate per run
MAX_PER_GROUP = 3  # differently shaped examples kept per kind of wrong answer
MESSY_PER_INPUT = 1  # messy variants tried per generated input once every (field, kind) pair has been tried
MESSY_MAX_DEPTH = 4  # how deep into nested objects and lists the messy fields go
MESSY_MAX_FIELDS = 64  # fields considered per input
MESSY_LIST_ITEMS = 2  # list elements considered per list
UNDEFINED = {'__undefined__': True}
MESSY_KINDS = ('undefined', 'null', 'number', 'boolean', 'empty-text', 'lone-surrogate', 'long-text', 'array',
               'object', 'number-as-text', 'removed')


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


def shape(v):
    """The input's shape without its exact values: which fields are empty, zero, negative, null, unusual text, big,
    and how many elements lists have. Two disagreements with the same output pattern but different input shapes
    are different kinds of failure (for example a missing tag versus an unknown tag)."""
    if v is None:
        return 'null'
    if isinstance(v, bool):
        return 'bool:%s' % v
    if isinstance(v, int):
        return 'zero' if v == 0 else ('neg' if v < 0 else ('big' if v > 2 ** 53 else 'pos'))
    if isinstance(v, float):
        return 'float'
    if isinstance(v, str):
        if v == '':
            return 'empty'
        if not v.strip():
            return 'space'
        return 'text' if v == v.strip() else 'padded'
    if isinstance(v, list):
        n = 'n0' if not v else ('n1' if len(v) == 1 else 'n+')
        return 'list:%s[%s]' % (n, ','.join(sorted({shape(x) for x in v})))
    if isinstance(v, dict):
        return '{%s}' % ','.join('%s:%s' % (k, shape(v[k])) for k in sorted(v))
    return type(v).__name__


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


def is_throw(v): return isinstance(v, dict) and set(v) == {'__throws__'}


def messy_fields(v, skip=(), path=(), depth=0, out=None):
    """The paths of the fields of an input (dict keys and the first list elements), bounded."""
    out = [] if out is None else out
    if depth >= MESSY_MAX_DEPTH:
        return out
    items = list(v.items()) if isinstance(v, dict) else list(enumerate(v[:MESSY_LIST_ITEMS])) if isinstance(v, list) else []
    for k, x in items:
        if len(out) >= MESSY_MAX_FIELDS:
            break
        p = path + (k,)
        if field_pattern(p) in skip:
            continue
        out.append(p)
        messy_fields(x, skip, p, depth + 1, out)
    return out


def field_pattern(path):
    """'limits[].scope': the field without list positions (used for coverage and grouping)."""
    return ''.join(('[]' if isinstance(k, int) else ('.' if i else '') + str(k)) for i, k in enumerate(path))


def field_name(path):
    return ''.join(('[%d]' % k if isinstance(k, int) else ('.' if i else '') + str(k)) for i, k in enumerate(path))


def _get(v, path):
    for k in path:
        if isinstance(v, dict) and k in v or isinstance(v, list) and isinstance(k, int) and k < len(v):
            v = v[k]
        else:
            raise KeyError(k)
    return v


LONG_TEXT = 'x' * 20000


def messy_value(kind, orig):
    """The odd value of this kind for a field whose generated value is `orig`; None if it does not apply."""
    num = isinstance(orig, (int, float)) and not isinstance(orig, bool)
    if kind == 'number-as-text' and not num:
        return None
    vals = {'undefined': UNDEFINED, 'null': None, 'number': 1.5 if num else 7, 'boolean': not orig if isinstance(orig, bool) else True,
            'empty-text': '', 'lone-surrogate': '\ud800', 'long-text': LONG_TEXT, 'array': [], 'object': {},
            'number-as-text': str(orig) if num else None}
    v = vals.get(kind)
    # The same JSON value as before (same type and equal; 1 and True differ): not messy. Cheaper than canon(), which
    # wrote the 20,000-character text once per field and kind (about 40% of a messy run's time).
    if v is not None and type(v) is type(orig) and v == orig or kind == 'null' and orig is None:
        return None
    return ('set', v)


def messy_applies(base, path, kind):
    try:
        orig = _get(base, path)
    except KeyError:
        return False
    if kind == 'removed':
        return isinstance(_get(base, path[:-1]), dict)
    return messy_value(kind, orig) is not None


def apply_messy(base, path, kind):
    """`base` with the field at `path` made messy (a new value); None if the field or the kind does not apply."""
    try:
        orig = _get(base, path)
    except KeyError:
        return None
    out = copy.deepcopy(base)
    parent = _get(out, path[:-1])
    if kind == 'removed':
        if not isinstance(parent, dict):
            return None
        del parent[path[-1]]
        return out
    mv = messy_value(kind, orig)
    if mv is None:
        return None
    parent[path[-1]] = copy.deepcopy(mv[1])
    return out


def dumps_any(v, **kw):
    """JSON text that may hold a lone surrogate (written as an escape, so the file stays valid JSON)."""
    return json.dumps(v, ensure_ascii=False, **kw).encode('utf-8', 'backslashreplace').decode('utf-8')


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
        self.messy = [n for n in hello.get('messy') or [] if n in self.candidates]
        self.has_undefined = hello.get('undefined') is True
        self.trace_mode = hello.get('trace') is True  # the adapter exports machines (cli/forge_trace.py)
        self.skip_kinds = [k for k in hello.get('skip') or [] if k in MESSY_KINDS]

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

    def run(self, inp, messy=False):
        msg = {'input': inp, 'messy': True} if messy else {'input': inp}
        self.p.stdin.write(json.dumps(msg, ensure_ascii=messy) + '\n')  # ASCII escapes carry a lone surrogate
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


def run_entry(cmd, cwd, env, inputs_path, seed, max_inputs, budget_ms, reg_dir, messy=True):
    """Run one diff entry. Returns the result dict (same shape for every language). `messy`: also send messy
    variants of the inputs to the candidates the evaluator names as raw-value wrappers (see the top of this file)."""
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

        # ---- messy inputs: one field of a generated input made odd; judged against the fail-closed answer.
        mnames = ev.messy if messy else []
        kinds = [k for k in MESSY_KINDS if (k != 'undefined' or ev.has_undefined) and k not in ev.skip_kinds]
        skip = set(getattr(gen, 'messy_skip', []) or [])
        mcache = {}
        mstats = {'inputs': 0, 'pairs_tried': 0, 'not_judged': 0, 'candidates': mnames, 'kinds': kinds}
        if ev.skip_kinds:
            mstats['skipped_kinds'] = ev.skip_kinds  # the evaluator's language cannot carry these (said in the report)

        def m_outputs(inp):
            key = canon(inp)
            if key not in mcache:
                if len(mcache) > 20000:
                    mcache.clear()
                mcache[key] = ev.run(copy.deepcopy(inp), messy=True)
            return mcache[key]

        def m_judge(name, inp):
            """(signature, reference, got) for a messy input the wrapper gets wrong, else None."""
            o = m_outputs(inp)
            ref, got = o['reference'], o['candidates'].get(name)
            if is_throw(got):
                return ('THROWS_ON_MESSY_INPUT %s' % got['__throws__'], ref, got)
            if is_throw(ref):  # no fail-closed answer and the proved rule cannot take it: not judged
                return None
            return None if canon(ref) == canon(got) else ('MESSY_ANSWER ' + signature(ref, got), ref, got)

        def m_shrink(name, base, path, kind, sig):
            """Shrink the rest of the input; the messy field and its kind stay (the smallest input that still
            fails the same way at the same field)."""
            best, steps, improved = base, 0, True
            while improved and steps < 300:
                improved = False
                for s in shrinks(best):
                    steps += 1
                    if steps > 300:
                        break
                    if not in_domain(s):
                        continue
                    m = apply_messy(s, path, kind)
                    d = m is not None and m_judge(name, m)
                    if d and d[0] == sig:
                        best, improved = s, True
                        break
            return best

        def m_record(name, inp, meta, is_new):
            d = m_judge(name, inp)
            if not d:
                return
            key = 'messy:' + name + canon(inp)
            if key in seen:
                return
            seen.add(key)
            remember(('%s|%s|%s' % (name, d[0], meta['pattern']), meta['as']))
            fname = '%s-messy-%s.json' % (name, hashlib.sha256(key.encode('utf-8', 'surrogatepass')).hexdigest()[:10])
            path = os.path.join(reg_dir, fname)
            if is_new and not os.path.exists(path):
                os.makedirs(reg_dir, exist_ok=True)
                with open(path, 'w', encoding='utf-8') as f:
                    f.write(dumps_any({'candidate': name, 'messy': meta, 'input': inp, 'reference': d[1], 'got': d[2],
                                       'seed': seed, 'found_at': datetime.datetime.now(datetime.timezone.utc).isoformat(
                                           timespec='seconds')}, indent=2) + '\n')
            result['disagreements'].append({'candidate': name, 'input': inp, 'reference': d[1], 'got': d[2],
                                            'file': fname, 'new': is_new, 'group': '%s at %s' % (d[0], meta['pattern']),
                                            'messy': meta,
                                            'kind': d[0].split(' ')[0]})

        mrng = random.Random(seed ^ 0x6d657373)  # its own stream: the plain inputs stay the same for a seed
        tried = set()

        def messy_round(base):
            pairs = [(p, k) for p in messy_fields(base, skip) for k in kinds if messy_applies(base, p, k)]
            fresh = [pk for pk in pairs if (field_pattern(pk[0]), pk[1]) not in tried]
            mrng.shuffle(fresh)
            pick = fresh[:max(MESSY_PER_INPUT, 12)] if fresh else [mrng.choice(pairs) for _ in range(MESSY_PER_INPUT)] if pairs else []
            for path, kind in pick:
                inp = apply_messy(base, path, kind)
                if inp is None:
                    continue
                tried.add((field_pattern(path), kind))
                mstats['inputs'] += 1
                if is_throw(m_outputs(inp)['reference']):
                    mstats['not_judged'] += 1
                for name in mnames:
                    if sum(d['candidate'] == name for d in result['disagreements']) >= MAX_PER_CANDIDATE:
                        continue
                    d0 = m_judge(name, inp)
                    if not d0:
                        continue
                    raw = 'messy|%s|%s|%s|%s' % (name, d0[0], field_pattern(path), kind)
                    if raw in raw_kinds:
                        continue
                    raw_kinds.add(raw)
                    small = m_shrink(name, base, path, kind, d0[0])
                    meta = {'field': field_name(path), 'pattern': field_pattern(path), 'as': kind}
                    if known(('%s|%s|%s' % (name, d0[0], meta['pattern']), kind)):
                        continue
                    m_record(name, apply_messy(small, path, kind), meta, True)

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

        seen = set()
        raw_kinds = set()
        groups = {}  # group -> set of input shapes already kept

        def known(k):
            return k is not None and (k[1] in groups.get(k[0], set()) or len(groups.get(k[0], set())) >= MAX_PER_GROUP)

        def remember(k):
            if k is not None:
                groups.setdefault(k[0], set()).add(k[1])

        def kind_of(name, inp):
            """Group = the kind of wrong answer (which output fields differ, and how); within a group, inputs of a
            different shape (empty, zero, missing, unknown text, ...) are kept as separate examples, up to
            MAX_PER_GROUP, because different bugs can give the same wrong answer."""
            d = disagrees(name, inp)
            return ('%s|%s' % (name, signature(*d)), shape(inp)) if d else None

        def record(name, inp, is_new):
            d = disagrees(name, inp)
            if not d:
                return
            key = name + canon(inp)
            if key in seen:
                return
            seen.add(key)
            remember(kind_of(name, inp))
            fname = '%s-%s.json' % (name, hashlib.sha256(key.encode()).hexdigest()[:10])
            path = os.path.join(reg_dir, fname)
            if is_new and not os.path.exists(path):
                os.makedirs(reg_dir, exist_ok=True)
                with open(path, 'w', encoding='utf-8') as f:
                    f.write(json.dumps({'candidate': name, 'input': inp, 'reference': d[0], 'got': d[1], 'seed': seed,
                                        'found_at': datetime.datetime.now(datetime.timezone.utc).isoformat(
                                            timespec='seconds')}, indent=2, ensure_ascii=False) + '\n')
            result['disagreements'].append({'candidate': name, 'input': inp, 'reference': d[0], 'got': d[1],
                                            'file': fname, 'new': is_new, 'group': signature(*d)})

        # 1. Saved cases.
        if os.path.isdir(reg_dir):
            for f in sorted(os.listdir(reg_dir)):
                if f.endswith('.json'):
                    case = json.load(open(os.path.join(reg_dir, f), encoding='utf-8'))
                    if case.get('messy') is not None:
                        if case.get('candidate') in mnames:
                            result['replayed'] += 1
                            m_record(case['candidate'], case['input'], case['messy'], False)
                    elif case.get('candidate') in names:
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
            for n in bad:  # a failing decision counts as one example of its group
                remember(kind_of(n, shrink(n, dec['input'])))
        # 3. Fresh random inputs within the input and time budget.
        rng = Rng(seed)
        while result['inputs'] < max_inputs and (time.time() - started) * 1000 < budget_ms:
            inp = gen.generate(rng)
            result['inputs'] += 1
            if not in_domain(inp):
                result['out_of_domain'] += 1
                continue
            if mnames:
                messy_round(inp)
            for name in names:
                if sum(d['candidate'] == name for d in result['disagreements']) >= MAX_PER_CANDIDATE:
                    continue
                d0 = disagrees(name, inp)
                if not d0:
                    continue
                raw = '%s|%s|%s' % (name, signature(*d0), shape(inp))
                if raw in raw_kinds:
                    continue
                raw_kinds.add(raw)  # skip shrinking this exact pattern again
                small = shrink(name, inp)
                if known(kind_of(name, small)):
                    continue
                record(name, small, True)
        if mnames:
            mstats['pairs_tried'] = len(tried)
            result['messy'] = mstats
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
