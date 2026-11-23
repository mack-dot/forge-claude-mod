"""`forge_check.py diff --coverage`: which lines and branches of the code under test the differential fuzz reached.

Measured per watched file of a diff entry, during the same diff run (the inputs, decisions and saved cases that
the run sends). A branch the fuzz never took is code the fuzz never compared with the proved rule.

  Python  coverage.py (`python -m coverage run --branch`) when the Python that runs forge can import it; else a
          standard-library tracer (cli/eval/py_cover.py) with branches read from the source: each `if`, `while`
          and `for` has two ways out (taken / not taken). Branches inside one line (`a if c else b`, `and`, `or`)
          are not counted by either.
  JS/TS   Node's built-in V8 coverage (NODE_V8_COVERAGE, no dependency). V8 lists a block only when it ran a
          different number of times than the code around it, so there is no branch TOTAL: the report names every
          block that never ran ("unreached blocks") and every function never called.
  Other   not measured (Go, Rust, Java, C#, cmd): the report says so.

Nothing in the project is written; the data goes to a temporary folder. Test tooling: NOT proved.
"""
import ast
import glob
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import unquote, urlparse

PY_EXT = ('.py',)
JS_EXT = ('.js', '.mjs', '.cjs', '.ts', '.mts', '.cts')
MAX_NAMED = 40  # unreached branches named per file


def has_coverage_py():
    if os.environ.get('FORGE_NO_COVERAGE_PY') == '1':  # tests: force the standard-library tracer
        return False
    return importlib.util.find_spec('coverage') is not None


