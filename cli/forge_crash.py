"""`/forge crash-fuzz`: a zero-spec CRASH-CHECK of a project's boundary functions with messy JSON.

No spec, no forge.json, no proof. It finds the functions where outside data enters the code (HTTP handlers, webhook
handlers, parsers, exported entry points), calls each one with messy inputs (the messy kinds of the differential
fuzz: undefined, null, NaN, wrong types, missing fields, empty text, very long text, lone surrogates, ...) and
reports every call where an error escaped the function, with the smallest input that still gives the same error at
the same place. The output is labelled CRASH-CHECK: a finite test that only shows crashes; it never proves anything.

Which functions (default):
  JavaScript / TypeScript (.js .mjs .cjs .ts .mts .cts)
    exported functions (ESM `export`, CommonJS `module.exports` / `exports.x`). Route handlers registered by name
    (`app.post('/x', handler)`) but not exported are listed as NOT tested: they cannot be called without changing
    the module.
  Python (.py)
    functions with a route / view decorator (`@app.route`, `@router.post`, `@api_view`, `@csrf_exempt`, ...),
    names in `__all__`, and public functions named like a boundary (handler, webhook, parse_*, *_view,
    lambda_handler, endpoint) or whose first parameter is `request` / `event`. FastAPI handlers with typed
    parameters (the framework validates before the call) and Flask views with no parameters (they read a global
    request) are listed as NOT tested.
  Private internal helpers are skipped and listed with the reason: JavaScript functions that are not exported (no
  caller outside the module can pass them data, so a crash there is reached only through a boundary function,
  which this check calls); Python names starting with `_`. Public functions that do not look like a boundary are
  skipped too. --target FILE:FUNCTION (or .forge/crash-fuzz.json {"targets": [...]}) names functions to check,
  and --include-private adds private Python helpers.

How each is called (from the parameter names): (req, res[, next]) Express/Fastify style with the input as req.body;
(request) fetch style (Next.js route handlers, Workers) with the input as JSON body text; (event, context) AWS
Lambda style; Python (request) as a Django/Flask-like request; anything else one argument per parameter. The input
shape is inferred from the fields the function reads (`body.item.quantity`, `payload["user"]`, destructuring).

What counts: an error that escapes the function (a throw, a rejected promise, an unhandled rejection) is a CRASH.
Runtime errors (TypeError, ReferenceError, RangeError, SyntaxError, AttributeError, KeyError, IndexError, ...) are
counted as crashes; an error the code throws on purpose (the line is a `throw` / `raise` statement) is listed
separately, since a framework may turn it into a 4xx answer. A call that does not answer within 1 s is a HANG.

Labels: CRASH-CHECK, never PROVED. Test tooling: NOT proved.
"""
import argparse
import ast
import json
import os
import random
import re
import select
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import forge_diff  # noqa: E402

JS_EXT = ('.js', '.mjs', '.cjs', '.ts', '.mts', '.cts')
PY_EXT = ('.py',)
SKIP_DIRS = {'node_modules', '.git', '.forge', 'dist', 'build', 'out', 'coverage', 'venv', '.venv', 'env',
             '__pycache__', 'site-packages', 'vendor', 'third_party', 'third-party', '.next', 'target', 'verified',
             'test', 'tests', '__tests__', 'spec', 'specs', 'fixtures', 'migrations', 'bower_components', '.tox'}
TEST_FILE = re.compile(r'(\.(test|spec)\.[cm]?[jt]s$|^test_.*\.py$|_test\.py$|^conftest\.py$|\.d\.[cm]?ts$|\.min\.js$)')
MAX_FILES = 3000
RUNTIME_ERRORS = {'TypeError', 'ReferenceError', 'RangeError', 'SyntaxError', 'URIError', 'InternalError',
                  'AttributeError', 'KeyError', 'IndexError', 'ValueError', 'UnicodeEncodeError', 'UnicodeDecodeError',
                  'UnicodeError', 'ZeroDivisionError', 'RecursionError', 'JSONDecodeError', 'OverflowError',
                  'NameError', 'UnboundLocalError', 'AssertionError'}
WHOLE_KINDS = ('undefined', 'null', 'empty-text', 'not-json-text', 'number', 'boolean', 'array', 'object',
               'long-text', 'lone-surrogate', 'nan')
FIELD_KINDS = forge_diff.MESSY_KINDS + ('nan',)
NAN = {'__nan__': True}
UNDEF = forge_diff.UNDEFINED
CALL_TIMEOUT_S = 3.0
HTTP_VERBS = {'GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'}
PY_ROUTE_DECOS = re.compile(r'(^|\.)(route|get|post|put|patch|delete|api_route|websocket|view|api_view|csrf_exempt|'
                            r'require_POST|require_GET|require_http_methods|require_safe|handler|webhook|'
                            r'lambda_handler|endpoint|action)$')
JS_BOUNDARY_NAME = re.compile(r'^(handle|handler|handle[A-Z_].*|.*Handler|.*[Ww]ebhook.*|parse|parse[A-Z_].*|decode[A-Z].*|'
                              r'on[A-Z]\w*(Request|Event|Message|Webhook)|.*(Route|Endpoint|Controller|Action)|'
                              r'ingest.*|receive[A-Z].*|lambdaHandler|main|middleware)$')
FACTORY = re.compile(r'^(create|make|build|new|init|setup)[A-Z_]?\w*(Handler|App|Server|Router|Middleware|Client|Service|Api|'
                     r'Worker|Listener|Context|Container)$', re.I)
JS_BOUNDARY_DIR = re.compile(r'(^|/)(routes?|api|handlers?|webhooks?|controllers?|endpoints?|functions|lambdas?)/|'
                             r'(^|/)app/(.*/)?route\.[cm]?[jt]s$|(^|/)pages/api/')
