"""Fuzz tiers and coverage-guided steering for `forge_check.py diff` (`--tier`, `--steer`).

TIERS. How much the differential fuzz runs depends on where it runs. Optional "fuzz" block in .forge/forge.json:

    "fuzz": {"tiers": {"local":   {"inputs": 1500,    "budget_ms": 1500,    "steer": false},
                       "ci":      {"inputs": 100000,  "budget_ms": 600000,  "steer": true},
                       "nightly": {"inputs": 1000000, "budget_ms": 3600000, "steer": true}}}

  local    the end-of-turn run in Claude Code and a plain `diff` (the defaults above). A diff entry's own
           "inputs_per_run" / "budget_ms" still win here, as before.
  ci       `diff --tier ci` (forge-ci Action: diff-tier: ci).
  nightly  `diff --tier nightly` (a scheduled job).
  --inputs / --budget-ms on the command line win over every tier. Any field left out takes the default above.

STEERING (py and js entries). Blind random inputs reach a narrow branch (`if amount == 7777`) only by luck. A
steered run keeps a small corpus, as AFL does: every plain input that reaches a new step of the shipped code
(Python: a new (line, next line) step; JavaScript/TypeScript: a new V8 block) is kept, and most later inputs are
small changes of kept ones. A change may also put in a constant read from the watched files' comparisons
(`amount == 7777` gives the field `amount` the values 7776, 7777, 7778): a dictionary, as libFuzzer uses.
The coverage comes from the evaluator itself (cli/eval/forge_py_steer.py, cli/eval/node_steer.mjs), per input, so
the run also reports which lines it never reached ("legacy_wallet.py:7 was never reached").

Steering changes which inputs are tried, never how they are judged. Go, Rust, Java, C# and cmd entries are not
steered (no per-input coverage): they run blind. Test tooling: NOT proved.
"""
import ast
import copy
import json
import os
import re

import forge_coverage
import forge_diff

TIERS = {
    'local': {'inputs': 1500, 'budget_ms': 1500, 'steer': False},
    'ci': {'inputs': 100000, 'budget_ms': 600000, 'steer': True},
    'nightly': {'inputs': 1000000, 'budget_ms': 3600000, 'steer': True},
}
STEERED_LANGS = ('py', 'js')
FRESH = 0.25  # share of steered inputs that are fresh from generate() instead of a change of a kept one
DICT_SHARE = 0.4  # share of value changes that take a dictionary constant (mode "full" and "dict")
MAX_POOL = 256
WINDOW = 4096  # plain inputs remembered by number, for the evaluator's late "novel" answers
MODES = ('blind', 'corpus', 'dict', 'full')


class TierError(Exception):
    pass


def tier(cfg, name):
    """The settings of one tier: forge.json "fuzz.tiers.<name>" over the defaults."""
    name = name or 'local'
    if name not in TIERS:
        raise TierError('unknown fuzz tier %r; known: %s' % (name, ', '.join(TIERS)))
    out = dict(TIERS[name])
    fz = (cfg or {}).get('fuzz') or {}
    if not isinstance(fz, dict):
        raise TierError('"fuzz" in forge.json must be an object')
    t = (fz.get('tiers') or {}).get(name) or {}
    for k, v in t.items():
        if k not in out:
            raise TierError('"fuzz.tiers.%s.%s" is unknown; known: %s' % (name, k, ', '.join(out)))
        if k == 'steer' and not isinstance(v, bool) or k != 'steer' and (not isinstance(v, int) or isinstance(v, bool) or v <= 0):
            raise TierError('"fuzz.tiers.%s.%s" must be %s' % (name, k, 'true or false' if k == 'steer' else 'a positive integer'))
        out[k] = v
    out['name'] = name
    return out


# ---------------------------------------------------------------------------- the dictionary

def _py_name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Subscript):
        s = node.slice
        s = getattr(s, 'value', s) if not isinstance(s, ast.Constant) else s  # Python 3.8 wraps it in ast.Index
        if isinstance(s, ast.Constant) and isinstance(s.value, str):
            return s.value
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'get' and node.args:
        a = node.args[0]
        if isinstance(a, ast.Constant) and isinstance(a.value, str):
            return a.value
    return None


def _const(node):
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub) and isinstance(node.operand, ast.Constant) \
            and type(node.operand.value) in (int, float):
        return -node.operand.value
    if isinstance(node, ast.Constant) and type(node.value) in (int, float, str):
        return node.value
    return None


