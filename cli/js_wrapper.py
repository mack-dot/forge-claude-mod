"""The generated ES module for the js target of `forge build`. Standard library only.

Dafny's JavaScript output is a script: it exports nothing and calls `require('bignumber.js')`. Loading it from an
app needs a loader (`new Function`, `vm`, `Module._compile`), which a strict Content Security Policy and Cloudflare
Workers forbid, and bignumber.js from npm.

At BUILD time this module writes, next to the emitted `<name>.js`:

  <name>.mjs    one ES module: a pinned, SHA-256-checked copy of bignumber.js (inlined, private), the emitted Dafny
                code (unchanged except the two `require` calls), real `export`s for the Dafny modules, the public
                functions and datatypes, and typed conversion helpers with a bounded string cache.
  <name>.d.mts  TypeScript declarations for it.

No `new Function`, no `eval`, no runtime file reading, no npm install for the app. The output depends only on the
emitted code, the pinned bignumber.js and this file, so it is deterministic and `build --check` covers it.

Labels: the generated module and its helpers are ordinary UNPROVED code (test code checks them); the emitted code
inside is a NATIVE CHECK (Dafny's compilers are not verified).
"""
import hashlib
import json
import os
import re
import subprocess

BIGNUMBER_VERSION = '9.3.1'
# SHA-256 of bignumber.mjs in the npm package bignumber.js@9.3.1 (MIT licence, Michael Mclaughlin).
BIGNUMBER_SHA256 = '476d669a2d8bcc169bce832bdb0e5a531b3c5bac127e94422a56bbff65f6d2b0'
GENERATOR_VERSION = '1'
STRING_CACHE_LIMIT = 4096
REQUIRE_BIGNUMBER = "const BigNumber = require('bignumber.js');\n"
REQUIRE_PROCESS = "require('process')"
HELPER_NAMES = ['toDafnyString', 'fromDafnyString', 'toDafnyInt', 'fromDafnyInt', 'toDafnySeq', 'fromDafnySeq',
                'toDafnySet', 'fromDafnySet', 'setStringCacheLimit', 'stringCacheSize', 'BigNumber', '_dafny']
JS_RESERVED = set('''break case catch class const continue debugger default delete do else enum export extends false
finally for function if import in instanceof let new null return super switch this throw true try typeof var void while
with yield await implements interface package private protected public static arguments eval'''.split())


class WrapError(Exception):
    pass


def _cache_root():
    return os.path.join(os.environ.get('XDG_CACHE_HOME') or os.path.expanduser('~/.cache'), 'forge-check')


def _sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def bignumber_source():
    """The text of bignumber.mjs 9.3.1, checked by SHA-256. Uses the copy that the js build already installs if it
    is that exact file; otherwise installs the pinned version once into its own cache folder."""
    candidates = [os.path.join(_cache_root(), 'js', 'node_modules', 'bignumber.js', 'bignumber.mjs'),
                  os.path.join(_cache_root(), 'js-bignumber-' + BIGNUMBER_VERSION, 'node_modules', 'bignumber.js',
                               'bignumber.mjs')]
    for path in candidates:
        if os.path.isfile(path):
            with open(path, encoding='utf-8', newline='') as f:
                text = f.read()
            if _sha(text) == BIGNUMBER_SHA256:
                return text
    prefix = os.path.dirname(os.path.dirname(os.path.dirname(candidates[1])))
    os.makedirs(prefix, exist_ok=True)
    r = subprocess.run(['npm', 'install', '--silent', '--no-audit', '--no-fund', '--prefix', prefix,
                        'bignumber.js@' + BIGNUMBER_VERSION], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       stdin=subprocess.DEVNULL, universal_newlines=True)
    if r.returncode != 0 or not os.path.isfile(candidates[1]):
        raise WrapError('npm could not install bignumber.js@%s: %s' % (BIGNUMBER_VERSION, r.stdout[-300:]))
    with open(candidates[1], encoding='utf-8', newline='') as f:
        text = f.read()
    if _sha(text) != BIGNUMBER_SHA256:
        raise WrapError('bignumber.js@%s has SHA-256 %s, not the pinned %s' % (BIGNUMBER_VERSION, _sha(text),
                                                                             BIGNUMBER_SHA256))
    return text