PY_BOUNDARY_NAME = re.compile(r'^(handle|handler|.*_handler|handle_.*|.*webhook.*|parse|parse_.*|.*_view|view_.*|'
                              r'lambda_handler|endpoint|endpoint_.*|api_.*|on_.*_event|process_event|ingest.*|'
                              r'receive.*|decode_.*|load_.*_payload)$')


class CrashError(Exception):
    pass


# ---------------------------------------------------------------------------- finding the boundary functions

def project_files(root):
    out = []
    for d, dirs, files in os.walk(root):
        dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS and not x.startswith('.'))
        for f in sorted(files):
            if f.endswith(JS_EXT + PY_EXT) and not TEST_FILE.search(f):
                out.append(os.path.relpath(os.path.join(d, f), root))
                if len(out) >= MAX_FILES:
                    return out
    return out


def _mask_js(text):
    """Comments replaced by spaces; strings kept (field names in ['x'] are read from them)."""
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c in '"\'`':
            j = i + 1
            while j < n and text[j] != c:
                j += 2 if text[j] == '\\' else 1
            out.append(text[i:j + 1])
            i = j + 1
        elif text.startswith('//', i):
            j = text.find('\n', i)
            j = n if j < 0 else j
            out.append(' ' * (j - i))
            i = j
        elif text.startswith('/*', i):
            j = text.find('*/', i + 2)
            j = n if j < 0 else j + 2
            out.append(re.sub(r'[^\n]', ' ', text[i:j]))
            i = j
        else:
            out.append(c)
            i += 1
    return ''.join(out)


def _split_params(s):
    parts, depth, cur = [], 0, ''
    for ch in s:
        if ch in '([{<':
            depth += 1
        elif ch in ')]}>':
            depth -= 1
        if ch == ',' and depth == 0:
            parts.append(cur)
            cur = ''
        else:
            cur += ch
    if cur.strip():
        parts.append(cur)
    out = []
    for p in parts:
        p = p.strip()
        if p.startswith('{'):  # destructured: ({ a, b }: T) -> one object parameter with fields a, b
            inner = p[1:p.index('}')] if '}' in p else p[1:]
            fields = [re.split(r'[:=\s]', x.strip())[0] for x in inner.split(',') if x.strip()]
            out.append({'name': '__obj%d' % len(out), 'fields': [f for f in fields if f and not f.startswith('...')]})
            continue
        name = re.split(r'[?:=\s]', p.lstrip('.'))[0]
        out.append({'name': name, 'fields': []})
    return out


def _block(code, start):
    """The text from `start` to the end of the next balanced {...} block (or the end of an arrow expression line)."""
    i = code.find('{', start)
    nl = code.find('\n', start)
    arrow = code.find('=>', start)
    if arrow >= 0 and (i < 0 or arrow < i) and nl >= 0 and code[arrow + 2:nl].strip() and not code[arrow + 2:nl].strip().startswith('{'):
        return code[start:nl]
    if i < 0:
        return code[start:start + 400]
    depth = 0
    for j in range(i, min(len(code), i + 200000)):
        if code[j] == '{':
            depth += 1
        elif code[j] == '}':
            depth -= 1
            if depth == 0:
                return code[start:j + 1]
    return code[start:]


JS_FN_DECL = re.compile(r'(?m)^(?P<pre>[ \t]*(?:export\s+(?:default\s+)?)?)(?:async\s+)?function\s*\*?\s*(?P<name>[A-Za-z_$][\w$]*)?\s*(?:<[^>]*>)?\s*\((?P<params>[^)]*)\)')
JS_ARROW = re.compile(r'(?m)^(?P<pre>[ \t]*(?:export\s+)?)(?:const|let|var)\s+(?P<name>[A-Za-z_$][\w$]*)\s*(?::[^=]+)?=\s*(?:async\s+)?(?:function\s*\w*\s*\((?P<p1>[^)]*)\)|\((?P<p2>[^)]*)\)\s*(?::\s*[^=]+)?=>|(?P<p3>[A-Za-z_$][\w$]*)\s*=>)')


def js_functions(rel, text):
    code = _mask_js(text)
    exported = set()
    for m in re.finditer(r'export\s*\{([^}]*)\}', code):
        for part in m.group(1).split(','):
            bits = part.strip().split(' as ')
            if bits[0].strip():
                exported.add((bits[0].strip(), (bits[-1] if len(bits) > 1 else bits[0]).strip()))
    cjs = set()
    for m in re.finditer(r'module\.exports\s*=\s*\{([^}]*)\}', code):
        for part in m.group(1).split(','):
            k = part.split(':')[0].strip()
            v = part.split(':')[-1].strip()
            if k:
                cjs.add((v, k))
    for m in re.finditer(r'(?:module\.)?exports\.([A-Za-z_$][\w$]*)\s*=\s*([A-Za-z_$][\w$]*)?', code):
        cjs.add((m.group(2) or m.group(1), m.group(1)))
    routed = {m.group(1) for m in re.finditer(
        r'\b(?:app|router|server|api|fastify|route|r)\s*\.\s*(?:get|post|put|patch|delete|all|use|route)\s*\(\s*[\'"`][^\'"`]*[\'"`]\s*,\s*(?:[\w$.]+\s*,\s*)*([A-Za-z_$][\w$]*)\s*\)', code)}
    out = []
    for rx in (JS_FN_DECL, JS_ARROW):
        for m in rx.finditer(code):
            name = m.group('name')
            if not name:
                if 'default' in m.group('pre'):
                    name = 'default'
                else:
                    continue
            if m.group('pre').strip() == '' and len(m.group('pre')) > 0:
                continue  # indented: a nested function, never callable from outside
            params = m.group('params') if 'params' in rx.groupindex else (m.group('p1') or m.group('p2') or m.group('p3') or '')
            is_export = 'export' in m.group('pre')
            export_name = name if is_export else None
            for local, public in exported | cjs:
                if local == name:
                    export_name = public
            line = code.count('\n', 0, m.start()) + 1
            out.append({'file': rel, 'name': name, 'export': export_name, 'params': _split_params(params),
                        'body': _block(code, m.end()), 'line': line, 'routed': name in routed, 'lang': 'js'})
    return out


