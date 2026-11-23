#!/usr/bin/env python3
"""forge callgraph: who calls the governed decision, and which unproved code feeds it.

A check may name its governed decision in forge.json:

  "callers": {"decision": "src/policy.ts:canShip",        the function the app calls for this question
              "wrapper":  "src/policy-proved.ts:provedCanShip",  the function that calls the proved code (optional;
                                                                default: the decision itself is the wrapper)
              "replaced": ["src/old.ts:legacyCanShip"]}        the old logic the proved rule replaces (optional)
  "allowedCallers": [{"file": ..., "line_text_sha256": ..., "note": ...}]   places a person reviewed and allowed

"callers" may also be a list of such entries (one per language, for example). For JavaScript / TypeScript and
Python files this module builds a static call graph from imports (aliases, re-exports, `export *`, namespace
imports, `require`, Python `from … import … as …`) and call sites, and lists every UNVERIFIED caller:

  - a call (or a reference) that resolves to a "replaced" function: the old logic is still on a path;
  - a call by the decision's name (or a replaced function's name) that resolves to some other function, to a
    member of an object this analysis cannot resolve, or to nothing in the project: a re-implementation path;
  - the decision itself, when it does not reach the wrapper through the call graph.

It also ranks the unproved FEEDERS of the decision: the direct callers that compute an argument themselves (not a
plain pass-through of their own parameter) and the project functions whose return values flow, inside such a
caller, into an argument. Leverage = parameters fed, decisions fed, fan-in, and branching on watched fields.

LIMIT: a static analysis of the source text, not a proof and not a type-checker. It does not see dynamic
dispatch (`obj[name]()`), `eval`, a function passed through a data structure, tsconfig path aliases, package
imports, decorators, or callers written in other languages ("callers not checked" for those). Test files are
listed but not gated. Python 3.8+, standard library only.
"""
import ast
import hashlib
import os
import re

JS_EXT = ('.ts', '.tsx', '.mts', '.cts', '.js', '.jsx', '.mjs', '.cjs')
PY_EXT = ('.py',)
MAX_BYTES = 1 << 20
TEST_FILE = re.compile(r'(^|/)(tests?|__tests__|spec)/|[._-](test|spec)\.[A-Za-z]+$|(^|/)test_[^/]+\.py$|(^|/)conftest\.py$')

JS_KEYWORDS = {
    'if', 'for', 'while', 'switch', 'catch', 'return', 'function', 'typeof', 'instanceof', 'new', 'delete', 'void',
    'in', 'of', 'do', 'else', 'try', 'finally', 'throw', 'case', 'default', 'break', 'continue', 'var', 'let',
    'const', 'class', 'extends', 'super', 'import', 'export', 'from', 'as', 'await', 'yield', 'async', 'with',
    'this', 'null', 'undefined', 'true', 'false', 'interface', 'type', 'enum', 'implements', 'static', 'get', 'set',
    'public', 'private', 'protected', 'readonly', 'declare', 'abstract', 'override', 'keyof', 'is', 'satisfies',
    'namespace', 'module', 'debugger'}
METHOD_PREV = {None, '{', '}', ';', ',', 'async', 'static', 'get', 'set', 'public', 'private', 'protected',
               'readonly', 'override', 'abstract', '*', 'export', 'default', 'declare'}
STMT_START = {'export', 'const', 'let', 'var', 'function', 'class', 'import', 'if', 'for', 'while', 'return',
              'interface', 'type', 'enum', 'switch', 'try', 'throw', 'async', 'do'}
REGEX_PREV_KW = {'return', 'typeof', 'case', 'do', 'else', 'in', 'of', 'new', 'delete', 'void', 'throw', 'await',
                 'yield', 'instanceof'}
PUNCT = sorted(['>>>=', '...', '===', '!==', '**=', '<<=', '>>=', '>>>', '&&=', '||=', '??=', '=>', '==', '!=',
                '<=', '>=', '&&', '||', '??', '?.', '++', '--', '+=', '-=', '*=', '/=', '%=', '&=', '|=', '^=', '**',
                '<<', '>>'], key=len, reverse=True)
MUTATORS = {'push', 'unshift', 'add', 'set', 'splice', 'concat', 'append', 'extend', 'update', 'insert',
            'setdefault', 'assign'}


def line_sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def read_text(path):
    with open(path, encoding='utf-8', errors='replace') as f:
        return f.read()


class Tok(object):
    __slots__ = ('kind', 'v', 'line', 'nl')

    def __init__(self, kind, v, line, nl):
        self.kind, self.v, self.line, self.nl = kind, v, line, nl  # nl: a line break comes before this token

    def __repr__(self):
        return '%s:%r@%d' % (self.kind, self.v, self.line)


def js_tokens(text):
    """Tokens of JavaScript / TypeScript: identifiers, punctuation, strings (as 'str'), numbers. Comments,
    regular-expression literals and the text parts of template literals are dropped; the ${…} parts of a
    template are tokenized like code (they may call functions)."""
    toks = []
    i, n, line, nl = 0, len(text), 1, True
    brace_stack = []  # for template literals: 'tmpl' marks a ${ that returns into a template

    def prev():
        return toks[-1] if toks else None

    def regex_ok():
        p = prev()
        if p is None:
            return True
        if p.kind == 'id':
            return p.v in REGEX_PREV_KW
        if p.kind in ('str', 'num'):
            return False
        return p.v not in (')', ']', '}')

    def template(i, line):
        """Scan a template body from i (after ` or after the } that ends a ${…}). Returns (i, line, opened)."""
        while i < n:
            ch = text[i]
            if ch == '\\':
                i += 2
                continue
            if ch == '\n':
                line += 1
            if ch == '`':
                return i + 1, line, False
            if ch == '$' and i + 1 < n and text[i + 1] == '{':
                return i + 2, line, True
            i += 1
        return i, line, False

    while i < n:
        ch = text[i]
        if ch == '\n':
            line += 1
            nl = True
            i += 1
            continue
        if ch in ' \t\r\f\v﻿':
            i += 1
            continue
        if text.startswith('//', i) or (text.startswith('#!', i) and i == 0):
            j = text.find('\n', i)
            i = n if j < 0 else j
            continue
        if text.startswith('/*', i):
            j = text.find('*/', i + 2)
            j = n if j < 0 else j + 2
            line += text.count('\n', i, j)
            nl = nl or '\n' in text[i:j]
            i = j
            continue
        if ch in '"\'':
            j = i + 1
            while j < n and text[j] != ch and text[j] != '\n':
                j += 2 if text[j] == '\\' else 1
            toks.append(Tok('str', text[i + 1:j], line, nl))
            nl = False
            i = j + 1
            continue
        if ch == '`':
            start_line = line
            j, line, opened = template(i + 1, line)
            toks.append(Tok('str', '`', start_line, nl))
            nl = False
            if opened:
                brace_stack.append('tmpl')
            i = j
            continue
        if ch == '/' and regex_ok():
            j, in_class = i + 1, False
            while j < n and text[j] != '\n':
                c = text[j]
                if c == '\\':
                    j += 2
                    continue
                if c == '[':
                    in_class = True
                elif c == ']':
                    in_class = False
                elif c == '/' and not in_class:
                    break
                j += 1
            j += 1
            while j < n and (text[j].isalnum() or text[j] == '_'):
                j += 1
            toks.append(Tok('str', '/re/', line, nl))
            nl = False
            i = j
            continue
        if ch.isalpha() or ch in '_$' or ch == '#' or ord(ch) > 127:
            j = i + 1
            while j < n and (text[j].isalnum() or text[j] in '_$' or ord(text[j]) > 127):
                j += 1
            toks.append(Tok('id', text[i:j], line, nl))
            nl = False
            i = j
            continue
        if ch.isdigit() or (ch == '.' and i + 1 < n and text[i + 1].isdigit()):
            j = i + 1
            while j < n and (text[j].isalnum() or text[j] in '._'):
                j += 1
            toks.append(Tok('num', text[i:j], line, nl))
            nl = False
            i = j
            continue
        if ch == '{':
            brace_stack.append('{')
        elif ch == '}':
            if brace_stack and brace_stack[-1] == 'tmpl':
                brace_stack.pop()
                j, line, opened = template(i + 1, line)
                if opened:
                    brace_stack.append('tmpl')
                i = j
                continue
            if brace_stack:
                brace_stack.pop()
        for p in PUNCT:
            if text.startswith(p, i):
                toks.append(Tok('p', p, line, nl))
                i += len(p)
                break
        else:
            toks.append(Tok('p', ch, line, nl))
            i += 1
        nl = False
    return toks


