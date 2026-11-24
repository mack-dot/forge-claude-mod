"""Decision reads for the bypass gate (`forge_check.py bypass`): which text hits of a watched field are places where
the code READS the field to DECIDE something.

TypeScript / JavaScript: parsed with the TypeScript compiler API (a pinned, SHA-256-checked typescript package,
downloaded once into the cache, like Dafny). Python: the standard `ast` module. In these files a text hit counts
only when it lies in a decision: an if / while condition, a comparison, `in`, `.includes` (and similar), a ternary,
a switch / case (match / case), the left side of && / || (a guard), a filter / find / some / every predicate, a
comprehension `if`, an assert, or the right side of an alias (`const v = row.f`) whose name is read in a decision.
Type declarations, annotations, imports, comments, string literals, plain assignment, object construction and
logging are not decisions.

Every other file (Go, Rust, Java, C#, ...) is searched as text, as before, and is labelled "text search, not AST".
A TS / JS / Python file that does not parse, or that cannot be parsed here (no Node, no TypeScript package), is also
searched as text, with the reason: the gate fails closed and never skips a file silently.

LIMIT: a syntactic test, not data flow. It misses decisions in another function that gets the value as an argument
or a return value, dynamic access through a computed key, and code that decides the same question under other names.
"""
import ast
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tokenize
import urllib.request

TS_EXT = ('.ts', '.tsx', '.mts', '.cts', '.js', '.jsx', '.mjs', '.cjs')
PY_EXT = ('.py', '.pyi')
TS_VERSION = '5.6.3'
TS_URL = 'https://registry.npmjs.org/typescript/-/typescript-%s.tgz' % TS_VERSION
TS_TGZ_SHA256 = 'ef67f8d8ad895858024b7339d3e34bf112cae3c5db1f538c3079038b17ae30fa'
TS_JS_SHA256 = 'f316520790d4db220a10d890c5f85310e26a1bd3c104b8d3b5eb62ba0491651b'
WALKER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'eval', 'ts_decisions.cjs')
NODE_TIMEOUT_S = 300


def language(rel):
    low = rel.lower()
    if low.endswith(TS_EXT):
        return 'ts'  # a .d.ts file too: parsed like any TS file (declarations give 0 decisions)
    if low.endswith(PY_EXT):
        return 'py'
    return 'text'


def _cache_dir():
    base = os.environ.get('XDG_CACHE_HOME') or os.path.join(os.path.expanduser('~'), '.cache')
    return os.path.join(base, 'forge-check')


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def typescript_js(log=sys.stderr, download=True):
    """Path of the pinned typescript.js, checked against its SHA-256. (path, None) or (None, reason)."""
    final = os.path.join(_cache_dir(), 'typescript-' + TS_VERSION)
    path = os.path.join(final, 'lib', 'typescript.js')
    if os.path.isfile(path):
        if _sha256_file(path) == TS_JS_SHA256:
            return path, None
        return None, 'the cached %s has the wrong SHA-256 (delete %s to download it again)' % (path, final)
    if not download or os.environ.get('FORGE_NO_DOWNLOAD'):
        return None, 'TypeScript %s is not in the cache and downloads are off' % TS_VERSION
    try:
        os.makedirs(_cache_dir(), exist_ok=True)
        print('forge: downloading TypeScript %s (the parser for the bypass search, about 4 MB, once)…' % TS_VERSION,
              file=log)
        with urllib.request.urlopen(TS_URL, timeout=60) as resp:
            data = resp.read()
        got = hashlib.sha256(data).hexdigest()
        if got != TS_TGZ_SHA256:
            return None, 'the TypeScript download has SHA-256 %s, not the pinned %s' % (got, TS_TGZ_SHA256)
        staging = tempfile.mkdtemp(prefix='typescript-unpack-', dir=_cache_dir())
        try:
            with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as tar:
                for name in ('package/lib/typescript.js', 'package/package.json', 'package/LICENSE.txt'):
                    member = tar.getmember(name)
                    target = os.path.join(staging, name[len('package/'):])
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    with tar.extractfile(member) as src, open(target, 'wb') as dst:
                        shutil.copyfileobj(src, dst)
            if _sha256_file(os.path.join(staging, 'lib', 'typescript.js')) != TS_JS_SHA256:
                return None, 'typescript.js in the checked download has an unexpected SHA-256'
            if os.path.isdir(final):
                shutil.rmtree(final)
            os.replace(staging, final)
        finally:
            if os.path.isdir(staging):
                shutil.rmtree(staging)
    except Exception as e:  # offline, proxy, disk: the TS / JS files are searched as text instead
        return None, 'TypeScript %s could not be downloaded (%s: %s)' % (TS_VERSION, type(e).__name__, e)
    return path, None