def _private_bignumber(src):
    """bignumber.mjs as an expression with no exports: (function () { ...; return BigNumber; })()."""
    src = src.replace('\r\n', '\n')  # the npm file has Windows line ends; the output uses \n only
    a, b = 'export var BigNumber = clone();', 'export default BigNumber;'
    if src.count(a) != 1 or src.count(b) != 1 or re.search(r'^\s*(export|import)\b', src.replace(a, '').replace(b, ''),
                                                               re.M):
        raise WrapError('bignumber.mjs does not have the expected exports')
    return '(function () {\n' + src.replace(a, 'var BigNumber = clone();').replace(b, 'return BigNumber;') + '\n})()'


def _modules(js):
    """Top-level Dafny modules in emitted order: [(name, body text)]."""
    out = []
    for m in re.finditer(r'^let (\w+) = \(function\(\) \{\n', js, re.M):
        if m.group(1) == '_dafny':  # the runtime has no end marker and no public names
            out.append(('_dafny', ''))
            continue
        end = js.find('\n})(); // end of module %s\n' % m.group(1), m.end())
        if end < 0:
            raise WrapError('module %s has no end marker' % m.group(1))
        out.append((m.group(1), js[m.end():end]))
    return out


def _public_names(name, body):
    """Functions and methods of the module's __default class, and its datatype classes."""
    funcs, types = [], []
    m = re.search(r'^  \$module\.__default = class __default \{\n(.*?)^  \};?\n', body, re.M | re.S)
    if m:
        for f in re.finditer(r'^    static (\w+)\(([^)]*)\) \{', m.group(1), re.M):
            funcs.append((f.group(1), [p.strip() for p in f.group(2).split(',') if p.strip()]))
    for t in re.finditer(r'^  \$module\.(\w+) = class (\w+)\b', body, re.M):
        if t.group(1) != '__default':
            types.append(t.group(1))
    return funcs, types