class Def(object):
    __slots__ = ('name', 'qual', 'line', 'params', 'start', 'end', 'node', 'file', 'kind')

    def __init__(self, file, name, line, params, start, end, node=None, qual=None, kind='function'):
        self.file, self.name, self.line, self.params = file, name, line, params
        self.start, self.end, self.node, self.qual, self.kind = start, end, node, qual or name, kind


class Use(object):
    """A call (kind 'call') or a plain reference (kind 'ref') of a name, maybe as a member of `base`."""
    __slots__ = ('kind', 'name', 'base', 'line', 'enclosing', 'args', 'pos', 'kwargs')

    def __init__(self, kind, name, base, line, enclosing, args=None, pos=None, kwargs=None):
        self.kind, self.name, self.base, self.line, self.enclosing = kind, name, base, line, enclosing
        self.args, self.pos, self.kwargs = args, pos, kwargs or {}


class JsModule(object):
    lang = 'js'

    def __init__(self, rel, text):
        self.file, self.text = rel, text
        self.lines = text.replace('\r\n', '\n').split('\n')
        self.t = js_tokens(text)
        self.match, self.back = {}, {}
        stack = []
        for k, tok in enumerate(self.t):
            if tok.kind != 'p':
                continue
            if tok.v in '([{':
                stack.append(k)
            elif tok.v in ')]}':
                want = {')': '(', ']': '[', '}': '{'}[tok.v]
                while stack and self.t[stack[-1]].v != want:
                    stack.pop()
                if stack:
                    o = stack.pop()
                    self.match[o], self.back[k] = k, o
        self.defs, self.imports, self.exports, self.stars, self.aliases = [], {}, {}, [], {}
        self.default = None
        self.uses = []
        self._parse()

    # ---- token helpers
    def v(self, k):
        return self.t[k].v if 0 <= k < len(self.t) else None

    def is_id(self, k):
        return 0 <= k < len(self.t) and self.t[k].kind == 'id'

    def close(self, k):
        return self.match.get(k, k)

    def skip_angle(self, k):
        depth = 0
        while k < len(self.t):
            x = self.v(k)
            if x == '<':
                depth += 1
            elif x == '>':
                depth -= 1
            elif x == '>>':
                depth -= 2
            elif x in ('{', '(', '['):
                k = self.close(k)
            elif x in (';', '=>') or (x == ')' and k not in self.back):
                return k
            if depth <= 0:
                return k + 1
            k += 1
        return k

    def skip_type(self, k):
        """k is at ':' after a parameter list: returns the index of the first token after the return type."""
        k += 1
        operand = False
        while k < len(self.t):
            x, kind = self.v(k), self.t[k].kind
            if kind == 'p' and x in '([{':
                if x == '{' and operand:
                    return k
                k = self.close(k) + 1
                operand = True
                continue
            if x == '<':
                k = self.skip_angle(k)
                operand = True
                continue
            if kind == 'p' and x in ('|', '&', '.', '?', '=>') or x in ('keyof', 'typeof', 'readonly', 'is', 'asserts', 'infer', 'unique'):
                operand = False
                k += 1
                continue
            if kind in ('id', 'str', 'num'):
                if operand:
                    return k
                operand = True
                k += 1
                continue
            return k
        return k

    def expr_end(self, k):
        """The last token of the expression that starts at k (an arrow body without braces, an initializer)."""
        start = k
        while k < len(self.t):
            x, tok = self.v(k), self.t[k]
            if tok.kind == 'p' and x in '([{':
                k = self.close(k) + 1
                continue
            if tok.kind == 'p' and x in (')', ']', '}', ',', ';'):
                break
            if k > start and tok.nl and x in STMT_START and self.t[k - 1].kind != 'p':
                break
            k += 1
        return k - 1

    def params_of(self, open_paren):
        """Parameter names of the list that opens at open_paren (the first identifier of each parameter)."""
        out, end, k, depth_start = [], self.close(open_paren), open_paren + 1, True
        while k < end:
            x = self.v(k)
            if depth_start:
                if x == '...':
                    k += 1
                    continue
                if self.is_id(k) and x not in ('this', 'public', 'private', 'protected', 'readonly'):
                    out.append(x)
                elif x in ('{', '['):  # a destructured parameter: its names, all for this position
                    inner = [self.v(j) for j in range(k + 1, self.close(k)) if self.is_id(j) and self.v(j - 1) != ':']
                    out.append('{%s}' % ','.join(inner))
                depth_start = False
            if self.v(k) in ('(', '[', '{') and self.t[k].kind == 'p':
                k = self.close(k) + 1
                continue
            if x == '<':
                k = self.skip_angle(k)
                continue
            if x == ',':
                depth_start = True
            k += 1
        return out

    def name_before(self, k):
        """The name a function expression starting at token k is bound to: `const NAME = `, `NAME = `, `NAME: `."""
        if self.v(k - 1) == 'async':
            k -= 1
        if self.v(k - 1) == '=':
            j = k - 2
            for back in range(j, max(-1, j - 12), -1):
                if self.v(back) in ('const', 'let', 'var') and self.is_id(back + 1):
                    return self.v(back + 1)
                if self.v(back) in (';', '{', '}') or (back < j and self.t[back + 1].nl):
                    break
            if self.is_id(j):
                return self.v(j)
        if self.v(k - 1) == ':' and self.is_id(k - 2) and self.v(k - 3) in ('{', ',', None):
            return self.v(k - 2)
        return None

    # ---- the parse
    def _parse(self):
        t, n = self.t, len(self.t)
        def_heads = set()  # token index of a defined function's name: not a call
        k = 0
        while k < n:
            x = t[k].v
            if t[k].kind == 'id' and x == 'function' and self.v(k - 1) != '.':
                j = k + 1
                if self.v(j) == '*':
                    j += 1
                name = self.v(j) if self.is_id(j) and self.v(j + 1) in ('(', '<') else None
                if name:
                    def_heads.add(j)
                    j += 1
                else:
                    name = self.name_before(k)
                if self.v(j) == '<':
                    j = self.skip_angle(j)
                if self.v(j) == '(':
                    close = self.close(j)
                    b = close + 1
                    if self.v(b) == ':':
                        b = self.skip_type(b)
                    if self.v(b) == '{':
                        self.defs.append(Def(self.file, name or '<anonymous>', t[k].line, self.params_of(j), b, self.close(b)))
            elif t[k].kind == 'p' and x == '=>':
                p = k - 1
                start = None
                if self.v(p) == ')' and p in self.back:
                    start = self.back[p]
                elif self.is_id(p) and self.v(p - 1) != ':':
                    start = p
                else:
                    q = p
                    while q > max(0, k - 80):
                        if self.v(q) == ')' and self.v(q + 1) == ':' and q in self.back:
                            start = self.back[q]
                            break
                        if self.v(q) in (')', ']', '}') and q in self.back:
                            q = self.back[q] - 1
                            continue
                        q -= 1
                params = self.params_of(start) if start is not None and self.v(start) == '(' else \
                    [self.v(start)] if start is not None else []
                name = self.name_before(start) if start is not None else None
                if self.v(k + 1) == '{':
                    b, e = k + 1, self.close(k + 1)
                else:
                    b, e = k + 1, self.expr_end(k + 1)
                if name:
                    self.defs.append(Def(self.file, name, t[k].line, params, b, e, kind='arrow'))
            elif t[k].kind == 'id' and x not in JS_KEYWORDS and self.v(k + 1) in ('(', '<') and self.v(k - 1) not in ('.', '?.', 'function', 'new'):
                # a method or a function written as `name(params) {` (classes, object literals)
                j = k + 1
                if self.v(j) == '<':
                    j = self.skip_angle(j)
                if self.v(j) == '(' and j in self.match and self.v(k - 1) in METHOD_PREV:
                    close = self.close(j)
                    b = close + 1
                    if self.v(b) == ':':
                        b = self.skip_type(b)
                    if self.v(b) == '{' and self.v(k - 1) in METHOD_PREV:
                        def_heads.add(k)
                        self.defs.append(Def(self.file, x, t[k].line, self.params_of(j), b, self.close(b), kind='method'))
            k += 1
        self._imports_exports()
        # innermost enclosing definition per token
        spans = sorted(range(len(self.defs)), key=lambda d: self.defs[d].end - self.defs[d].start)
        self.owner = [None] * n
        for d in reversed(spans):  # widest first, narrower ones overwrite
            D = self.defs[d]
            for i in range(max(0, D.start), min(n, D.end + 1)):
                self.owner[i] = d
        # uses
        skip = self._import_tokens
        for k in range(n):
            if t[k].kind != 'id' or k in skip or k in def_heads:
                continue
            x = t[k].v
            if x in JS_KEYWORDS and x not in ('this',):
                continue
            prev = self.v(k - 1)
            member = prev in ('.', '?.')
            base = None
            if member:
                b = k - 2
                if self.is_id(b) and self.v(b - 1) not in ('.', '?.'):
                    base = self.v(b)
                else:
                    base = '<expr>'
            if not member and self.v(k + 1) == ':' and prev in ('{', ','):
                continue  # an object literal key
            if not member and prev in ('const', 'let', 'var', 'class', 'interface', 'type', 'enum', 'typeof', 'keyof'):
                continue
            nxt = k + 1
            if self.v(nxt) == '?.':
                nxt += 1
            if self.v(nxt) == '<' and t[nxt].kind == 'p':
                close = self.skip_angle(nxt)
                if self.v(close) == '(':
                    nxt = close
            if self.v(nxt) == '(' and nxt in self.match:
                name, b2 = x, base
                if member and x in ('call', 'apply', 'bind') and base not in (None, '<expr>'):
                    name, b2 = base, None  # f.call(this, …) is a call of f
                self.uses.append(Use('call', name, b2, t[k].line, self.owner[k], args=self._args(nxt), pos=k))
            elif member:
                continue  # a property read: never a function reference this analysis can follow
            else:
                if self.v(k + 1) in ('=',) and self.v(k + 2) != '=':
                    continue  # an assignment target
                self.uses.append(Use('ref', x, None, t[k].line, self.owner[k], pos=k))

    def _args(self, open_paren):
        """Token ranges of each argument of a call."""
        out, start, k, end = [], open_paren + 1, open_paren + 1, self.close(open_paren)
        while k < end:
            if self.t[k].kind == 'p' and self.v(k) in '([{':
                k = self.close(k) + 1
                continue
            if self.v(k) == ',':
                out.append((start, k - 1))
                start = k + 1
            k += 1
        if start <= end - 1:
            out.append((start, end - 1))
        return out

    def _imports_exports(self):
        t, n = self.t, len(self.t)
        self._import_tokens = set()

        def spec_at(k):
            return t[k].v if 0 <= k < n and t[k].kind == 'str' else None

        def names_in(open_brace):
            """{ a, b as c, type T, default as d } -> [(imported, local)]"""
            out, k, end = [], open_brace + 1, self.close(open_brace)
            while k < end:
                if self.v(k) == 'type' and self.is_id(k + 1) and self.v(k + 1) not in ('as',):
                    k += 1
                    continue
                if self.is_id(k) or t[k].kind == 'str':
                    a = t[k].v
                    if self.v(k + 1) == 'as' and (self.is_id(k + 2) or t[k + 2].kind == 'str'):
                        out.append((a, t[k + 2].v))
                        self._import_tokens.update((k, k + 2))
                        k += 3
                        continue
                    out.append((a, a))
                    self._import_tokens.add(k)
                k += 1
            return out

        k = 0
        while k < n:
            x = t[k].v
            if t[k].kind == 'id' and x == 'import' and self.v(k - 1) not in ('.',) and self.v(k + 1) != '(':
                j = k + 1
                type_only = self.v(j) == 'type' and self.v(j + 1) != ','
                if type_only:
                    j += 1
                end = j
                while end < n and not (t[end].kind == 'str' and self.v(end - 1) in ('from', 'import')) and self.v(end) != ';' and end - j < 400:
                    end += 1
                spec = spec_at(end)
                if spec is not None and not type_only:
                    m = j
                    while m < end:
                        if self.is_id(m) and self.v(m) not in ('from', 'type') and self.v(m - 1) in ('import', ',', 'type') and self.v(m + 1) in (',', 'from'):
                            self.imports[self.v(m)] = ('name', spec, 'default')
                            self._import_tokens.add(m)
                        elif self.v(m) == '*' and self.v(m + 1) == 'as' and self.is_id(m + 2):
                            self.imports[self.v(m + 2)] = ('ns', spec)
                            self._import_tokens.add(m + 2)
                            m += 2
                        elif self.v(m) == '{':
                            for a, b in names_in(m):
                                self.imports[b] = ('name', spec, a)
                            m = self.close(m)
                        m += 1
                k = end + 1
                continue
            if t[k].kind == 'id' and x == 'export' and self.v(k - 1) != '.':
                j = k + 1
                if self.v(j) == 'type' and self.v(j + 1) in ('{', '*'):
                    k = j + 1
                    continue
                if self.v(j) == '*':
                    if self.v(j + 1) == 'as' and self.is_id(j + 2) and self.v(j + 3) == 'from' and spec_at(j + 4) is not None:
                        self.exports[self.v(j + 2)] = ('ns', spec_at(j + 4))
                        k = j + 5
                        continue
                    if self.v(j + 1) == 'from' and spec_at(j + 2) is not None:
                        self.stars.append(spec_at(j + 2))
                        k = j + 3
                        continue
                if self.v(j) == '{':
                    close = self.close(j)
                    pairs = names_in(j)
                    if self.v(close + 1) == 'from' and spec_at(close + 2) is not None:
                        for a, b in pairs:
                            self.exports[b] = ('from', spec_at(close + 2), a)
                        k = close + 3
                    else:
                        for a, b in pairs:
                            self.exports[b] = ('local', a)
                        k = close + 1
                    continue
                if self.v(j) == 'default':
                    m = j + 1
                    if self.v(m) == 'async':
                        m += 1
                    if self.v(m) in ('function', 'class'):
                        m += 1
                        if self.v(m) == '*':
                            m += 1
                    if self.is_id(m) and self.v(m) not in JS_KEYWORDS:
                        self.default = self.v(m)
                        self.exports['default'] = ('local', self.v(m))
                        self._import_tokens.add(m)
                    k = m
                    continue
                m = j
                while self.v(m) in ('async', 'declare', 'abstract', 'function', 'const', 'let', 'var', 'class', '*',
                                    'interface', 'type', 'enum'):
                    m += 1
                if self.is_id(m):
                    self.exports[self.v(m)] = ('local', self.v(m))
            # const NAME = require('spec') | const { a, b: c } = require('spec') | const NAME = other | NS.member
            if t[k].kind == 'id' and x in ('const', 'let', 'var'):
                j = k + 1
                if self.v(j) in ('{', '[') and self.v(self.close(j) + 1) == '=':
                    close = self.close(j)
                    r = close + 2
                    if self.v(r) == 'require' and self.v(r + 1) == '(' and spec_at(r + 2) is not None:
                        m = j + 1
                        while m < close:
                            if self.is_id(m) and self.v(m + 1) == ':' and self.is_id(m + 2):
                                self.imports[self.v(m + 2)] = ('name', spec_at(r + 2), self.v(m))
                                self._import_tokens.add(m + 2)
                                m += 3
                                continue
                            if self.is_id(m) and self.v(m - 1) in ('{', ','):
                                self.imports[self.v(m)] = ('name', spec_at(r + 2), self.v(m))
                                self._import_tokens.add(m)
                            m += 1
                elif self.is_id(j) and self.v(j + 1) == '=':
                    r = j + 2
                    if self.v(r) == 'await':
                        r += 1
                    if self.v(r) == 'require' and self.v(r + 1) == '(' and spec_at(r + 2) is not None:
                        if self.v(r + 4) == '.' and self.is_id(r + 5):
                            self.imports[self.v(j)] = ('name', spec_at(r + 2), self.v(r + 5))
                        else:
                            self.imports[self.v(j)] = ('ns', spec_at(r + 2))
                        self._import_tokens.add(j)
                    elif self.is_id(r) and self.v(r) not in JS_KEYWORDS and (
                            self.v(r + 1) in (';', None) or (t[r + 1].nl and self.v(r + 1) not in ('.', '?.', '(', '[', '`'))):
                        self.aliases[self.v(j)] = ('local', self.v(r))
                        self._import_tokens.add(j)
                    elif self.is_id(r) and self.v(r + 1) == '.' and self.is_id(r + 2) and (self.v(r + 3) in (';', None) or (r + 3 < n and t[r + 3].nl)):
                        self.aliases[self.v(j)] = ('attr', self.v(r), self.v(r + 2))
                        self._import_tokens.add(j)
            k += 1

    # ---- for the feeder analysis
    def def_tokens(self, d):
        D = self.defs[d]
        return D.start, D.end

    def arg_sources(self, d, arg, depth=0):
        """(pass_through, provider_uses) for an argument token range (a, b) of a call inside definition d.
        pass_through: the argument is only a parameter of d (or a loop variable over one). provider_uses: calls
        whose return values flow into the argument inside d (one function, transitively through locals)."""
        a, b = arg
        params = set()
        if d is not None:
            for p in self.defs[d].params:
                params.update(p.strip('{}').split(',') if p.startswith('{') else [p])
        ids = [k for k in range(a, b + 1) if self.is_id(k) and self.v(k - 1) not in ('.', '?.') and self.v(k) not in JS_KEYWORDS]
        calls = [u for u in self.uses if u.kind == 'call' and u.pos is not None and a <= u.pos <= b]
        # a plain name, maybe with property reads (`x`, `x.y`, `x!`, `...x`): it passes the name's value on
        single = len(ids) == 1 and not calls and all(
            self.is_id(k) or self.v(k) in ('.', '?.', '!', '...') for k in range(a, b + 1))
        if d is None:
            return False, calls
        lo, hi = self.def_tokens(d)
        seen, todo, providers, computed = set(), [], list(calls), not single
        for k in ids:
            todo.append(self.v(k))
        pass_only = single
        while todo:
            name = todo.pop()
            if name in seen or len(seen) > 40:
                continue
            seen.add(name)
            if name in params:
                continue
            found = False
            for k in range(lo, hi + 1):
                if self.v(k) != name or not self.is_id(k) or self.v(k - 1) in ('.', '?.'):
                    continue
                init = None
                if self.v(k + 1) in ('=', '+=', '-=', '||=', '??=', '&&=') and self.v(k + 2) not in ('=', '>'):
                    init = (k + 2, self.expr_end(k + 2))
                elif self.v(k - 1) in ('const', 'let', 'var') and self.v(k + 1) == 'of' or self.v(k + 1) == 'of' and self.v(k - 2) == '(':
                    init = (k + 2, self.expr_end(k + 2))
                elif self.v(k + 1) in ('.', '?.') and self.v(k + 2) in MUTATORS and self.v(k + 3) == '(':
                    init = (k + 4, self.close(k + 3) - 1)
                else:
                    # a destructuring declaration: const { name } = expr / const [a, name] = expr
                    for o in range(k - 1, max(lo, k - 40), -1):
                        if self.v(o) in ('{', '[') and o in self.match and self.match[o] > k and self.v(self.match[o] + 1) == '=' \
                                and self.v(o - 1) in ('const', 'let', 'var'):
                            init = (self.match[o] + 2, self.expr_end(self.match[o] + 2))
                            break
                if init is None:
                    continue
                found = True
                ia, ib = init
                if not (self.v(k + 1) == 'of' and ib == ia and self.v(ia) in params):
                    computed = True
                    pass_only = False
                providers += [u for u in self.uses if u.kind == 'call' and u.pos is not None and ia <= u.pos <= ib]
                for m in range(ia, ib + 1):
                    if self.is_id(m) and self.v(m - 1) not in ('.', '?.') and self.v(m) not in JS_KEYWORDS:
                        todo.append(self.v(m))
            if not found and name not in params:
                pass_only = False
        return pass_only and not computed, providers

    def branching(self, d, terms):
        """(branch points in d, branch lines that mention a watched term)."""
        lo, hi = self.def_tokens(d)
        lines, n = set(), 0
        for k in range(lo, hi + 1):
            if self.v(k) in ('if', '?', '&&', '||', '??', 'case', 'switch', 'while') or (self.v(k) == 'for'):
                n += 1
                lines.add(self.t[k].line)
        watched = 0
        for ln in lines:
            text = self.lines[ln - 1] if 0 < ln <= len(self.lines) else ''
            if any(rx.search(text) for rx in terms):
                watched += 1
        return n, watched