# ------------------------------------------------------------------------------------------------- Python
_EXIT = (ast.Return, ast.Raise, ast.Continue, ast.Break)
_PY_INCLUDES = {'startswith', 'endswith', '__contains__', 'issubset', 'issuperset', 'isdisjoint', 'match',
                'fullmatch', 'search'}
_PY_KEY_READERS = {'get', 'pop', 'setdefault', 'getattr', 'hasattr', '__getitem__', 'has_key'}
_PY_PREDICATE = {'filter': 'filter-predicate', 'any': 'predicate', 'all': 'predicate', 'next': 'find-predicate'}
_LITERALS = (ast.Constant, ast.List, ast.Dict, ast.Tuple, ast.Set)


_SQL_WHERE = re.compile(r'\b(select|update|delete)\b[\s\S]*\bwhere\b', re.I)


def _sql_where(n):
    return isinstance(n, ast.Constant) and isinstance(n.value, str) and bool(_SQL_WHERE.search(n.value))


def _call_name(call):
    f = call.func
    return f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else None


def _py_analyze(text):
    """(spans, excluded) as code-point offsets into text, or raises SyntaxError / ValueError."""
    tree = ast.parse(text)
    lines = text.split('\n')
    starts = [0]
    for ln in lines:
        starts.append(starts[-1] + len(ln) + 1)

    def off(lineno, col_bytes):
        line = lines[lineno - 1]
        col = col_bytes if line.isascii() else len(line.encode('utf-8')[:col_bytes].decode('utf-8', 'replace'))
        return starts[lineno - 1] + col

    def rng(n):
        return off(n.lineno, n.col_offset), off(n.end_lineno, n.end_col_offset)

    spans, excluded, names = [], [], {}

    def span(n, kind):
        if n is not None and getattr(n, 'lineno', None) is not None:
            s, e = rng(n)
            spans.append((s, e, kind))

    def mark(n, kind):
        span(n, kind)
        for sub in ast.walk(n):
            if isinstance(sub, ast.Name):
                names.setdefault(sub.id, []).append(rng(sub)[0])

    for node in ast.walk(tree):
        if isinstance(node, (ast.If, ast.While)):
            first = node.body[0] if node.body else None
            mark(node.test, 'loop-condition' if isinstance(node, ast.While)
                 else 'early-return-guard' if isinstance(first, _EXIT) else 'if-condition')
        elif isinstance(node, ast.IfExp):
            mark(node.test, 'ternary')
        elif isinstance(node, ast.Assert):
            mark(node.test, 'assert')
        elif isinstance(node, ast.Compare):
            mark(node, 'in' if any(isinstance(o, (ast.In, ast.NotIn)) for o in node.ops) else 'comparison')
        elif isinstance(node, ast.BoolOp):
            vals = node.values
            if isinstance(node.op, ast.Or) and len(vals) == 2 and isinstance(vals[-1], _LITERALS):
                continue  # `x or ''`: a default value, not a guard
            for v in vals[:-1]:
                mark(v, 'logical-guard')
        elif isinstance(node, ast.comprehension):
            for cond in node.ifs:
                mark(cond, 'filter-predicate')
        elif sys.version_info >= (3, 10) and isinstance(node, ast.Match):
            mark(node.subject, 'switch')
            for case in node.cases:
                mark(case.pattern, 'case')
                if case.guard is not None:
                    mark(case.guard, 'if-condition')
        elif isinstance(node, ast.Call) and any(_sql_where(a) for a in node.args):
            # db.execute("... WHERE tenant_id = %s", (row.tenant_id,)): the database decides with the parameters
            for a in list(node.args) + [k.value for k in node.keywords]:
                if not _sql_where(a):
                    mark(a, 'sql-where')
        elif isinstance(node, ast.Call):
            name = _call_name(node)
            if name in _PY_INCLUDES and isinstance(node.func, ast.Attribute):
                mark(node, 'includes')
            elif name in _PY_PREDICATE and node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Lambda):
                    mark(arg.body, _PY_PREDICATE[name])
                elif isinstance(arg, ast.GeneratorExp) and name in ('any', 'all'):
                    mark(arg.elt, 'predicate')
    # string literals are text, except where they name the key being read: row['f'], 'f' in row, row.get('f')
    keys = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript):
            keys.add(id(node.slice))
        elif isinstance(node, ast.Compare) and any(isinstance(o, (ast.In, ast.NotIn)) for o in node.ops):
            keys.add(id(node.left))
        elif isinstance(node, ast.Call) and _call_name(node) in _PY_KEY_READERS:
            keys.update(id(a) for a in node.args)
    # Row(f=value) builds a value: the keyword name f is not a read
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg and getattr(node, 'lineno', None) is not None:
            s0 = rng(node)[0]
            excluded.append((s0, s0 + len(node.arg)))
    # Before Python 3.12 the parts of an f-string carry the f-string's own position: exclude none of them there
    # (an over-count, never a miss). From 3.12 the text parts are excluded and the {expressions} stay.
    inner = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.JoinedStr):
            inner.update(id(v) for v in ast.walk(n) if v is not n)
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes))) or id(node) in keys:
            continue
        if id(node) in inner and sys.version_info < (3, 12):
            continue
        excluded.append(rng(node))
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.COMMENT:
                excluded.append((off(tok.start[0], len(lines[tok.start[0] - 1][:tok.start[1]].encode('utf-8'))),
                                 off(tok.end[0], len(lines[tok.end[0] - 1][:tok.end[1]].encode('utf-8')))))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        pass
    # aliases: `v = row.f` where v is read in a decision in the same function (or a function inside it). Only a
    # plain read (an attribute or a subscript, maybe `or` a default) is an alias; a call or a built value is not.
    scopes = [rng(n) for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))]

    def scope_of(pos):
        best = (0, len(text))
        for s, e in scopes:
            if s <= pos < e and e - s < best[1] - best[0]:
                best = (s, e)
        return best

    def plain_read(v):
        while isinstance(v, (ast.Await, ast.BoolOp)):
            v = v.value if isinstance(v, ast.Await) else v.values[0]
        if not isinstance(v, (ast.Attribute, ast.Subscript)):
            return False
        while isinstance(v, (ast.Attribute, ast.Subscript)):  # row.body.f, self.x, row['f']; not f().g
            v = v.value
        return isinstance(v, ast.Name)

    for node in ast.walk(tree):
        targets, value = [], None
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, (ast.AnnAssign, ast.NamedExpr)) and node.value is not None:
            targets, value = [node.target], node.value
        if value is None or not plain_read(value):
            continue
        s, e = scope_of(rng(node)[0])
        if any(s <= p < e for t in targets for n in ast.walk(t) if isinstance(n, ast.Name)
               for p in names.get(n.id, ())):
            span(value, 'alias')
    return spans, excluded