def wrap(js, bignumber_src, source_name, unicode_strings=True, trace_name=None):
    """Returns (module text, declarations text). Raises WrapError if the emitted code is not in the known shape."""
    js = js.replace('\r\n', '\n')
    if js.count(REQUIRE_BIGNUMBER) != 1:
        raise WrapError('the emitted code does not load bignumber.js in the known way')
    body = js.replace(REQUIRE_BIGNUMBER, '').replace(REQUIRE_PROCESS, 'globalThis.process')
    if 'require(' in body:
        raise WrapError('the emitted code calls require() for something other than bignumber.js and process')
    for pattern in (r'\bnew Function\b', r'\beval\s*\(', r'\bFunction\s*\('):
        if re.search(pattern, body):
            raise WrapError('the emitted code matches %s' % pattern)
    mods = _modules(body)
    if not mods or mods[0][0] != '_dafny':
        raise WrapError('the emitted code does not start with the Dafny runtime')
    names = [n for n, _ in mods]
    user = [(n, b) for n, b in mods if not n.startswith('_')]
    flat, seen = {}, {}
    for n, b in user:
        funcs, types = _public_names(n, b)
        for f, params in funcs:
            seen.setdefault(f, []).append((n, 'function', params))
        for t in types:
            seen.setdefault(t, []).append((n, 'type', None))
    taken = set(names) | set(HELPER_NAMES) | JS_RESERVED
    skipped = []
    for k in sorted(seen):
        if len(seen[k]) == 1 and k not in taken and not k.startswith('_'):
            flat[k] = seen[k][0]
        else:
            skipped.append(k)
    head = ('// Generated by forge build (js_wrapper.py, generator %s) from %s. Do not edit: `forge build` writes it,\n'
            '// and `forge build --check` reports STALE_BUILD for any hand edit.\n'
            '// One ES module: no new Function, no eval, no file reading, no npm install. Contains a private copy of\n'
            '// bignumber.js %s (MIT licence, header below) and the code that Dafny emitted (MIT licence).\n'
            '// Labels: the Dafny model is PROVED; the emitted code below is a NATIVE CHECK (Dafny\'s compilers are not\n'
            '// verified); this wrapper and its helpers are UNPROVED (tested).\n'
            % (GENERATOR_VERSION, source_name, BIGNUMBER_VERSION))
    # The runtime (_dafny) comes first; right after it, its string literals are routed through the cache.
    starts = [m.start() for m in re.finditer(r'^let (\w+) = \(function\(\) \{\n', body, re.M)]
    if len(starts) < 2:
        raise WrapError('the emitted code has no module after the Dafny runtime')
    cut = starts[1]
    helpers = HELPERS.replace('@LIMIT@', str(STRING_CACHE_LIMIT)).replace(
        '@UNICODE@', 'true' if unicode_strings else 'false')
    parts = [head, 'const BigNumber = ', _private_bignumber(bignumber_src), ';\n', helpers,
             '// ---- the emitted Dafny code, unchanged except: require(\'bignumber.js\') -> the copy above,\n'
             '// require(\'process\') -> globalThis.process, and one line after the runtime (marked "forge")\n',
             body[:cut], FAST_LITERALS, body[cut:]]
    # Live-call trace for a check's "serviceTest": off (no cost) unless FORGE_TRACE is set at load time; then the
    # first call of a function of the module appends one line "<check>\tjs\t<pid>" to that file.
    traced = ['%s.__default.%s' % (n, f) for n, b in user for f, _ in _public_names(n, b)[0]]
    parts.append('\n// ---- live-call trace for `forge build` (serviceTest): nothing happens unless FORGE_TRACE is set\n'
                 'const FORGE_TRACE = globalThis.process && globalThis.process.env ? globalThis.process.env.FORGE_TRACE : undefined;\n'
                 'if (FORGE_TRACE) {\n'
                 '  let called = false;\n'
                 '  const mark = () => {\n'
                 '    if (called) return;\n'
                 '    called = true;\n'
                 '    try {\n'
                 '      globalThis.process.getBuiltinModule(\'node:fs\').appendFileSync(FORGE_TRACE, %s + \'\\tjs\\t\' + globalThis.process.pid + \'\\n\');\n'
                 '    } catch (e) { /* no trace: the build reports NOT_CALLED */ }\n'
                 '  };\n'
                 '  const traced = f => function (...args) { mark(); return f.apply(this, args); };\n'
                 % json.dumps(trace_name or source_name))
    for x in traced:
        parts.append('  %s = traced(%s);\n' % (x, x))
    parts.append('}\n')
    exports = ['_dafny', 'BigNumber'] + [n for n in names if n != '_dafny']
    parts.append('\n// ---- exports\nexport { %s };\n' % ', '.join(exports))
    for k in sorted(flat):
        mod, kind, _ = flat[k]
        if kind == 'function':
            parts.append('export const %s = %s.__default.%s.bind(%s.__default);\n' % (k, mod, k, mod))
        else:
            parts.append('export const %s = %s.%s;\n' % (k, mod, k))
    if skipped:
        parts.append('// not exported by their bare name (a clash or a reserved name; use Module.__default.name): %s\n'
                     % ', '.join(skipped))
    return ''.join(parts), _declarations(names, user, flat, source_name)


def _declarations(names, user, flat, source_name):
    out = ['// Generated by forge build from %s. Do not edit. Types of Dafny values are `any`: the emitted code\n'
           '// is untyped. See the helpers for plain JavaScript values.\n' % source_name,
           'export type DafnyValue = any;\n',
           '/** A Dafny seq (an Array subclass of the Dafny runtime). Treat it as immutable. */\n'
           'export type DafnySeq<T = DafnyValue> = readonly T[] & DafnyValue;\n',
           '/** A Dafny int: a bignumber.js BigNumber. */\nexport type DafnyInt = DafnyValue;\n',
           'export declare const _dafny: any;\nexport declare const BigNumber: any;\n']
    for n in names:
        if n != '_dafny':
            out.append('export declare const %s: any;\n' % n)
    for k in sorted(flat):
        mod, kind, params = flat[k]
        if kind == 'function':
            out.append('/** %s.%s, as Dafny emitted it. Arguments and result are Dafny values. */\n'
                       'export declare function %s(%s): any;\n'
                       % (mod, k, k, ', '.join('%s: DafnyValue' % p for p in params)))
        else:
            out.append('/** The datatype %s.%s (create_<Ctor>, is_<Ctor>, dtor_<field>). */\n'
                       'export declare const %s: any;\n' % (mod, k, k))
    out.append(DECLS)
    return ''.join(out)