def harvest(files):
    """(constants by the field name they are compared with, all constants) from the watched files."""
    by_name, pool = {}, []

    def add(name, c):
        if isinstance(c, str) and len(c) > 64:
            return
        if name:
            by_name.setdefault(name, set()).add(c)
        if c not in pool and len(pool) < MAX_POOL:
            pool.append(c)
    for path in files:
        try:
            text = open(path, encoding='utf-8').read()
        except (OSError, UnicodeDecodeError):
            continue
        if path.endswith(forge_coverage.PY_EXT):
            try:
                tree = ast.parse(text)
            except SyntaxError:
                continue
            for n in ast.walk(tree):
                if isinstance(n, ast.Compare):
                    sides = [n.left] + list(n.comparators)
                    for a, b in zip(sides, sides[1:]):
                        for x, y in ((a, b), (b, a)):
                            c = _const(y)
                            if c is not None:
                                add(_py_name(x), c)
                elif isinstance(n, (ast.Constant, ast.UnaryOp)) and _const(n) is not None and not isinstance(_const(n), str):
                    add(None, _const(n))
        else:
            code = forge_coverage._js_code_mask(text)
            ident = r'([A-Za-z_$][\w$]*(?:\??\.[A-Za-z_$][\w$]*)*)'
            lit = r'(-?\d+(?:\.\d+)?|\'[^\'\n]{0,64}\'|"[^"\n]{0,64}")'
            op = r'\s*(?:===?|!==?|<=?|>=?)\s*'
            for m in re.finditer(ident + op + lit, code):
                add(re.split(r'\??\.', m.group(1))[-1], _js_lit(m.group(2)))
            for m in re.finditer(lit + op + ident, code):
                add(re.split(r'\??\.', m.group(2))[-1], _js_lit(m.group(1)))
            for m in re.finditer(r'(?<![\w$.])(-?\d+)(?![\w$.])', code):
                add(None, int(m.group(1)))
    return {k: sorted(v, key=lambda x: (isinstance(x, str), str(x))) for k, v in by_name.items()}, pool


def _js_lit(t):
    if t[0] in '\'"':
        return t[1:-1]
    return float(t) if '.' in t else int(t)


# ---------------------------------------------------------------------------- the steered run

def _leaves(v):
    out = []
    for p in forge_diff.messy_fields(v):
        x = forge_diff._get(v, p)
        if isinstance(x, (bool, int, float, str)):
            out.append(p)
    return out


def _set(v, path, x):
    parent = forge_diff._get(v, path[:-1])
    parent[path[-1]] = x