def js_style(fn):
    names = [p['name'].lower() for p in fn['params']]
    if len(names) >= 2 and names[0] in ('req', 'request', 'ctx') and names[1] in ('res', 'response', 'reply', 'rep'):
        return 'express'
    if fn['name'] in HTTP_VERBS or (len(names) in (1, 2) and names[0] in ('request', 'req') and
                                    re.search(r'\b%s\s*\.\s*(json|text|formData)\s*\(' % re.escape(fn['params'][0]['name']), fn['body'])):
        return 'fetch'
    if names[:1] == ['event'] or names == ['event', 'context']:
        return 'lambda'
    return 'plain'


def py_functions(rel, text):
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    allnames = set()
    for n in tree.body:
        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == '__all__' for t in n.targets) \
                and isinstance(n.value, (ast.List, ast.Tuple)):
            allnames |= {e.value for e in n.value.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)}
    out = []
    for n in tree.body:
        if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        decos = [ast.unparse(d.func if isinstance(d, ast.Call) else d) if hasattr(ast, 'unparse') else '' for d in n.decorator_list]
        params = [a.arg for a in n.args.posonlyargs + n.args.args + n.args.kwonlyargs]
        typed = [a for a in n.args.args if a.annotation is not None and not (
            isinstance(a.annotation, ast.Name) and a.annotation.id in ('str', 'int', 'float', 'bool', 'dict', 'list', 'Any', 'object'))]
        out.append({'file': rel, 'name': n.name, 'params': [{'name': p, 'fields': []} for p in params], 'node': n,
                    'decos': decos, 'in_all': n.name in allnames, 'line': n.lineno, 'lang': 'py',
                    'typed': bool(typed), 'async': isinstance(n, ast.AsyncFunctionDef)})
    return out


def py_style(fn):
    names = [p['name'] for p in fn['params']]
    if names[:1] in (['request'], ['req']):
        return 'request'
    if names[:1] == ['event']:
        return 'lambda'
    return 'plain'


def package_entries(root):
    """Files a package.json names as entry points ("main", "module", "exports", "bin"), relative to root."""
    out = set()
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if x not in SKIP_DIRS and not x.startswith('.')]
        if 'package.json' not in files:
            continue
        try:
            pj = json.load(open(os.path.join(d, 'package.json'), encoding='utf-8'))
        except (OSError, ValueError):
            continue

        def walk(v):
            if isinstance(v, str):
                yield v
            elif isinstance(v, dict):
                for x in v.values():
                    yield from walk(x)
            elif isinstance(v, list):
                for x in v:
                    yield from walk(x)
        for key in ('main', 'module', 'exports', 'bin'):
            for rel in walk(pj.get(key)):
                if rel.startswith(('./', '.')) or '/' in rel or rel.endswith(JS_EXT):
                    out.add(os.path.normpath(os.path.relpath(os.path.join(d, rel), root)))
    return out


def classify(root, files, include_private=False, targets=None):
    """(boundary functions to call, skipped [(where, reason)])."""
    found, skipped = [], []
    wanted = {tuple(t.rsplit(':', 1)) for t in targets or []}
    entries = package_entries(root)
    for rel in files:
        try:
            text = open(os.path.join(root, rel), encoding='utf-8').read()
        except (OSError, UnicodeDecodeError):
            continue
        if rel.endswith(JS_EXT):
            for fn in js_functions(rel, text):
                key = (rel, fn['name'])
                here = '%s:%d %s' % (rel, fn['line'], fn['name'])
                if wanted and key not in wanted and (rel, fn['export'] or '') not in wanted:
                    continue
                if not fn['export']:
                    if fn['routed']:
                        skipped.append((here, 'route handler, NOT tested: not exported, so it cannot be called without '
                                              'changing the module (export it, or test the route)'))
                    else:
                        skipped.append((here, 'private helper: not exported, so no caller outside its module can pass it '
                                              'data; a crash there shows up through the boundary function that calls it'))
                    continue
                fn['style'] = js_style(fn)
                if not wanted and FACTORY.match(fn['name']):
                    skipped.append((here, 'NOT tested: a factory (create/make/build...): it takes code dependencies, '
                                          'not request data; check the function it returns with --target'))
                    continue
                entry = rel in entries or os.path.splitext(rel)[0] in {os.path.splitext(e)[0] for e in entries}
                if not wanted and not (fn['style'] != 'plain' or fn['routed'] or JS_BOUNDARY_NAME.match(fn['name'])
                                       or JS_BOUNDARY_NAME.match(fn['export']) or entry or JS_BOUNDARY_DIR.search(rel)):
                    skipped.append((here, 'exported, but not detected as a boundary (no request/response or event '
                                          'parameters, no handler/webhook/parse-like name, not a package entry point, '
                                          'not under routes/ or api/): an internal module export; name it with '
                                          '--target %s:%s to check it' % (rel, fn['name'])))
                    continue
                found.append(fn)
        else:
            for fn in py_functions(rel, text):
                key = (rel, fn['name'])
                here = '%s:%d %s' % (rel, fn['line'], fn['name'])
                if wanted:
                    if key not in wanted:
                        continue
                elif fn['name'].startswith('_') and not include_private:
                    skipped.append((here, 'private helper (name starts with _): only code in its module calls it; a crash '
                                          'there shows up through the boundary function that calls it'))
                    continue
                routed = any(PY_ROUTE_DECOS.search(d) for d in fn['decos'])
                names = [p['name'] for p in fn['params']]
                boundary = routed or fn['in_all'] or PY_BOUNDARY_NAME.match(fn['name']) or names[:1] in (
                    ['request'], ['req'], ['event']) or include_private and fn['name'].startswith('_')
                if not boundary and not wanted:
                    skipped.append((here, 'not detected as a boundary (no route decorator, not in __all__, no handler-like '
                                          'name); name it with --target %s:%s to check it' % (rel, fn['name'])))
                    continue
                if not wanted and FACTORY.match(fn['name']):
                    skipped.append((here, 'NOT tested: a factory (create_/make_/build_...): it takes code dependencies, '
                                          'not request data; check the function it returns with --target'))
                    continue
                if routed and not fn['params']:
                    skipped.append((here, 'NOT tested: a view with no parameters reads a framework-global request; '
                                          'crash-fuzz calls functions directly'))
                    continue
                if routed and fn['typed'] and any(re.search(r'\.(get|post|put|patch|delete|api_route)$', d) for d in fn['decos']):
                    skipped.append((here, 'NOT tested: FastAPI-style typed parameters are validated by the framework '
                                          'before the call; a direct call would report crashes that cannot happen'))
                    continue
                fn['style'] = py_style(fn)
                found.append(fn)
    return found, skipped