class Session:
    """Wraps one evaluator command so it records coverage, then reads the result after the run."""

    def __init__(self, lang, root, watch):
        self.lang, self.root = lang, root
        self.tmp = tempfile.mkdtemp(prefix='forge-cov-')
        self.files = [os.path.join(root, w) for w in watch or []]
        self.tool = None

    def wrap(self, cmd, env):
        env = dict(env)
        if self.lang == 'py':
            files = [f for f in self.files if f.endswith(PY_EXT)]
            if has_coverage_py():
                self.tool = 'coverage.py'
                self.data = os.path.join(self.tmp, '.coverage')
                cmd = [cmd[0], '-m', 'coverage', 'run', '--branch', '--data-file=' + self.data,
                       '--include=' + ','.join(os.path.realpath(f) for f in files)] + cmd[1:]
            else:
                self.tool = 'stdlib tracer'
                self.data = os.path.join(self.tmp, 'trace.json')
                wl = os.path.join(self.tmp, 'watched.json')
                with open(wl, 'w', encoding='utf-8') as f:
                    json.dump(files, f)
                here = os.path.dirname(os.path.abspath(__file__))
                cmd = [cmd[0], os.path.join(here, 'eval', 'py_cover.py'), self.data, wl] + cmd[1:]
        elif self.lang == 'js':
            self.tool = 'V8 (NODE_V8_COVERAGE)'
            self.data = os.path.join(self.tmp, 'v8')
            env['NODE_V8_COVERAGE'] = self.data
        return cmd, env

    def collect(self):
        try:
            if self.lang == 'py':
                files = {os.path.realpath(f): os.path.relpath(f, self.root) for f in self.files if f.endswith(PY_EXT)}
                res = self._py_coverage(files) if self.tool == 'coverage.py' else self._py_stdlib(files)
            elif self.lang == 'js':
                files = {os.path.realpath(f): os.path.relpath(f, self.root) for f in self.files if f.endswith(JS_EXT)}
                res = self._js(files)
            else:
                return {'measured': False, 'detail': 'coverage is measured for py and js entries only (not %s)' % self.lang}
            skipped = [os.path.relpath(f, self.root) for f in self.files if os.path.realpath(f) not in files]
            out = {'measured': True, 'tool': self.tool, 'files': res}
            if skipped:
                out['not_measured'] = skipped
            return out
        except Exception as e:  # noqa: BLE001  coverage must never change the diff verdict
            return {'measured': False, 'detail': 'coverage could not be read: %s' % e}
        finally:
            shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- Python
    def _py_coverage(self, files):
        js = os.path.join(self.tmp, 'cov.json')
        r = subprocess.run([sys.executable, '-m', 'coverage', 'json', '--data-file=' + self.data, '-o', js],
                           cwd=self.root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
        if r.returncode != 0 and 'No data' not in r.stdout:
            raise RuntimeError(r.stdout[-300:])
        data = json.load(open(js, encoding='utf-8')).get('files', {}) if os.path.exists(js) else {}
        by_real = {os.path.realpath(os.path.join(self.root, k) if not os.path.isabs(k) else k): v for k, v in data.items()}
        out = {}
        for real, rel in sorted(files.items(), key=lambda x: x[1]):
            src = _lines(real)
            d = by_real.get(real)
            if d is None:  # never imported
                stmts = _py_statements(real)
                out[rel] = _file_result(0, len(stmts), None, None, [], 'never loaded during the run')
                continue
            s = d.get('summary', {})
            missing = [_branch_name(rel, src, a, b) for a, b in d.get('missing_branches', [])]
            out[rel] = _file_result(len(d.get('executed_lines', [])), s.get('num_statements', 0),
                                    s.get('covered_branches'), s.get('num_branches'), missing)
            out[rel]['unreached_lines'] = d.get('missing_lines', [])
        return out

    def _py_stdlib(self, files):
        data = json.load(open(self.data, encoding='utf-8')) if os.path.exists(self.data) else {}
        out = {}
        for real, rel in sorted(files.items(), key=lambda x: x[1]):
            src = _lines(real)
            stmts = _py_statements(real)
            got = data.get(real)
            if got is None:
                out[rel] = _file_result(0, len(stmts), 0, 2 * len(_py_branch_points(real)), [], 'never loaded during the run')
                continue
            lines, arcs = set(got['lines']), {tuple(a) for a in got['arcs']}
            points = _py_branch_points(real)
            taken, missing = 0, []
            for kind, span, body, other in points:
                def went(pred):
                    return any(a in span and pred(b) for a, b in arcs)
                t = went(lambda b: b == body)
                f = went(lambda b: b not in span and b != body if other is None else b == other)
                taken += t + f
                if not t:
                    missing.append(_point_name(rel, src, min(span), kind, True))
                if not f:
                    missing.append(_point_name(rel, src, min(span), kind, False))
            reached = len([l for l in stmts if l in lines])
            out[rel] = _file_result(reached, len(stmts), taken, 2 * len(points), missing)
            out[rel]['unreached_lines'] = sorted(l for l in stmts if l not in lines)
        return out

    # ---- JavaScript / TypeScript
    def _js(self, files):
        scripts = {}
        for path in glob.glob(os.path.join(self.data, '*.json')):
            for s in json.load(open(path, encoding='utf-8')).get('result', []):
                u = urlparse(s.get('url', ''))
                if u.scheme == 'file':
                    real = os.path.realpath(unquote(u.path))
                    if real in files:
                        scripts.setdefault(real, []).extend(s.get('functions', []))
        out = {}
        for real, rel in sorted(files.items(), key=lambda x: x[1]):
            text = open(real, encoding='utf-8').read()
            code = _js_code_mask(_strip_types(real, text))
            starts = _utf16_line_starts(text)
            code_lines = [i + 1 for i, l in enumerate(code.split('\n')) if l.strip(' \t\r{}();,')]
            fns = scripts.get(real)
            if fns is None:
                out[rel] = _file_result(0, len(code_lines), None, None, [], 'never loaded during the run')
                continue
            ranges = [(r['startOffset'], r['endOffset'], r['count']) for f in fns for r in f['ranges']]
            u16 = _utf16_index(text)

            def count_at(off):
                best = None
                for a, b, c in ranges:
                    if a <= off < b and (best is None or b - a < best[1] - best[0]):
                        best = (a, b, c)
                return best[2] if best else 0
            reached = 0
            unreached_lines = []
            src = text.split('\n')
            for ln in code_lines:
                line = code.split('\n')[ln - 1]
                col = len(line) - len(line.lstrip())
                if count_at(u16(starts[ln - 1], col)) > 0:
                    reached += 1
                else:
                    unreached_lines.append(ln)
            blocks, never, missing = 0, 0, []
            for f in fns:
                for i, r in enumerate(f['ranges']):
                    if i == 0 and f.get('functionName') == '' and r['startOffset'] == 0:
                        continue  # the module itself
                    blocks += 1
                    if r['count'] == 0:
                        ln = _first_code_line(code, starts, text, r['startOffset'], r['endOffset'])
                        if ln is None:
                            continue
                        never += 1
                        what = ('function %s never called' % (f.get('functionName') or '(anonymous)')) if i == 0 \
                            else 'block never run'
                        missing.append('%s:%d  %s  `%s`' % (rel, ln, what, src[ln - 1].strip()[:100]))
            res = _file_result(reached, len(code_lines), None, None, missing)
            res['blocks_reported'], res['blocks_never_run'] = blocks, never
            res['unreached_lines'] = unreached_lines
            out[rel] = res
        return out


def _file_result(lines_hit, lines_total, br_hit, br_total, missing, note=None):
    r = {'lines': [lines_hit, lines_total], 'branches': None if br_total is None else [br_hit, br_total],
         'unreached': missing[:MAX_NAMED], 'unreached_count': len(missing)}
    if note:
        r['note'] = note
    return r


def _lines(path):
    with open(path, encoding='utf-8') as f:
        return f.read().split('\n')


def _branch_name(rel, src, a, b):
    text = src[a - 1].strip()[:100] if 0 < a <= len(src) else ''
    to = 'leaves the function' if b < 0 else 'line %d' % b
    return '%s:%d  never went to %s  `%s`' % (rel, a, to, text)


def _point_name(rel, src, line, kind, taken):
    text = src[line - 1].strip()[:100] if 0 < line <= len(src) else ''
    what = {'if': ('condition never True', 'condition never False'),
            'while': ('loop body never entered', 'loop never ended by its condition'),
            'for': ('loop body never entered', 'loop never ran to its end')}[kind][0 if taken else 1]
    return '%s:%d  %s  `%s`' % (rel, line, what, text)


def _docstring_lines(tree):
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(getattr(first, 'value', None), ast.Constant) \
                    and isinstance(first.value.value, str):
                out.add(first.lineno)
    return out


def _py_statements(path):
    tree = ast.parse(open(path, encoding='utf-8').read())
    skip = _docstring_lines(tree)
    return sorted({n.lineno for n in ast.walk(tree) if isinstance(n, ast.stmt) and n.lineno not in skip})


def _py_branch_points(path):
    """(kind, lines of the test, first body line, the other way out or None for 'anything else')."""
    tree = ast.parse(open(path, encoding='utf-8').read())
    pts = []
    for n in ast.walk(tree):
        if isinstance(n, (ast.If, ast.While)):
            head = n.test
            kind = 'if' if isinstance(n, ast.If) else 'while'
        elif isinstance(n, (ast.For, ast.AsyncFor)):
            head = n.iter
            kind = 'for'
        else:
            continue
        span = set(range(n.lineno, (getattr(head, 'end_lineno', None) or head.lineno) + 1))
        body = n.body[0].lineno
        if body in span:  # `if c: x` on one line: the two ways out cannot be told apart by line
            continue
        other = n.orelse[0].lineno if isinstance(n, ast.If) and n.orelse else None
        pts.append((kind, span, body, other))
    return sorted(pts, key=lambda p: min(p[1]))


def _strip_types(path, text):
    """TypeScript with its types replaced by spaces (same offsets), so type-only lines do not count as code."""
    if not path.endswith(('.ts', '.mts', '.cts')) or not shutil.which('node'):
        return text
    r = subprocess.run(['node', '--no-warnings', '-e',
                        "const m=require('node:module');process.stdout.write(m.stripTypeScriptTypes("
                        "require('node:fs').readFileSync(0,'utf8')))"],
                       input=text, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                       encoding='utf-8')
    return r.stdout if r.returncode == 0 and len(r.stdout) == len(text) else text


def _js_code_mask(text):
    """Comments replaced by spaces (newlines kept)."""
    return re.sub(r'//[^\n]*|/\*.*?\*/', lambda m: re.sub(r'[^\n]', ' ', m.group(0)), text, flags=re.S)


def _utf16_line_starts(text):
    starts, pos = [], 0
    for line in text.split('\n'):
        starts.append(pos)
        pos += len(line) + 1
    return starts


def _utf16_index(text):
    """Maps a code-point offset to V8's UTF-16 offset."""
    if all(ord(c) < 0x10000 for c in text):
        return lambda line_start, col: line_start + col
    pre = [0]
    for c in text:
        pre.append(pre[-1] + (2 if ord(c) >= 0x10000 else 1))
    return lambda line_start, col: pre[line_start + col]


def _first_code_line(code, starts, text, a16, b16):
    """The first line with code inside the UTF-16 range [a16, b16)."""
    if any(ord(c) >= 0x10000 for c in text):
        pre, i = 0, 0
        cp = []
        for c in text:
            cp.append(pre)
            pre += 2 if ord(c) >= 0x10000 else 1
        a = next((i for i, v in enumerate(cp) if v >= a16), len(text))
        b = next((i for i, v in enumerate(cp) if v >= b16), len(text))
    else:
        a, b = a16, b16
    for i in range(a, min(b, len(code))):
        if not code[i].isspace() and code[i] not in '{}();,':
            return code.count('\n', 0, i) + 1
    return None


def report_lines(cov):
    """Lines under a diff result."""
    if not cov:
        return []
    if not cov.get('measured'):
        return ['  coverage: not measured: %s' % cov.get('detail', '')]
    out = ['  coverage (%s) of the code under test during this run:' % cov['tool']]
    for rel, r in cov['files'].items():
        br = r.get('branches')
        part = 'lines %d/%d' % tuple(r['lines'])
        if br is not None:
            part += ', branches %d/%d' % tuple(br)
        elif 'blocks_never_run' in r:
            part += ', %d block(s) never run (V8 lists no branch total)' % r['blocks_never_run']
        out.append('    %s: %s%s' % (rel, part, ' (%s)' % r['note'] if r.get('note') else ''))
        for u in r['unreached']:
            out.append('      UNREACHED ' + u)
        if r['unreached_count'] > len(r['unreached']):
            out.append('      ... and %d more' % (r['unreached_count'] - len(r['unreached'])))
    if cov.get('not_measured'):
        out.append('    not measured (other language): ' + ', '.join(cov['not_measured']))
    if any(r['unreached_count'] for r in cov['files'].values()):
        out.append('  The fuzz never compared these paths with the proved rule: widen the inputs module, or add a '
                   'decision that reaches them.')
    return out