class Steer:
    """One steered (or, for measurement, blind but instrumented) diff entry run."""

    def __init__(self, lang, root, watch, mode='full'):
        if mode not in MODES:
            raise ValueError(mode)
        self.lang, self.mode = lang, mode
        ext = forge_coverage.PY_EXT if lang == 'py' else forge_coverage.JS_EXT
        watch = [w for w in (watch or []) if w.endswith(ext)]
        self.cov = forge_coverage.Session(lang, root, watch)
        self.files = [os.path.realpath(f) for f in self.cov.files]
        self.by_name, self.pool = harvest(self.files)
        self.corpus, self.window, self.seq = [], {}, 0
        self.counts = {'fresh': 0, 'mutated': 0, 'dictionary_values': 0, 'novel': 0, 'repeats_avoided': 0}
        self.made = set()  # inputs already made (canonical text), so a change rarely repeats an earlier input
        self.gen = None

    def wrap(self, cmd, env):
        env = dict(env)
        env.pop('NODE_V8_COVERAGE', None)  # a take resets V8's counters; the two cannot run together
        env['FORGE_STEER_FILES'] = json.dumps(self.files)
        if self.lang == 'py':
            self.cov.tool = 'stdlib tracer, per input'
            self.cov.data = env['FORGE_STEER_OUT'] = os.path.join(self.cov.tmp, 'trace.json')
        else:
            self.cov.tool = 'V8 block coverage, per input'
            self.cov.data = env['FORGE_STEER_OUT'] = os.path.join(self.cov.tmp, 'v8')
        return cmd, env

    def bind(self, gen):
        self.gen = gen

    # -- inputs
    def next(self, rng):
        for _ in range(2):
            inp = self._next(rng)
            key = forge_diff.canon(inp)
            if key not in self.made or self.mode == 'blind':
                break
            self.counts['repeats_avoided'] += 1
        if len(self.made) > 100000:
            self.made.clear()
        self.made.add(key)
        return inp

    def _next(self, rng):
        if self.mode == 'blind':
            self.counts['fresh'] += 1
            return self.gen.generate(rng)
        if self.mode == 'dict':  # no corpus: a fresh input with dictionary values put in
            self.counts['mutated'] += 1
            return self._mutate(self.gen.generate(rng), rng)
        if not self.corpus or rng.float() < FRESH:
            self.counts['fresh'] += 1
            return self.gen.generate(rng)
        base = rng.pick(self.corpus[-8:]) if rng.float() < 0.5 else rng.pick(self.corpus)
        self.counts['mutated'] += 1
        return self._mutate(copy.deepcopy(base), rng)

    def _mutate(self, inp, rng):
        ls = _leaves(inp)
        if not ls:
            return inp
        for _ in range(rng.pick([1, 1, 1, 2, 3])):
            p = rng.pick(ls)
            _set(inp, p, self._value(forge_diff._get(inp, p), str(p[-1]), inp, ls, rng))
        return inp

    def _value(self, v, name, inp, ls, rng):
        if isinstance(v, bool):
            return not v
        num = isinstance(v, (int, float))
        if self.mode in ('full', 'dict') and (self.mode == 'dict' or rng.float() < DICT_SHARE):
            named = [c for c in self.by_name.get(name, []) if isinstance(c, str) != num]
            pool = named if named and rng.float() < 0.8 else [c for c in self.pool if isinstance(c, str) != num]
            if pool:
                self.counts['dictionary_values'] += 1
                c = rng.pick(pool)
                return c + rng.pick([0, 0, 0, 1, -1]) if num and isinstance(c, int) and isinstance(v, int) else c
            if self.mode == 'dict':
                return v
        others = [forge_diff._get(inp, q) for q in ls]
        if num:
            same = [o for o in others if isinstance(o, type(v)) and not isinstance(o, bool)]
            ops = [lambda: v + 1, lambda: v - 1, lambda: v + rng.int(-16, 16), lambda: v * 2, lambda: v // 2 if isinstance(v, int) else v / 2,
                   lambda: -v, lambda: 0, lambda: rng.pick(same) + rng.pick([0, 1, -1]) if same else v + 1]
            return rng.pick(ops)()
        if isinstance(v, str):
            strs = [o for o in others if isinstance(o, str)]
            ops = [lambda: '', lambda: v + rng.string(4), lambda: v[:len(v) // 2], lambda: rng.pick(strs),
                   lambda: v[::-1], lambda: v.upper()]
            return rng.pick(ops)()
        return v

    # -- feedback
    def sent(self, inp, out):
        """Called for every plain input the evaluator ran, in order."""
        self.window[self.seq] = inp
        self.window.pop(self.seq - WINDOW, None)
        self.seq += 1
        for n in out.pop('novel', None) or []:
            x = self.window.get(n)
            if x is not None:
                self.counts['novel'] += 1
                if self.mode in ('corpus', 'full'):
                    self.corpus.append(copy.deepcopy(x))

    def stats(self):
        return dict(self.counts, mode=self.mode, corpus=len(self.corpus),
                    dictionary={k: v[:8] for k, v in sorted(self.by_name.items())[:20]})

    def collect(self):
        return self.cov.collect()


def make(df, root, enabled, mode=None):
    """A Steer for this diff entry when steering is on and its language has per-input coverage, else None.
    FORGE_STEER_MODE (blind, corpus, dict, full) is for measurements."""
    mode = mode or os.environ.get('FORGE_STEER_MODE') or 'full'
    if not enabled or df.get('lang') not in STEERED_LANGS or not df.get('watch'):
        return None
    return Steer(df['lang'], root, df.get('watch'), mode)


def report_lines(r):
    """Lines under a diff result: what the steering did, and every watched line no input reached, plainly."""
    st = r.get('steer')
    out = []
    if st:
        d = ', '.join('%s: %s' % (k, '/'.join(str(x) for x in v)) for k, v in list(st['dictionary'].items())[:6])
        out.append('  steered (%s): %d inputs took a new path and %d were kept as seeds; %d fresh, %d changed from '
                   'seeds%s' % (st['mode'], st['novel'], st['corpus'], st['fresh'], st['mutated'],
                                '; constants from the code: ' + d if d else ''))
    elif r.get('steer_note'):
        out.append('  ' + r['steer_note'])
    cov = r.get('coverage') or {}
    for rel, f in (cov.get('files') or {}).items() if st else ():
        lines = f.get('unreached_lines') or []
        for ln in lines[:12]:
            out.append('  %s:%d was never reached' % (rel, ln))
        if len(lines) > 12:
            out.append('  ... and %d more lines of %s were never reached' % (len(lines) - 12, rel))
    return out