# ---------------------------------------------------------------------------- the input shape

def _guess(name):
    n = name.lower()
    if n in ('raw', 'body', 'payload', 'data', 'input', 'event', 'json', 'msg', 'message', 'obj', 'value', 'params',
             'args', 'options', 'opts', 'config', 'record', 'item', 'entry', 'doc', 'document', 'update', 'patch'):
        return {}
    if re.search(r'(amount|count|qty|quantity|price|total|age|num|number|limit|size|cents|balance|offset|page|port|'
                 r'score|level|index|year|duration|timeout|retries|max|min)', n) and not n.endswith('s'):
        return 5
    if re.match(r'(is|has|can|should|enabled|active|ok|flag|allow|verified|paid)', n):
        return True
    if 'email' in n:
        return 'a@example.com'
    if re.search(r'(_at|date|time)$', n):
        return '2026-01-01T00:00:00Z'
    if 'url' in n or 'uri' in n or 'href' in n:
        return 'https://example.com/'
    if re.search(r'(items|list|tags|ids|lines|entries|rows|values)$', n):
        return ['x']
    return 'x'


def _tree_put(tree, path):
    cur = tree
    for k in path[:-1]:
        nxt = cur.get(k)
        if not isinstance(nxt, dict):
            nxt = cur[k] = {}
        cur = nxt
    if path[-1] not in cur:
        cur[path[-1]] = {}
    return cur


def _materialize(tree, name='x'):
    if not tree:
        return _guess(name)
    return {k: _materialize(v, k) for k, v in tree.items()}


ACCESS = r'(?:\??\.\s*([A-Za-z_$][\w$]*)|\[\s*[\'"]([^\'"]+)[\'"]\s*\])'


def _js_paths(body, var):
    """Field paths read from `var` in a JS body: var.a.b, var['a'], const { a, b } = var, aliases (const x = var.a)."""
    paths = []
    seen_vars = {var: ()}
    for _ in range(3):
        for v, base in list(seen_vars.items()):
            for m in re.finditer(r'(?<![\w$.])%s((?:%s)+)' % (re.escape(v), ACCESS), body):
                chain = [a or b for a, b in re.findall(ACCESS, m.group(1))]
                chain = [c for c in chain if c not in ('length', 'trim', 'toString', 'map', 'filter', 'forEach', 'json',
                                                       'text', 'then', 'toLowerCase', 'toUpperCase', 'split', 'includes')]
                if chain:
                    paths.append(base + tuple(chain[:3]))
            for m in re.finditer(r'(?:const|let|var)\s*\{([^}]*)\}\s*=\s*(?:await\s+)?%s((?:%s)*)\s*[;\n]' % (re.escape(v), ACCESS), body):
                pre = tuple(a or b for a, b in re.findall(ACCESS, m.group(2)))
                for part in m.group(1).split(','):
                    k = re.split(r'[:=\s]', part.strip())[0]
                    if k and not k.startswith('...'):
                        paths.append(base + pre + (k,))
                        alias = part.split(':')[1].split('=')[0].strip() if ':' in part else k
                        seen_vars.setdefault(alias, base + pre + (k,))
            for m in re.finditer(r'(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:await\s+)?%s((?:%s)+)\s*[;\n]' % (re.escape(v), ACCESS), body):
                seen_vars.setdefault(m.group(1), base + tuple(a or b for a, b in re.findall(ACCESS, m.group(2))))
    return paths


