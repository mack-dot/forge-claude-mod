"""Runner for `forge_crash.py` (CRASH-CHECK) on Python boundary functions. Test tooling: NOT proved.
Usage: python3 crash_py.py <project root> <targets.json>
  targets.json: [{"id", "file", "name", "style": "request|lambda|plain", "params": [...]}]
First line out: {"ready": [ids], "missing": {id: reason}}. Then per line in {"id", "input"}, per line out {"ok": true}
or {"throws": {"name", "message", "at": "file:line", "raised": true if the line is a `raise` statement}}.
{"__undefined__": true} (a missing value) and {"__nan__": true} (float NaN) are markers in inputs.
Styles: request (a Django/Flask-like request object whose JSON body is the input), lambda (event, context), plain
(keyword arguments by parameter name from the input object)."""
import asyncio
import importlib
import importlib.util
import inspect
import json
import linecache
import os
import signal
import sys
import traceback

ROOT = os.path.realpath(sys.argv[1])
TARGETS = json.load(open(sys.argv[2], encoding='utf-8'))
CALL_S = 1
MISSING = object()


def module_for(rel):
    """Import a project file by its package path when it sits in packages (relative imports work), else by path."""
    abs_path = os.path.join(ROOT, rel)
    d, parts = os.path.dirname(abs_path), [os.path.splitext(os.path.basename(abs_path))[0]]
    while os.path.isfile(os.path.join(d, '__init__.py')) and d != ROOT:
        parts.insert(0, os.path.basename(d))
        d = os.path.dirname(d)
    for p in (d, ROOT, os.path.dirname(abs_path)):
        if p not in sys.path:
            sys.path.insert(0, p)
    if len(parts) > 1:
        return importlib.import_module('.'.join(parts))
    spec = importlib.util.spec_from_file_location('forge_crash_' + rel.replace('/', '_').replace('.', '_'), abs_path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def revive(x):
    if isinstance(x, dict) and len(x) == 1 and x.get('__undefined__') is True:
        return MISSING
    if isinstance(x, dict) and len(x) == 1 and x.get('__nan__') is True:
        return float('nan')
    if isinstance(x, list):
        return [revive(v) for v in x if revive(v) is not MISSING]
    if isinstance(x, dict):
        out = {}
        for k, v in x.items():
            r = revive(v)
            if r is not MISSING:
                out[k] = r
        return out
    return x


class FakeRequest:
    """What Django, Flask and similar frameworks hand a view: the JSON body as bytes and as parsed data."""

    def __init__(self, value):
        self._value = None if value is MISSING else value
        text = '' if value is MISSING else value if isinstance(value, str) else json.dumps(value)
        self.body = self.data = text.encode('utf-8', 'surrogatepass')
        self.method = 'POST'
        self.headers = {'Content-Type': 'application/json'}
        self.META = {'CONTENT_TYPE': 'application/json'}
        self.GET = self.args = {}
        self.POST = self.form = self._value if isinstance(self._value, dict) else {}
        self.json = self._value
        self.path = '/'

    def get_json(self, *a, **k):
        return self._value


class FakeContext:
    function_name = 'crash-check'
    aws_request_id = 'crash-check'

    def get_remaining_time_in_millis(self):
        return 30000


def args_for(t, inp):
    v = revive(inp)
    if t['style'] == 'request':
        return [FakeRequest(v)] + [None] * (len(t['params']) - 1), {}
    if t['style'] == 'lambda':
        return [None if v is MISSING else v, FakeContext()][:max(1, len(t['params']))], {}
    o = v if isinstance(v, dict) else {}
    kw = {p: o[p] for p in t['params'] if p in o}
    return [], kw


def where(tb):
    frames = [f for f in traceback.extract_tb(tb) if os.path.realpath(f.filename).startswith(ROOT + os.sep)
              and '/site-packages/' not in f.filename and os.path.realpath(f.filename) != os.path.realpath(__file__)]
    if not frames:
        return None, False
    f = frames[-1]
    line = (f.line or linecache.getline(f.filename, f.lineno) or '').strip()
    return '%s:%d' % (os.path.relpath(os.path.realpath(f.filename), ROOT), f.lineno), line.startswith('raise')


class Hang(BaseException):
    pass


def _alarm(*_):
    raise Hang()


fns, missing = {}, {}
for t in TARGETS:
    try:
        m = module_for(t['file'])
        f = getattr(m, t['name'], None)
        if not callable(f):
            missing[t['id']] = '%s is not a function in %s' % (t['name'], t['file'])
        else:
            fns[t['id']] = (t, f)
    except BaseException as e:  # noqa: BLE001  a module that does not import is reported, not fatal
        missing[t['id']] = 'the module did not import: %s: %s' % (type(e).__name__, str(e)[:200])

has_alarm = hasattr(signal, 'SIGALRM')
if has_alarm:
    signal.signal(signal.SIGALRM, _alarm)
sys.stdout.write(json.dumps({'ready': list(fns), 'missing': missing}) + '\n')
sys.stdout.flush()
for line in sys.stdin:
    if not line.strip():
        continue
    msg = json.loads(line)
    t, f = fns[msg['id']]
    args, kw = args_for(t, msg['input'])
    if has_alarm:
        signal.alarm(CALL_S + 1)
    try:
        r = f(*args, **kw)
        if inspect.isawaitable(r):
            asyncio.run(asyncio.wait_for(r, CALL_S))
        out = {'ok': True}
    except Hang:
        out = {'hang': True}
    except asyncio.TimeoutError:
        out = {'hang': True}
    except Exception as e:  # noqa: BLE001
        at, raised = where(e.__traceback__)
        out = {'throws': {'name': type(e).__name__, 'message': str(e)[:200], 'at': at, 'raised': raised}}
    finally:
        if has_alarm:
            signal.alarm(0)
    sys.stdout.write(json.dumps(out) + '\n')
    sys.stdout.flush()