# ------------------------------------------------------------------------------------------------- analyzer
def _u16_map(text):
    """UTF-16 offset -> code-point offset, or None when the text is ASCII (the two are equal)."""
    if text.isascii():
        return None
    m = []
    for i, ch in enumerate(text):
        m.append(i)
        if ord(ch) > 0xFFFF:
            m.append(i)
    m.append(len(text))
    return m


class Decisions:
    """Per-file decision spans. prime() parses many TS / JS files with one Node process; classify() answers per hit."""

    def __init__(self, log=sys.stderr):
        self.log = log
        self.info = {}  # rel -> {'method': 'ast'|'text', 'reason': str|None, 'spans': [...], 'excluded': [...]}
        self.ts_version = None
        self.ts_problem = None

    def prime(self, texts):
        """texts: {rel: full text ('\\n' line ends)}. Parses each file once."""
        ts_files = {}
        for rel, text in texts.items():
            if rel in self.info:
                continue
            lang = language(rel)
            if lang == 'py':
                try:
                    spans, excl = _py_analyze(text)
                    self.info[rel] = {'method': 'ast', 'reason': None, 'spans': spans, 'excluded': excl}
                except (SyntaxError, ValueError, RecursionError) as e:
                    line = getattr(e, 'lineno', None)
                    self.info[rel] = self._text('Python parse error%s: %s' % (
                        ' at line %s' % line if line else '', getattr(e, 'msg', None) or e))
            elif lang == 'ts':
                ts_files[rel] = text
            else:
                self.info[rel] = self._text(None)
        if ts_files:
            self._prime_ts(ts_files)

    @staticmethod
    def _text(reason):
        return {'method': 'text', 'reason': reason, 'spans': None, 'excluded': None}

    def _prime_ts(self, files):
        problem = None
        node = shutil.which('node')
        if not node:
            problem = 'node is not on PATH, so TS / JS files cannot be parsed'
        tsjs = None
        if not problem:
            tsjs, problem = typescript_js(self.log)
        if not problem:
            try:
                r = subprocess.run([node, WALKER, tsjs], input=json.dumps(
                    {'files': [{'path': k, 'text': v} for k, v in files.items()]}).encode('utf-8'),
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=NODE_TIMEOUT_S)
                if r.returncode != 0:
                    problem = 'the TS parser exited %d: %s' % (r.returncode, r.stderr.decode('utf-8', 'replace')[-300:])
                else:
                    out = json.loads(r.stdout.decode('utf-8'))
                    self.ts_version = out.get('typescript')
            except (OSError, ValueError, subprocess.TimeoutExpired) as e:
                problem = 'the TS parser did not run (%s: %s)' % (type(e).__name__, e)
        if problem:
            self.ts_problem = problem
            for rel in files:
                self.info[rel] = self._text(problem)
            return
        for rel, text in files.items():
            f = out['files'].get(rel) or {'ok': False, 'error': 'no answer from the TS parser'}
            if not f.get('ok'):
                self.info[rel] = self._text('TS/JS parse error: %s' % f.get('error'))
                continue
            m = _u16_map(text)
            conv = (lambda o: o) if m is None else (lambda o: m[min(o, len(m) - 1)])
            self.info[rel] = {'method': 'ast', 'reason': None,
                              'spans': [(conv(s), conv(e), k) for s, e, k in f['spans']],
                              'excluded': [(conv(s), conv(e)) for s, e in f['excluded']]}

    def classify(self, rel, line_start, line_text, regexes):
        """Is this text hit a decision read? -> (True|False, kind or None, method, reason)."""
        info = self.info.get(rel) or self._text('not parsed')
        if info['method'] != 'ast':
            return True, None, 'text', info['reason']
        end = line_start + len(line_text)
        spans = [s for s in info['spans'] if s[0] < end and s[1] > line_start]
        if not spans:
            return False, None, 'ast', None
        excl = [x for x in info['excluded'] if x[0] < end and x[1] > line_start]
        best = None
        for rx in regexes:
            for m in rx.finditer(line_text):
                for p in range(m.start(), max(m.end(), m.start() + 1)):
                    a = line_start + p
                    if line_text[p:p + 1].isspace() or any(x[0] <= a < x[1] for x in excl):
                        continue
                    for s, e, k in spans:
                        if s <= a < e and (best is None or e - s <= best[1] - best[0]):  # innermost; on a tie the later (more specific)
                            best = (s, e, k)
        if best is None:
            return False, None, 'ast', None
        return True, best[2], 'ast', None

    def summary(self, rels):
        """Counts per method over the given files, and the files searched as text for a reason (fail closed)."""
        out = {'ast': 0, 'text': 0, 'fallback': []}
        for rel in rels:
            i = self.info.get(rel)
            if not i:
                continue
            out[i['method']] += 1
            if i['method'] == 'text' and i['reason']:
                out['fallback'].append({'file': rel, 'reason': i['reason']})
        return out