def js_base(fn):
    body, params = fn['body'], fn['params']
    style = fn['style']

    def shape_of(var, prefix=()):
        tree = {}
        for p in _js_paths(body, var):
            if p[:len(prefix)] == prefix and len(p) > len(prefix):
                _tree_put(tree, p[len(prefix):])
        return tree
    if style == 'express':
        tree = shape_of(params[0]['name'], ('body',))
        for m in re.finditer(r'(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*%s\s*\.\s*body\b' % re.escape(params[0]['name']), body):
            for p in _js_paths(body, m.group(1)):
                _tree_put(tree, p)
        return _materialize(tree, 'body') if tree else {}
    if style == 'fetch':
        tree = {}
        for m in re.finditer(r'(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*await\s+%s\s*\.\s*json\s*\(' % re.escape(params[0]['name']), body):
            for p in _js_paths(body, m.group(1)):
                _tree_put(tree, p)
        for m in re.finditer(r'(?:const|let|var)\s*\{([^}]*)\}\s*=\s*await\s+%s\s*\.\s*json\s*\(' % re.escape(params[0]['name']), body):
            for part in m.group(1).split(','):
                k = re.split(r'[:=\s]', part.strip())[0]
                if k:
                    _tree_put(tree, (k,))
        return _materialize(tree, 'body') if tree else {}
    if style == 'lambda':
        tree = shape_of(params[0]['name'])
        ev = _materialize(tree, 'event') if tree else {}
        return _json_text_fields(body, params[0]['name'], ev)
    out = {}
    for p in params:
        if p['fields']:
            out[p['name']] = {f: _guess(f) for f in p['fields']}
            continue
        tree = shape_of(p['name'])
        if re.search(r'JSON\.parse\s*\(\s*%s\s*\)' % re.escape(p['name']), body):
            inner = {}
            for m in re.finditer(r'(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*JSON\.parse\s*\(\s*%s\s*\)' % re.escape(p['name']), body):
                for q in _js_paths(body, m.group(1)):
                    _tree_put(inner, q)
            out[p['name']] = json.dumps(_materialize(inner, 'x') if inner else {})
        else:
            out[p['name']] = _materialize(tree, p['name']) if tree else _guess(p['name'])
    return out


def _json_text_fields(body, var, ev):
    """event.body that the code JSON.parse()s / json.loads() is JSON TEXT in the real event: make it so."""
    if isinstance(ev, dict) and isinstance(ev.get('body'), (dict, str)) and re.search(
            r'(JSON\.parse|json\.loads)\s*\(\s*%s\s*(\.\s*body|\[\s*[\'"]body[\'"]\s*\])' % re.escape(var), body):
        ev['body'] = json.dumps(ev['body'] if isinstance(ev['body'], dict) else {})
    return ev


def _py_paths(node, var):
    """Field paths read from `var` in a Python function: var['a']['b'], var.get('a'), aliases (x = var['a'],
    x = json.loads(var['body']), data = request.get_json() / request.json / json.loads(request.body))."""
    aliases = {var: ()}
    paths = []
    req_json = set()

    def resolve(e):
        if isinstance(e, ast.Name):
            return aliases.get(e.id)
        if isinstance(e, ast.Subscript):
            base = resolve(e.value)
            s = e.slice
            s = getattr(s, 'value', s) if not isinstance(s, ast.Constant) else s
            if base is not None and isinstance(s, ast.Constant) and isinstance(s.value, str):
                return base + (s.value,)
        if isinstance(e, ast.Call) and isinstance(e.func, ast.Attribute):
            if e.func.attr == 'get' and e.args and isinstance(e.args[0], ast.Constant) and isinstance(e.args[0].value, str):
                base = resolve(e.func.value)
                if base is not None:
                    return base + (e.args[0].value,)
            if e.func.attr == 'get_json' and isinstance(e.func.value, ast.Name) and e.func.value.id == var:
                return ()
        if isinstance(e, ast.Attribute) and isinstance(e.value, ast.Name) and e.value.id == var and e.attr in ('json', 'POST', 'data', 'form'):
            return ()
        if isinstance(e, ast.Call) and isinstance(e.func, ast.Attribute) and e.func.attr == 'loads' and e.args:
            inner = e.args[0]
            if isinstance(inner, ast.BoolOp):
                inner = inner.values[0]
            if isinstance(inner, ast.Attribute) and isinstance(inner.value, ast.Name) and inner.value.id == var and inner.attr in ('body', 'data'):
                return ()
            base = resolve(inner)
            if base is not None:
                req_json.add(base)
                return base
        if isinstance(e, ast.Await):
            return resolve(e.value)
        if isinstance(e, ast.BoolOp):  # request.body or b'null'
            return resolve(e.values[0])
        return None
    for _ in range(3):
        for n in ast.walk(node):
            if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
                r = resolve(n.value)
                if r is not None:
                    aliases.setdefault(n.targets[0].id, r)
    for n in ast.walk(node):
        if isinstance(n, (ast.Subscript, ast.Call)):
            r = resolve(n)
            if r:
                paths.append(r)
    return paths, req_json


def py_base(fn):
    node, params, style = fn['node'], fn['params'], fn['style']
    if style in ('request', 'lambda'):
        paths, as_json = _py_paths(node, params[0]['name'])
        tree = {}
        for p in paths:
            _tree_put(tree, p[:3])
        v = _materialize(tree, 'body') if tree else {}
        for jp in sorted(as_json, key=len, reverse=True):  # event['body'] that the code json.loads() is JSON text
            if jp and isinstance(v, dict):
                try:
                    parent = forge_diff._get(v, jp[:-1])
                    parent[jp[-1]] = json.dumps(parent.get(jp[-1]) if isinstance(parent.get(jp[-1]), dict) else {})
                except KeyError:
                    pass
        return v
    out = {}
    for p in params:
        if p['name'] in ('self', 'cls'):
            continue
        paths, as_json = _py_paths(node, p['name'])
        tree = {}
        for q in paths:
            _tree_put(tree, q[:3])
        if () in as_json:
            out[p['name']] = json.dumps(_materialize(tree, 'x') if tree else {})
        else:
            out[p['name']] = _materialize(tree, p['name']) if tree else _guess(p['name'])
    return out


# ---------------------------------------------------------------------------- messy variants

def whole_value(kind, lang):
    if kind == 'undefined':
        return UNDEF
    if kind == 'nan':
        return NAN
    return {'null': None, 'empty-text': '', 'not-json-text': 'not json {', 'number': 7, 'boolean': True, 'array': [],
            'object': {}, 'long-text': forge_diff.LONG_TEXT, 'lone-surrogate': '\ud800'}[kind]