# Dafny's runtime builds a new seq for each string literal at each evaluation (UnicodeFromString). Routed through the
# cache, equal strings are often the same frozen object, so Seq.equals returns at `this === other`. Sound because
# Dafny seqs are values: the runtime copies a seq to update or concatenate it and never changes one in place (sets,
# multisets and maps are changed only while they are built). A change to a frozen seq would throw (modules are
# strict), not give another answer. The fuzz harness runs through this same module at every build.
FAST_LITERALS = ('if (UNICODE_STRINGS) _dafny.Seq.UnicodeFromString = toDafnyString; '
                 '// forge: cached, frozen string literals\n')

HELPERS = r'''
// ---- helpers: plain JavaScript values <-> Dafny values. UNPROVED code.
const UNICODE_STRINGS = @UNICODE@;
let stringCacheLimit = @LIMIT@;
// Two generations (JS string -> frozen Dafny seq<char>): a hit costs one Map lookup. When the young generation is
// full it becomes the old one and the old one is dropped, so at most stringCacheLimit + 1 strings are held.
let young = new Map(), old = new Map();

function convertString(s) {
  if (!UNICODE_STRINGS) return s;
  const seq = new _dafny.Seq();
  for (let i = 0; i < s.length; ) {
    const c = s.codePointAt(i);
    if (c >= 0xD800 && c <= 0xDFFF) throw new RangeError('toDafnyString: a lone surrogate at index ' + i + ' is not a Dafny char');
    seq.push(Object.freeze(new _dafny.CodePoint(c)));
    i += c > 0xFFFF ? 2 : 1;
  }
  return Object.freeze(seq);
}

/** A JS string as a Dafny string (seq<char>). Results are cached (bounded) and frozen: Dafny seqs are values and the
 * emitted code never changes one, so a cached seq gives the same results as a fresh one. */
export function toDafnyString(s) {
  let seq = young.get(s);
  if (seq !== undefined) return seq;
  if (typeof s !== 'string') throw new TypeError('toDafnyString: not a string: ' + typeof s);
  if (stringCacheLimit <= 0) return convertString(s);
  seq = old.get(s);
  if (seq === undefined) seq = convertString(s);
  if (young.size >= (stringCacheLimit + 1) >> 1) {
    old = young;
    young = new Map();
  }
  young.set(s, seq);
  return seq;
}

/** A Dafny string (seq<char>) as a JS string. */
export function fromDafnyString(seq) {
  if (typeof seq === 'string') return seq;
  if (!(seq instanceof _dafny.Seq)) throw new TypeError('fromDafnyString: not a Dafny seq');
  let out = '';
  for (let i = 0; i < seq.length; i++) out += String.fromCodePoint(seq[i].value);
  return out;
}

/** Sets the number of cached strings (0 turns the cache off) and empties the cache. */
export function setStringCacheLimit(n) {
  if (!Number.isSafeInteger(n) || n < 0) throw new RangeError('setStringCacheLimit: not a count');
  stringCacheLimit = n;
  young = new Map();
  old = new Map();
}
/** The number of strings the cache holds now (old and young generations; a string can be in both). */
export const stringCacheSize = () => young.size + old.size;

/** A JS integer as a Dafny int. Exact: a number must be a safe integer (above 2^53 pass a BigInt or a string). */
export function toDafnyInt(x) {
  if (typeof x === 'number') {
    if (!Number.isSafeInteger(x)) throw new RangeError('toDafnyInt: ' + x + ' is not a safe integer; pass a BigInt or a string');
    return new BigNumber(x);
  }
  if (typeof x === 'bigint') return new BigNumber(x.toString());
  if (typeof x === 'string' && /^-?[0-9]+$/.test(x)) return new BigNumber(x);
  if (BigNumber.isBigNumber(x) && x.isInteger()) return x;
  throw new TypeError('toDafnyInt: not an integer');
}

/** A Dafny int as a JS number when it is a safe integer, else as a BigInt (exact). A native-int newtype that Dafny
 * already emits as a JS number is returned as it is. */
export function fromDafnyInt(b) {
  if (typeof b === 'number') return b;
  if (!BigNumber.isBigNumber(b) || !b.isInteger()) throw new TypeError('fromDafnyInt: not a Dafny int');
  // b.e is the base-10 exponent (a documented property of bignumber.js): below 15 means |b| < 10^15 < 2^53.
  if (b.e < 15) return b.toNumber();
  return b.abs().isLessThanOrEqualTo(Number.MAX_SAFE_INTEGER) ? b.toNumber() : BigInt(b.toFixed());
}

const same = x => x;
/** A JS array as a Dafny seq; `each` converts one element (default: none). */
export function toDafnySeq(xs, each = same) {
  const seq = new _dafny.Seq();
  for (const x of xs) seq.push(each(x));
  return seq;
}
/** A Dafny seq as a JS array. */
export function fromDafnySeq(seq, each = same) {
  const out = new Array(seq.length);
  for (let i = 0; i < seq.length; i++) out[i] = each(seq[i]);
  return out;
}
/** A JS iterable as a Dafny set (duplicates by Dafny equality are dropped). */
export function toDafnySet(xs, each = same) {
  const elems = [];
  for (const x of xs) elems.push(each(x));
  return _dafny.Set.fromElements(...elems);
}
/** A Dafny set as a JS array, in the set's own order. */
export function fromDafnySet(set, each = same) {
  const out = [];
  for (const x of set.Elements) out.push(each(x));
  return out;
}
'''

