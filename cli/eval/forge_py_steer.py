"""Per-input coverage feedback for the Python evaluator (py_eval.py) in a steered diff run (cli/forge_steer.py).

Turned on by the environment: FORGE_STEER_FILES (JSON list of watched files) and FORGE_STEER_OUT (where the
lines and arcs of the whole run go at exit, in the format of py_cover.py, so forge_coverage.py can read them).
For each plain input it tells the core whether the input took a (from line, to line) step in a watched file that no
earlier plain input took ("novel"). Messy inputs count for the end-of-run report only.
The tracer runs while the adapter loads and around the candidate calls only (on(), off()): the reference, the
compiled proved code, runs untraced, so the cost is paid for the shipped code alone. Test tooling: NOT proved."""
import json
import os
import sys


class PySteer:
    def __init__(self, files, out_path):
        self.watched = {os.path.realpath(p) for p in files}
        self.out_path = out_path
        self.known = {}
        self.data = {}  # real path -> (lines, arcs), the whole run
        self.cur = set()  # (path, from, to) taken by the current input
        self.seen = set()  # taken by any earlier plain input
        sys.settrace(self._tracer)  # the adapter's import, including the watched files' top level

    def _file(self, code):
        fn = code.co_filename
        if fn not in self.known:
            rp = os.path.realpath(fn) if fn and not fn.startswith('<') else None
            self.known[fn] = rp if rp in self.watched else None
        return self.known[fn]

    def _tracer(self, frame, event, arg):
        rp = self._file(frame.f_code)
        if rp is None:
            return None
        lines, arcs = self.data.setdefault(rp, (set(), set()))
        cur = self.cur
        prev = [-frame.f_code.co_firstlineno]

        def local(frame, event, arg):
            if event == 'line':
                a = (prev[0], frame.f_lineno)
                lines.add(frame.f_lineno)
                arcs.add(a)
                cur.add((rp,) + a)
                prev[0] = frame.f_lineno
            elif event == 'return':
                a = (prev[0], -frame.f_code.co_firstlineno)
                arcs.add(a)
                cur.add((rp,) + a)
            return local
        return local(frame, event, arg) if event == 'line' else local

    def on(self, plain=True):
        if plain:
            self.cur.clear()
        sys.settrace(self._tracer)

    def off(self):
        sys.settrace(None)

    def plain_done(self):
        """True if the plain input just run took a step no earlier plain input took."""
        new = bool(self.cur - self.seen)
        self.seen |= self.cur
        return new

    def finish(self):
        sys.settrace(None)
        with open(self.out_path, 'w', encoding='utf-8') as f:
            json.dump({p: {'lines': sorted(l), 'arcs': sorted(a)} for p, (l, a) in self.data.items()}, f)


def start():
    files = os.environ.get('FORGE_STEER_FILES')
    if not files:
        return None
    return PySteer(json.loads(files), os.environ['FORGE_STEER_OUT'])