def field_messy(base, path, kind):
    if kind == 'nan':
        try:
            orig = forge_diff._get(base, path)
        except KeyError:
            return None
        if isinstance(orig, dict) or isinstance(orig, list):
            return None
        out = json.loads(json.dumps(base))
        forge_diff._get(out, path[:-1])[path[-1]] = dict(NAN)
        return out
    return forge_diff.apply_messy(base, path, kind)


def variants(fn, base, rng, budget):
    """(input, description) pairs: every (field, kind) once, the whole input replaced, then random pairs."""
    lang = fn['lang']
    kinds = [k for k in FIELD_KINDS if not (lang == 'py' and k == 'undefined')]
    out = [(base, {'what': 'the inferred input itself', 'field': None, 'as': None})]
    plain = fn['style'] == 'plain' and isinstance(base, dict)
    whole = [] if plain else [(whole_value(k, lang), {'what': 'whole input = %s' % k, 'field': '(whole)', 'as': k})
                              for k in WHOLE_KINDS if not (lang == 'py' and k == 'undefined')]
    paths = forge_diff.messy_fields(base) if isinstance(base, (dict, list)) else []
    for p in paths:
        for k in kinds:
            if plain and lang == 'py' and len(p) == 1 and k == 'removed':
                continue  # a Python call always passes every parameter; a missing one is not a messy input
            m = field_messy(base, p, k)
            if m is not None:
                out.append((m, {'what': '%s = %s' % (forge_diff.field_name(p), k), 'field': forge_diff.field_name(p),
                                'as': k, 'path': list(p)}))
    out.extend(whole)  # after the fields: a crash found on a field names that field
    pairs = [(p, k) for p in paths for k in kinds if not (plain and lang == 'py' and len(p) == 1 and k == 'removed')]
    tries = 0
    while len(out) < budget and len(pairs) >= 2 and tries < budget * 4:
        tries += 1
        (p1, k1), (p2, k2) = rng.sample(pairs, 2)
        if p1[:len(p2)] == p2 or p2[:len(p1)] == p1:
            continue  # the same field (or one inside the other): not two messy fields
        m = field_messy(base, p1, k1)
        m = field_messy(m, p2, k2) if m is not None else None
        if m is not None:
            out.append((m, {'what': '%s = %s and %s = %s' % (forge_diff.field_name(p1), k1, forge_diff.field_name(p2), k2),
                            'field': forge_diff.field_name(p1), 'as': k1}))
    return out[:budget]


# ---------------------------------------------------------------------------- running

class Runner:
    def __init__(self, lang, root, targets):
        self.lang, self.root, self.targets = lang, root, targets
        self.tmp = tempfile.mkdtemp(prefix='forge-crash-')
        self.tfile = os.path.join(self.tmp, 'targets.json')
        with open(self.tfile, 'w', encoding='utf-8') as f:
            json.dump(targets, f)
        self.p = None
        self.start()

    def start(self):
        if self.lang == 'js':
            cmd = ['node', '--no-warnings', os.path.join(HERE, 'eval', 'crash_node.mjs'), self.root, self.tfile]
        else:
            cmd = [sys.executable, os.path.join(HERE, 'eval', 'crash_py.py'), self.root, self.tfile]
        env = dict(os.environ, NODE_ENV='test', FORGE_CRASH_CHECK='1')
        self.p = subprocess.Popen(cmd, cwd=self.root, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, universal_newlines=True, bufsize=1, encoding='utf-8',
                                  errors='backslashreplace')
        self.err = forge_diff.drain(self.p.stderr)  # a full stderr pipe would block the runner: a false hang
        try:
            hello = self._read(60.0)
        except Exception:
            self._stderr()
            raise
        if not isinstance(hello, dict) or 'ready' not in hello:
            raise CrashError('the %s runner did not start: %s' % (self.lang, self._stderr()))
        self.ready, self.missing = hello['ready'], hello.get('missing') or {}

    def _stderr(self):
        try:
            self.p.kill()
            self.p.wait(timeout=5)
        except Exception:  # noqa: BLE001
            pass
        return self.err(2.0)[-600:]

    def _read(self, timeout):
        ready, _, _ = select.select([self.p.stdout], [], [], timeout)
        if not ready:
            return None
        line = self.p.stdout.readline()
        if not line:
            raise CrashError('the %s runner stopped: %s' % (self.lang, self._stderr()))
        return json.loads(line)

    def call(self, tid, inp):
        try:
            self.p.stdin.write(json.dumps({'id': tid, 'input': inp}, ensure_ascii=True) + '\n')
            self.p.stdin.flush()
            out = self._read(CALL_TIMEOUT_S)
        except (CrashError, BrokenPipeError, ValueError) as e:
            out = {'died': str(e)[:300]}
        if out is None or 'died' in out:
            # A synchronous endless loop (or the process exited): restart, and report it for this input.
            self.close()
            self.start()
            return {'hang': True} if out is None else {'throws': {'name': 'ProcessExit', 'message': out['died'], 'at': None}}
        return out

    def close(self):
        try:
            self.p.stdin.close()
            self.p.kill()
            self.p.wait(timeout=5)
        except Exception:  # noqa: BLE001
            pass

    def cleanup(self):
        self.close()
        shutil.rmtree(self.tmp, ignore_errors=True)


def _line_text(root, at):
    if not at or ':' not in at:
        return ''
    f, ln = at.rsplit(':', 1)
    try:
        return open(os.path.join(root, f), encoding='utf-8').read().split('\n')[int(ln) - 1].strip()
    except (OSError, ValueError, IndexError, UnicodeDecodeError):
        return ''


MAX_PER_FUNCTION = 8  # crash entries kept per function


BUILTIN_ERRORS = RUNTIME_ERRORS | {'ProcessExit'}