DECLS = '''
/** A JS string as a Dafny string (cached, frozen). Throws RangeError on a lone surrogate. */
export declare function toDafnyString(s: string): DafnySeq;
/** A Dafny string as a JS string. */
export declare function fromDafnyString(seq: DafnySeq): string;
/** Sets the string cache size (0 = off) and empties it. */
export declare function setStringCacheLimit(n: number): void;
export declare function stringCacheSize(): number;
/** Exact: a number must be a safe integer. */
export declare function toDafnyInt(x: number | bigint | string | DafnyInt): DafnyInt;
/** A number when safe, else a BigInt. */
export declare function fromDafnyInt(b: DafnyInt | number): number | bigint;
export declare function toDafnySeq<T>(xs: Iterable<T>, each?: (x: T) => DafnyValue): DafnySeq;
export declare function fromDafnySeq<T = DafnyValue>(seq: DafnySeq, each?: (x: DafnyValue) => T): T[];
export declare function toDafnySet<T>(xs: Iterable<T>, each?: (x: T) => DafnyValue): DafnyValue;
export declare function fromDafnySet<T = DafnyValue>(set: DafnyValue, each?: (x: DafnyValue) => T): T[];
'''


def write_module(js_dir, name, trace_name=None):
    """Writes <name>.mjs and <name>.d.mts next to <name>.js in js_dir. Returns None, or an error text."""
    src = os.path.join(js_dir, name + '.js')
    try:
        with open(src, encoding='utf-8', newline='') as f:
            js = f.read()
        dtr = os.path.join(js_dir, name + '-js.dtr')
        options = open(dtr, encoding='utf-8').read() if os.path.isfile(dtr) else ''
        # Dafny 4 strings are seq<char> of code points unless --unicode-char false (then JS strings)
        unicode_strings = not re.search(r'unicode-char\s*=\s*false', options)
        mjs, dts = wrap(js, bignumber_source(), name + '.js', unicode_strings, trace_name)
    except (OSError, WrapError) as e:
        return 'the ES module for %s.js: %s' % (name, e)
    for ext, text in (('.mjs', mjs), ('.d.mts', dts)):
        with open(os.path.join(js_dir, name + ext), 'w', encoding='utf-8', newline='\n') as f:
            f.write(text)
    return None