class PyModule(object):
    lang = 'py'

    def __init__(self, rel, text):
        self.file, self.text = rel, text
        self.lines = text.replace('\r\n', '\n').split('\n')
        self.defs, self.imports, self.exports, self.stars, self.aliases = [], {}, {}, [], {}
        self.default = None
        self.uses = []
        self.tree = ast.parse(text, rel)
        self.parents = {}
        for node in ast.walk(self.tree):
            for ch in ast.iter_child_nodes(node):
                self.parents[ch] = node
        self._walk(self.tree, None, '')

    def _params(self, args):
        names = [a.arg for a in getattr(args, 'posonlyargs', [])] + [a.arg for a in args.args]
        if args.vararg:
            names.append('*' + args.vararg.arg)
        names += [a.arg for a in args.kwonlyargs]
        return names

    def _walk(self, node, owner, prefix):
        for ch in ast.iter_child_nodes(node):
            if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef)):
                params = self._params(ch.args)
                if prefix and params and params[0] in ('self', 'cls'):
                    params = params[1:]
                self.defs.append(Def(self.file, ch.name, ch.lineno, params, ch.lineno,
                                     getattr(ch, 'end_lineno', ch.lineno), node=ch, qual=prefix + ch.name))
                idx = len(self.defs) - 1
                for dec in ch.decorator_list:
                    self._walk_expr(dec, owner)
                for dflt in ch.args.defaults + [x for x in ch.args.kw_defaults if x is not None]:
                    self._walk_expr(dflt, owner)
                for stmt in ch.body:
                    self._walk_stmt(stmt, idx, prefix + ch.name + '.')
                continue
            if isinstance(ch, ast.ClassDef):
                for b in ch.bases:
                    self._walk_expr(b, owner)
                for stmt in ch.body:
                    self._walk_stmt(stmt, owner, prefix + ch.name + '.')
                continue
            self._walk_stmt(ch, owner, prefix)

    def _walk_stmt(self, st, owner, prefix):
        if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            holder = ast.Module(body=[st], type_ignores=[])
            self._walk(holder, owner, prefix)
            return
        if isinstance(st, ast.Import):
            for a in st.names:
                if a.asname:
                    self.imports[a.asname] = ('pymod', a.name, 0)
                else:
                    self.imports[a.name.split('.')[0]] = ('pymod', a.name.split('.')[0], 0)
            return
        if isinstance(st, ast.ImportFrom):
            for a in st.names:
                if a.name == '*':
                    self.stars.append(('pymod', st.module or '', st.level))
                    continue
                self.imports[a.asname or a.name] = ('pyname', st.module or '', st.level, a.name)
            return
        if isinstance(st, ast.Assign) and len(st.targets) == 1 and isinstance(st.targets[0], ast.Name):
            v = st.value
            if isinstance(v, ast.Name):
                self.aliases[st.targets[0].id] = ('local', v.id)
            elif isinstance(v, ast.Attribute) and isinstance(v.value, ast.Name):
                self.aliases[st.targets[0].id] = ('attr', v.value.id, v.attr)
            elif isinstance(v, ast.Lambda):
                self.defs.append(Def(self.file, st.targets[0].id, st.lineno, self._params(v.args), st.lineno,
                                     getattr(st, 'end_lineno', st.lineno), node=v, qual=prefix + st.targets[0].id))
                idx = len(self.defs) - 1
                self._walk_expr(v.body, idx)
                return
        for ch in ast.iter_child_nodes(st):
            if isinstance(ch, ast.stmt):
                self._walk_stmt(ch, owner, prefix)
            else:
                self._walk_expr(ch, owner)

    def _walk_expr(self, e, owner):
        for node in ast.walk(e) if isinstance(e, ast.AST) else []:
            if isinstance(node, ast.Call):
                f = node.func
                if isinstance(f, ast.Name):
                    name, base = f.id, None
                elif isinstance(f, ast.Attribute):
                    name = f.attr
                    base = f.value.id if isinstance(f.value, ast.Name) else '<expr>'
                else:
                    continue
                self.uses.append(Use('call', name, base, node.lineno, owner, args=list(node.args), pos=node,
                                     kwargs={k.arg: k.value for k in node.keywords if k.arg}))
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                par = self.parents.get(node)
                if isinstance(par, ast.Call) and par.func is node:
                    continue
                if isinstance(par, ast.Attribute):
                    continue
                self.uses.append(Use('ref', node.id, None, node.lineno, owner, pos=node))

    def arg_sources(self, d, arg, depth=0):
        params = set(p.lstrip('*') for p in self.defs[d].params) if d is not None else set()
        node = self.defs[d].node if d is not None else None
        call_nodes = {id(n) for n in ast.walk(arg) if isinstance(n, ast.Call)}
        providers = [u for u in self.uses if u.kind == 'call' and id(u.pos) in call_nodes]
        single = isinstance(arg, ast.Name)
        if node is None:
            return False, providers
        names = [n.id for n in ast.walk(arg) if isinstance(n, ast.Name)]
        seen, computed, pass_only = set(), not single, single
        while names:
            name = names.pop()
            if name in seen or len(seen) > 40:
                continue
            seen.add(name)
            if name in params:
                continue
            found = False
            for st in ast.walk(node):
                srcs = []
                if isinstance(st, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                    targets = st.targets if isinstance(st, ast.Assign) else [st.target]
                    if any(isinstance(x, ast.Name) and x.id == name for t in targets for x in ast.walk(t)) and st.value is not None:
                        srcs.append(st.value)
                elif isinstance(st, (ast.For, ast.comprehension)):
                    if any(isinstance(x, ast.Name) and x.id == name for x in ast.walk(st.target)):
                        srcs.append(st.iter)
                elif isinstance(st, ast.Call) and isinstance(st.func, ast.Attribute) and st.func.attr in MUTATORS \
                        and isinstance(st.func.value, ast.Name) and st.func.value.id == name:
                    srcs += list(st.args)
                for s in srcs:
                    found = True
                    if not (isinstance(s, ast.Name) and s.id in params and isinstance(st, (ast.For, ast.comprehension))):
                        computed = True
                        pass_only = False
                    ids = {id(n) for n in ast.walk(s) if isinstance(n, ast.Call)}
                    providers += [u for u in self.uses if u.kind == 'call' and id(u.pos) in ids]
                    names += [n.id for n in ast.walk(s) if isinstance(n, ast.Name)]
            if not found:
                pass_only = False
        return pass_only and not computed, providers

    def branching(self, d, terms):
        node = self.defs[d].node
        lines, n = set(), 0
        for x in ast.walk(node):
            if isinstance(x, (ast.If, ast.IfExp, ast.BoolOp, ast.While, ast.For, ast.comprehension)) or \
                    (hasattr(ast, 'Match') and isinstance(x, getattr(ast, 'Match'))):
                n += 1
                ln = getattr(x, 'lineno', None) or getattr(getattr(x, 'target', None), 'lineno', None)
                if ln:
                    lines.add(ln)
        watched = sum(1 for ln in lines if 0 < ln <= len(self.lines) and any(rx.search(self.lines[ln - 1]) for rx in terms))
        return n, watched


# ---------------------------------------------------------------------------- the project graph

class Project(object):
    def __init__(self, root, files):
        self.root = root
        self.files = [f for f in files if f.endswith(JS_EXT + PY_EXT)]
        self.fileset = set(self.files)
        self.mods, self.errors = {}, []
        self._res, self._edges = {}, {}

    def mod(self, rel):
        if rel is None or rel not in self.fileset:
            return None
        if rel not in self.mods:
            path = os.path.join(self.root, rel)
            try:
                if os.path.getsize(path) > MAX_BYTES:
                    self.mods[rel] = None
                    return None
                text = read_text(path)
                self.mods[rel] = PyModule(rel, text) if rel.endswith(PY_EXT) else JsModule(rel, text)
            except (OSError, SyntaxError, ValueError, RecursionError) as e:
                self.errors.append('%s: %s' % (rel, str(e).split('\n')[0][:120]))
                self.mods[rel] = None
        return self.mods[rel]

    def all_mods(self):
        for f in self.files:
            m = self.mod(f)
            if m is not None:
                yield m

    # ---- module resolution
    def js_resolve(self, frm, spec):
        if not spec.startswith('.'):
            return None
        base = os.path.normpath(os.path.join(os.path.dirname(frm), spec)).replace(os.sep, '/')
        cands = [base] + [base + e for e in JS_EXT] + [base + '/index' + e for e in JS_EXT]
        stem, ext = os.path.splitext(base)
        if ext in ('.js', '.mjs', '.cjs', '.jsx'):
            cands += [stem + e for e in ('.ts', '.tsx', '.mts', '.cts')]
        for c in cands:
            if c in self.fileset:
                return c
        return None

    def py_resolve(self, frm, module, level):
        parts = module.split('.') if module else []
        if level:
            base = os.path.dirname(frm)
            for _ in range(level - 1):
                base = os.path.dirname(base)
            roots = [base]
        else:
            roots, d = [''], os.path.dirname(frm)
            while True:  # the importing file's folder and each folder above it (scripts run from there)
                if d not in roots:
                    roots.append(d)
                if not d:
                    break
                d = os.path.dirname(d)
            roots += [r for r in ('src', 'lib') if r not in roots]
        for r in roots:
            p = '/'.join([x for x in [r] + parts if x])
            for c in (p + '.py', p + '/__init__.py'):
                if c in self.fileset:
                    return c
        return None

    def resolve_name(self, rel, name, seen=None):
        """What a local name in a file refers to: ('def', file, name), ('ns', file), ('external', spec) or None."""
        key = (rel, name)
        if seen is None:
            if key in self._res:
                return self._res[key]
            seen = set()
        if key in seen:
            return None
        seen.add(key)
        m = self.mod(rel)
        out = None
        if m is None:
            return None
        if name in m.aliases:
            a = m.aliases[name]
            if a[0] == 'local' and a[1] != name:
                out = self.resolve_name(rel, a[1], seen)
            elif a[0] == 'attr':
                b = self.resolve_name(rel, a[1], seen)
                if b and b[0] == 'ns':
                    out = self.resolve_export(b[1], a[2], seen)
        if out is None and any(d.name == name for d in m.defs):
            out = ('def', rel, name)
        if out is None and name in m.imports:
            imp = m.imports[name]
            if imp[0] == 'ns':
                f = self.js_resolve(rel, imp[1])
                out = ('ns', f) if f else ('external', imp[1])
            elif imp[0] == 'name':
                f = self.js_resolve(rel, imp[1])
                out = self.resolve_export(f, imp[2], seen) if f else ('external', imp[1])
            elif imp[0] == 'pymod':
                f = self.py_resolve(rel, imp[1], imp[2])
                out = ('ns', f) if f else ('external', imp[1])
            elif imp[0] == 'pyname':
                f = self.py_resolve(rel, imp[1], imp[2])
                if f:
                    sub = self.py_resolve(f, imp[3], 1) if f.endswith('__init__.py') else None
                    out = self.resolve_export(f, imp[3], seen) or (('ns', sub) if sub else None)
                else:
                    sub = self.py_resolve(rel, (imp[1] + '.' if imp[1] else '') + imp[3], imp[2])
                    out = ('ns', sub) if sub else ('external', imp[1] or imp[3])
        if out is None and m.lang == 'py':
            for star in m.stars:
                f = self.py_resolve(rel, star[1], star[2])
                if f:
                    out = self.resolve_export(f, name, seen)
                    if out:
                        break
        if len(seen) == 1:
            self._res[key] = out
        return out

    def resolve_export(self, rel, name, seen):
        m = self.mod(rel)
        if m is None:
            return None
        if m.lang == 'py':
            return self.resolve_name(rel, name, seen)
        e = m.exports.get(name)
        if e:
            if e[0] == 'local':
                return self.resolve_name(rel, e[1], seen)
            if e[0] == 'from':
                f = self.js_resolve(rel, e[1])
                return self.resolve_export(f, e[2], seen) if f else ('external', e[1])
            if e[0] == 'ns':
                f = self.js_resolve(rel, e[1])
                return ('ns', f) if f else ('external', e[1])
        if name == 'default' and m.default:
            return self.resolve_name(rel, m.default, seen)
        for star in m.stars:
            f = self.js_resolve(rel, star)
            if f:
                r = self.resolve_export(f, name, seen)
                if r:
                    return r
        return None

    def resolve_use(self, m, u):
        if u.base is None:
            return self.resolve_name(m.file, u.name)
        if u.base == '<expr>':
            return ('member', u.base, u.name)
        b = self.resolve_name(m.file, u.base)
        if b and b[0] == 'ns' and b[1]:
            return self.resolve_export(b[1], u.name, set()) or ('missing', b[1], u.name)
        return ('member', u.base, u.name)

    def def_of(self, sym):
        if not sym or sym[0] != 'def':
            return None, None
        m = self.mod(sym[1])
        if m is None:
            return None, None
        top = [i for i, d in enumerate(m.defs) if d.name == sym[2]]
        return (m, top[0]) if top else (None, None)

    def edges(self, sym):
        """The symbols that the definition of sym calls (or references)."""
        if sym in self._edges:
            return self._edges[sym]
        self._edges[sym] = out = self._edges_of(sym)
        return out

    def _edges_of(self, sym):
        m, d = self.def_of(sym)
        if m is None:
            return []
        out = []
        for u in m.uses:
            if u.enclosing is None:
                continue
            # the use is inside d, or inside a function nested in d
            D, U = m.defs[d], m.defs[u.enclosing]
            if not (D.start <= U.start and U.end <= D.end):
                continue
            r = self.resolve_use(m, u)
            if r and r[0] == 'def':
                out.append(r)
        return out

    def reaches(self, start, goal, limit=4000):
        if start == goal:
            return [start]
        prev, todo, seen = {start: None}, [start], {start}
        while todo and len(seen) < limit:
            s = todo.pop(0)
            for nxt in self.edges(s):
                if nxt not in seen:
                    seen.add(nxt)
                    prev[nxt] = s
                    if nxt == goal:
                        path = [nxt]
                        while prev[path[-1]] is not None:
                            path.append(prev[path[-1]])
                        return list(reversed(path))
                    todo.append(nxt)
        return None


def parse_ref(ref):
    """'path/file.ts:name' -> (file, name)."""
    if not isinstance(ref, str) or ':' not in ref:
        return None
    f, name = ref.rsplit(':', 1)
    f = os.path.normpath(f).replace(os.sep, '/')
    return (f, name) if f and re.match(r'^[A-Za-z_$][\w$]*$', name) else None


def entries_of(c):
    e = c.get('callers')
    return [] if e is None else e if isinstance(e, list) else [e]


def validate(c):
    """Configuration problems of a check's "callers" (a list of texts)."""
    problems = []
    for e in entries_of(c):
        if not isinstance(e, dict) or not parse_ref(e.get('decision')):
            problems.append('each "callers" entry needs "decision": "path/file.ext:functionName"')
            continue
        if e.get('wrapper') is not None and not parse_ref(e['wrapper']):
            problems.append('"callers" "wrapper" must be "path/file.ext:functionName"')
        rep = e.get('replaced') or []
        if not isinstance(rep, list) or not all(parse_ref(x) for x in rep):
            problems.append('"callers" "replaced" must be a list of "path/file.ext:functionName"')
    for a in c.get('allowedCallers') or []:
        if not (isinstance(a, dict) and isinstance(a.get('file'), str)
                and re.match(r'^[0-9a-f]{64}$', str(a.get('line_text_sha256', '')))):
            problems.append('each "allowedCallers" entry needs "file" and "line_text_sha256" (64 hex digits)')
    return problems


def term_matchers(c):
    out = []
    for t in c.get('watchedFields') or []:
        if not isinstance(t, str) or not t.strip():
            continue
        try:
            out.append(re.compile(t[1:-1] if len(t) > 2 and t.startswith('/') and t.endswith('/') else re.escape(t)))
        except re.error:
            pass
    return out


def analyze(root, cfg, files, own_files=None, is_test=None):
    """Per check with "callers": the caller verdict, the unverified callers, the verified ones, and the ranked
    feeders. files: project-relative paths. own_files(c) -> set of paths (and folders ending in /) of the rule
    itself (proved code, emitted code, diff adapters): never reported."""
    is_test = is_test or (lambda rel: bool(TEST_FILE.search(rel)))
    proj = Project(root, files)
    out = {}
    proved_syms = set()
    for c in cfg['checks']:
        for e in entries_of(c):
            for k in ('decision', 'wrapper'):
                r = parse_ref(e.get(k))
                if r:
                    proved_syms.add(('def',) + r)
    for c in cfg['checks']:
        entries = entries_of(c)
        if not entries:
            continue
        res = out[c['name']] = {'verdict': 'CALLERS_OK', 'unverified': [], 'verified': [], 'tests': 0,
                                'not_checked': [], 'reviewed': 0, 'outdated_reviews': [], 'feeders': [],
                                'decisions': [], 'parse_errors': []}
        problems = validate(c)
        if problems:
            res['verdict'] = 'CALLERS_ERROR'
            res['error'] = '; '.join(sorted(set(problems)))
            continue
        own = own_files(c) if own_files else set()

        def is_own(rel):
            return rel in own or any(o.endswith('/') and rel.startswith(o) for o in own)

        allowed = c.get('allowedCallers') or []
        used_allow = set()
        terms = term_matchers(c)
        feeds = {}  # symbol -> {'params': set, 'decisions': set, 'sites': [...]}

        def add_unverified(m, u_line, kind, why, function=None):
            rel = m.file
            text = m.lines[u_line - 1] if 0 < u_line <= len(m.lines) else ''
            h = line_sha(text)
            hit = [k for k, a in enumerate(allowed) if a['file'] == rel and a['line_text_sha256'] == h]
            if hit:
                used_allow.update(hit)
                res['reviewed'] += 1
                return
            item = {'file': rel, 'line': u_line, 'kind': kind, 'why': why, 'text': text.strip()[:200],
                    'function': function, 'line_text_sha256': h}
            if item not in res['unverified']:
                res['unverified'].append(item)

        for e in entries:
            dfile, dname = parse_ref(e['decision'])
            ext = os.path.splitext(dfile)[1]
            if ext not in JS_EXT + PY_EXT:
                res['not_checked'].append('%s: callers not checked (%s files: only JavaScript/TypeScript and Python '
                                          'are analyzed)' % (e['decision'], ext or 'no extension'))
                continue
            lang = 'py' if ext in PY_EXT else 'js'
            D = ('def', dfile, dname)
            W = ('def',) + parse_ref(e['wrapper']) if e.get('wrapper') else D
            R = {('def',) + parse_ref(x) for x in e.get('replaced') or []}
            dm, di = proj.def_of(D)
            if dm is None:
                res['verdict'] = 'CALLERS_ERROR'
                res['error'] = 'the decision %s was not found (no such file, or no function of that name)' % e['decision']
                continue
            Ddef = dm.defs[di]
            info = {'decision': e['decision'], 'wrapper': e.get('wrapper') or e['decision'], 'params': Ddef.params,
                    'line': Ddef.line, 'path_to_wrapper': None}
            res['decisions'].append(info)
            if W != D:
                wm, _ = proj.def_of(W)
                if wm is None:
                    res['verdict'] = 'CALLERS_ERROR'
                    res['error'] = 'the wrapper %s was not found' % e['wrapper']
                    continue
                path = proj.reaches(D, W)
                if path is None:
                    add_unverified(dm, Ddef.line, 'decision-skips-wrapper',
                                   'the decision %s does not reach the wrapper %s in the call graph' % (dname, e['wrapper']),
                                   dname)
                else:
                    info['path_to_wrapper'] = [s[2] for s in path]
            names = {dname} | {r[2] for r in R}
            for m in proj.all_mods():
                if (m.lang == 'py') != (lang == 'py'):
                    continue
                if is_own(m.file):
                    continue
                test = is_test(m.file)
                local_hits = [u for u in m.uses if u.name in names or u.name in m.imports or u.name in m.aliases
                              or (u.base is not None and u.base in m.imports)]
                for u in local_hits:
                    if u.enclosing is not None and ('def', m.file, m.defs[u.enclosing].name) in R:
                        continue  # inside the replaced logic itself
                    r = proj.resolve_use(m, u)
                    fn = m.defs[u.enclosing].qual if u.enclosing is not None else None
                    in_decision = m.file == dfile and fn is not None and fn.split('.')[-1] == dname
                    if r == D:
                        if u.kind != 'call':
                            continue
                        if test:
                            res['tests'] += 1
                            continue
                        res['verified'].append({'file': m.file, 'line': u.line, 'function': fn})
                        if u.enclosing is not None and not in_decision:
                            caller = ('def', m.file, m.defs[u.enclosing].name)
                            if caller in proved_syms:
                                continue
                            for idx, pname in enumerate(Ddef.params):
                                arg = None
                                if m.lang == 'js' and u.args and idx < len(u.args):
                                    arg = u.args[idx]
                                elif m.lang == 'py':
                                    if idx < len(u.args or []):
                                        arg = u.args[idx]
                                    elif pname in u.kwargs:
                                        arg = u.kwargs[pname]
                                if arg is None:
                                    continue
                                pt, providers = m.arg_sources(u.enclosing, arg)
                                if not pt:
                                    f = feeds.setdefault(caller, {'params': set(), 'decisions': set(), 'sites': set(), 'role': 'caller'})
                                    f['params'].add(pname)
                                    f['decisions'].add(e['decision'])
                                    f['of'] = max(f.get('of', 0), len(Ddef.params))
                                    f['sites'].add('%s:%d' % (m.file, u.line))
                                for pu in providers:
                                    pr = proj.resolve_use(m, pu)
                                    if pr and pr[0] == 'def' and pr not in proved_syms and pr != caller and pr != D:
                                        pm, _ = proj.def_of(pr)
                                        if pm is None or is_own(pm.file):
                                            continue
                                        f = feeds.setdefault(pr, {'params': set(), 'decisions': set(), 'sites': set(), 'role': 'provider'})
                                        f['params'].add(pname)
                                        f['decisions'].add(e['decision'])
                                        f['of'] = max(f.get('of', 0), len(Ddef.params))
                                        f['sites'].add('%s:%d' % (m.file, pu.line))
                        continue
                    if r in R:
                        if test:
                            res['tests'] += 1
                            continue
                        add_unverified(m, u.line, 'calls-replaced' if u.kind == 'call' else 'references-replaced',
                                       '%s %s, the logic the proved rule replaces' % (
                                           'calls' if u.kind == 'call' else 'refers to', '%s:%s' % (r[1], r[2])), fn)
                        continue
                    if u.name not in names or u.kind != 'call' or in_decision:
                        continue
                    if r and r[0] == 'def' and (r in proved_syms or proj.reaches(r, W)):
                        continue  # another function of that name that does go through the wrapper
                    if test:
                        res['tests'] += 1
                        continue
                    if r and r[0] == 'def':
                        why = 'calls %s:%s, a function named like the %s that does not reach the wrapper' % (
                            r[1], r[2], 'decision' if u.name == dname else 'replaced logic')
                        kind = 'same-name-reimplementation'
                    elif r and r[0] == 'member':
                        why = 'calls %s.%s(): a member this analysis cannot resolve to the decision' % (u.base, u.name)
                        kind = 'unresolved-member'
                    elif r and r[0] == 'external':
                        why = 'calls %s from %s, outside this project' % (u.name, r[1])
                        kind = 'external'
                    else:
                        why = 'calls %s, which is not defined in the project' % u.name
                        kind = 'undefined'
                    add_unverified(m, u.line, kind, why, fn)
        res['outdated_reviews'] = [a for k, a in enumerate(allowed) if k not in used_allow]
        res['parse_errors'] = list(proj.errors)
        if res['verdict'] != 'CALLERS_ERROR' and res['unverified']:
            res['verdict'] = 'UNVERIFIED_CALLER'
        if res['verdict'] == 'CALLERS_OK' and not res['decisions']:
            res['verdict'] = 'NOT_CHECKED'
        # fan-in and branching of each feeder, then the ranking
        fan_in = {}
        if feeds:
            want = {s[2] for s in feeds}
            for m in proj.all_mods():
                if is_test(m.file):
                    continue
                for u in m.uses:
                    if u.kind == 'call' and u.name in want:
                        r = proj.resolve_use(m, u)
                        if r in feeds:
                            fan_in[r] = fan_in.get(r, 0) + 1
        ranked = []
        for sym, f in feeds.items():
            fm, fd = proj.def_of(sym)
            if fm is None:
                continue
            branches, watched = fm.branching(fd, terms)
            n_params, n_dec, fi = len(f['params']), len(f['decisions']), fan_in.get(sym, 0)
            score = 4 * n_params + 3 * n_dec + min(fi, 10) + (3 if watched else 0) + (1 if branches else 0)
            ranked.append({'function': sym[2], 'file': sym[1], 'line': fm.defs[fd].line, 'role': f['role'],
                           'params': sorted(f['params']), 'of': f.get('of'), 'decisions': sorted(f['decisions']), 'fan_in': fi,
                           'branches': branches, 'watched_branches': watched if terms else None, 'score': score,
                           'sites': sorted(f['sites'])[:8]})
        ranked.sort(key=lambda x: (-x['score'], x['file'], x['function']))
        res['feeders'] = ranked
    return out


def feeder_text(f, decision_params=None):
    of = f.get('of') or (len(decision_params) if decision_params else 0)
    total = ' of %d' % of if of else ''
    bits = ['feeds %d%s parameter%s (%s)' % (len(f['params']), total, '' if len(f['params']) == 1 and not of else 's',
                                               ', '.join(f['params']))]
    if len(f['decisions']) > 1:
        bits.append('%d decisions' % len(f['decisions']))
    bits.append('%d caller%s' % (f['fan_in'], '' if f['fan_in'] == 1 else 's'))
    if f.get('watched_branches'):
        bits.append('branches on watched fields (%d line%s)' % (f['watched_branches'], '' if f['watched_branches'] == 1 else 's'))
    elif f['branches']:
        bits.append('%d branch point%s' % (f['branches'], '' if f['branches'] == 1 else 's'))
    return '%s() (%s:%d, %s): %s' % (f['function'], f['file'], f['line'],
                                      'direct caller' if f['role'] == 'caller' else 'its return value is passed in',
                                      ', '.join(bits))