def on_purpose(root, t, lang):
    """An error the code throws itself: its line has a throw / raise, or (JavaScript) it is not a built-in error."""
    if t.get('name') in ('TypeError', 'ReferenceError', 'AttributeError', 'NameError'):
        return False
    if t.get('raised') or re.search(r'\b(throw|raise)\b', _line_text(root, t.get('at'))):
        return True
    return lang == 'js' and t.get('name') not in BUILTIN_ERRORS


def _sig(out):
    """Same error at the same place: one crash (an unknown place, for example inside an awaited platform call,
    counts as one place)."""
    if out.get('hang'):
        return ('HANG', None)
    t = out.get('throws')
    if not t:
        return None
    return (t['name'], t.get('at') or '?')


def shrink(runner, tid, base, meta, sig, limit=200):
    """The smallest input that still gives the same error at the same place; the messy field and its kind stay."""
    if meta.get('field') in (None, '(whole)') or 'path' not in meta:
        return None
    path, kind = tuple(meta['path']), meta['as']
    best, steps, improved = base, 0, True
    while improved and steps < limit:
        improved = False
        for s in forge_diff.shrinks(best):
            steps += 1
            if steps > limit:
                break
            m = field_messy(s, path, kind)
            if m is not None and _sig(runner.call(tid, m)) == sig:
                best, improved = s, True
                break
    return field_messy(best, path, kind)


def run(root, inputs=120, seed=None, targets=None, include_private=False, langs=('js', 'py')):
    root = os.path.realpath(root)
    cfg_path = os.path.join(root, '.forge', 'crash-fuzz.json')
    if os.path.isfile(cfg_path):
        cfg = json.load(open(cfg_path, encoding='utf-8'))
        targets = list(targets or []) + list(cfg.get('targets') or [])
        include_private = include_private or bool(cfg.get('include_private'))
    seed = seed if seed is not None else int.from_bytes(os.urandom(4), 'little')
    started = time.time()
    files = [f for f in project_files(root) if (f.endswith(JS_EXT) and 'js' in langs) or (f.endswith(PY_EXT) and 'py' in langs)]
    found, skipped = classify(root, files, include_private, targets)
    result = {'label': 'CRASH-CHECK', 'seed': seed, 'inputs_per_function': inputs, 'functions': [], 'skipped':
              [{'where': w, 'reason': r} for w, r in skipped], 'not_run': []}
    if 'js' in langs and any(f['lang'] == 'js' for f in found) and not shutil.which('node'):
        result['not_run'].append('JavaScript/TypeScript: node is not on PATH')
        found = [f for f in found if f['lang'] != 'js']
    rng = random.Random(seed)
    for lang in ('js', 'py'):
        fns = [f for f in found if f['lang'] == lang]
        if not fns:
            continue
        specs = []
        for i, fn in enumerate(fns):
            fn['id'] = '%s#%d' % (fn['name'], i)
            specs.append({'id': fn['id'], 'file': fn['file'], 'name': fn['export'] if lang == 'js' else fn['name'],
                          'style': fn['style'], 'params': [p['name'] for p in fn['params']]})
        try:
            runner = Runner(lang, root, specs)
        except CrashError as e:
            result['not_run'].append(str(e))
            continue
        try:
            for fn in fns:
                row = {'file': fn['file'], 'line': fn['line'], 'name': fn['name'], 'lang': lang, 'style': fn['style'],
                       'calls': 0, 'crashes': [], 'thrown_on_purpose': [], 'hangs': []}
                result['functions'].append(row)
                if fn['id'] in runner.missing or fn['id'] not in runner.ready:
                    row['not_run'] = runner.missing.get(fn['id'], 'not loaded')
                    continue
                base = (js_base if lang == 'js' else py_base)(fn)
                row['inferred_input'] = base
                seen = {}
                for inp, meta in variants(fn, base, rng, inputs):
                    out = runner.call(fn['id'], inp)
                    row['calls'] += 1
                    sig = _sig(out)
                    if meta['as'] is None and sig is not None and not (out.get('throws') and on_purpose(root, out['throws'], lang)):
                        # The inferred input itself crashes: most likely Forge's guess of the input shape is wrong, so
                        # no messy result of this function is judged.
                        t = out.get('throws') or {}
                        row['not_judged'] = ('crashes on the inferred input itself (%s at %s: %s); Forge\'s guess of '
                                             'the input shape is probably wrong' % (
                                                 t.get('name', 'HANG'), t.get('at') or '?', (t.get('message') or '')[:120]))
                        row['inferred_input'] = base
                        break
                    if sig is None:
                        continue
                    if sig in seen:  # the same crash from another messy input: named, not shrunk again
                        also = seen[sig].setdefault('also', [])
                        if len(also) < 6 and meta['what'] not in also:
                            also.append(meta['what'])
                        continue
                    if len(seen) >= MAX_PER_FUNCTION:
                        continue
                    # A hang is not shrunk: every try would wait for the time limit.
                    small = None if out.get('hang') else shrink(runner, fn['id'], json.loads(json.dumps(base)), meta, sig)
                    small = small or inp
                    entry = seen[sig] = {'input': small, 'messy': meta['what'], 'kind': meta.get('as'), 'field': meta.get('field')}
                    if out.get('hang'):
                        row['hangs'].append(entry)
                        continue
                    t = out['throws']
                    entry.update({'error': t['name'], 'message': t.get('message', ''), 'at': t.get('at')})
                    if on_purpose(root, t, lang):
                        row['thrown_on_purpose'].append(entry)
                    else:
                        row['crashes'].append(entry)
        finally:
            runner.cleanup()
    result['ms'] = int((time.time() - started) * 1000)
    for r in result['functions']:
        if r.get('not_judged'):
            r['crashes'], r['hangs'], r['thrown_on_purpose'] = [], [], []
    result['crashing'] = sum(1 for r in result['functions'] if r['crashes'] or r['hangs'])
    result['not_judged'] = sum(1 for r in result['functions'] if r.get('not_judged'))
    return result


