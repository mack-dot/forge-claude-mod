"""Line and arc tracer for `forge_check.py diff --coverage` when coverage.py is not installed (stdlib only).
Usage: python3 py_cover.py <out.json> <watched-files.json> <script.py> [args...]
Runs <script.py> as __main__ and records, for the watched files only, every executed line and every
(from line, to line) step; a negative line is a function entry or exit. Written to <out.json> at exit.
Test tooling: NOT proved."""
import atexit
import json
import os
import runpy
import sys
import threading

out_path, watched_path, script = sys.argv[1], sys.argv[2], sys.argv[3]
with open(watched_path, encoding='utf-8') as f:
    WATCHED = {os.path.realpath(p) for p in json.load(f)}
data = {}  # real path -> (set of lines, set of arcs)
known = {}  # co_filename -> real path or None


def _file(code):
    fn = code.co_filename
    if fn not in known:
        rp = os.path.realpath(fn) if fn and not fn.startswith('<') else None
        known[fn] = rp if rp in WATCHED else None
    return known[fn]


def _tracer(frame, event, arg):
    rp = _file(frame.f_code)
    if rp is None:
        return None
    lines, arcs = data.setdefault(rp, (set(), set()))
    prev = [-frame.f_code.co_firstlineno]

    def local(frame, event, arg):
        if event == 'line':
            lines.add(frame.f_lineno)
            arcs.add((prev[0], frame.f_lineno))
            prev[0] = frame.f_lineno
        elif event == 'return':
            arcs.add((prev[0], -frame.f_code.co_firstlineno))
        return local
    return local(frame, event, arg) if event == 'line' else local


def _save():
    sys.settrace(None)
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump({p: {'lines': sorted(l), 'arcs': sorted(a)} for p, (l, a) in data.items()}, f)


atexit.register(_save)
sys.argv = [script] + sys.argv[4:]
threading.settrace(_tracer)
sys.settrace(_tracer)
runpy.run_path(script, run_name='__main__')