# ---------------------------------------------------------------------------- the report

def _kind_words(k):
    return {'undefined': 'a missing value (undefined)', 'removed': 'a missing field', 'null': 'null',
            'nan': 'NaN', 'number': 'a number where other data was expected', 'boolean': 'a boolean',
            'empty-text': 'empty text', 'long-text': 'very long text', 'lone-surrogate': 'a lone surrogate',
            'array': 'an array', 'object': 'an object', 'number-as-text': 'a number as text',
            'not-json-text': 'text that is not JSON', None: 'the inferred input itself'}.get(k, k)


def headline(result):
    rows = [r for r in result['functions'] if r['crashes'] or r['hangs']]
    if not rows:
        n = sum(1 for r in result['functions'] if not r.get('not_run') and not r.get('not_judged'))
        return 'CRASH-CHECK: 0 of %d boundary function%s crashed on messy input (a finite test, NOT PROVED)' % (
            n, '' if n == 1 else 's')
    errs = {}
    for r in rows:
        for c in r['crashes']:
            errs.setdefault(c['error'], set()).add(_kind_words(c['kind']))
        if r['hangs']:
            errs.setdefault('hangs', set()).add('messy input')
    parts = ['%s on %s' % (e, ', '.join(sorted(ks))) for e, ks in sorted(errs.items())]
    return 'CRASH-CHECK: %d handler%s crash with unhandled errors: %s' % (len(rows), '' if len(rows) == 1 else 's',
                                                                          '; '.join(parts))


def _show(v):
    """An input for the report: very long text is written as its length."""
    if isinstance(v, str) and len(v) > 60:
        return '%s...(%d characters)' % (v[:12], len(v))
    if isinstance(v, list):
        return [_show(x) for x in v]
    if isinstance(v, dict):
        return {k: _show(x) for k, x in v.items()}
    return v


def report(result):
    out = [headline(result)]
    total = sum(r['calls'] for r in result['functions'])
    out.append('  %d boundary function(s), %d calls, seed %s, %d ms. CRASH-CHECK only: it shows crashes; it proves '
               'nothing, and 0 crashes is not a proof of no crash.' % (len(result['functions']), total, result['seed'],
                                                                        result['ms']))
    for r in result['functions']:
        where = '%s:%d %s (%s)' % (r['file'], r['line'], r['name'], r['style'])
        if r.get('not_run'):
            out.append('  - %s: NOT run: %s' % (where, r['not_run']))
            continue
        if r.get('not_judged'):
            out.append('  ? %s: NOT judged: %s. Inferred input: %s. Check it with --target and a fixed shape.' % (
                where, r['not_judged'], forge_diff.dumps_any(_show(r.get('inferred_input')))[:200]))
            continue
        if not r['crashes'] and not r['hangs']:
            out.append('  ✓ %s: 0 crashes in %d calls%s' % (where, r['calls'], '; %d error(s) thrown on purpose' % len(
                r['thrown_on_purpose']) if r['thrown_on_purpose'] else ''))
        else:
            out.append('  ✗ %s: %d crash(es) in %d calls' % (where, len(r['crashes']) + len(r['hangs']), r['calls']))
        for c in r['crashes']:
            out.append('      CRASH %s at %s on %s: %s' % (c['error'], c.get('at') or '?', c['messy'], c['message'][:140]))
            if c.get('also'):
                out.append('        same crash also on: %s' % ', '.join(c['also']))
            out.append('        repro: %s' % forge_diff.dumps_any(_show(c['input']))[:300])
        for c in r['hangs']:
            out.append('      HANG (no answer in 1 s) on %s; repro: %s' % (c['messy'], forge_diff.dumps_any(_show(c['input']))[:300]))
        for c in r['thrown_on_purpose'][:3]:
            out.append('      thrown on purpose (%s at %s) on %s: check that the framework answers 4xx, not 500'
                       % (c['error'], c.get('at') or '?', c['messy']))
    if result['skipped']:
        out.append('  skipped %d function(s):' % len(result['skipped']))
        for s in result['skipped'][:30]:
            out.append('    %s: %s' % (s['where'], s['reason']))
        if len(result['skipped']) > 30:
            out.append('    ... and %d more' % (len(result['skipped']) - 30))
    for n in result['not_run']:
        out.append('  NOT run: ' + n)
    out.append('  Inputs are wrapped as {"__undefined__": true} (undefined) and {"__nan__": true} (NaN).')
    return '\n'.join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(prog='forge_crash.py', description='CRASH-CHECK: boundary functions with messy JSON')
    ap.add_argument('--root', default='.')
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--inputs', type=int, default=120, help='messy calls per function (default 120)')
    ap.add_argument('--seed', type=int)
    ap.add_argument('--target', action='append', help='FILE:FUNCTION to check (repeatable); only these are checked')
    ap.add_argument('--include-private', action='store_true', help='also check private Python helpers (_name)')
    ap.add_argument('--langs', default='js,py')
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(errors='backslashreplace')
    try:
        res = run(a.root, a.inputs, a.seed, a.target, a.include_private, tuple(a.langs.split(',')))
    except CrashError as e:
        print('forge crash-fuzz: %s' % e, file=sys.stderr)
        return 2
    for r in res['functions']:
        r.pop('inferred_input', None) if not a.json else None
    print(forge_diff.dumps_any(res, indent=2) if a.json else report(res))
    return 1 if res['crashing'] else 0


if __name__ == '__main__':
    sys.exit(main())
