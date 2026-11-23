"""`forge_check.py mutate --app`: how good is the differential fuzz on the person's SHIPPED code?

Plants one small bug at a time (the MUTATIONS list of `mutate`, plus Python's `and`/`or`/`True`/`False`) in the
"watch" files of each diff entry, in a temporary copy of the project (never in the project), and runs that diff
entry on each mutant with a fixed seed and a fixed number of inputs (no time budget, so every run sends the same
inputs).

  caught    the diff tells the mutant apart: for code that agrees with the proved rule, the mutant DISAGREEs;
            for code that already disagrees, the diff's failure kinds or failing decisions change.
  survived  the diff report is the same as for the unmutated code. Either the inputs never reach that case (see
            `diff --coverage`), or the mutant is EQUIVALENT to the original under the spec (harmless: no input
            can tell them apart). Read each one; this tool cannot decide which.
  not run   the mutant does not compile, or the evaluator could not start or hung on it. Not scored.

Python and JS/TS by default. Go, Rust, Java and C# (`--langs all`) rebuild the evaluator per mutant, so they are
slower; the mutant cap (--max) holds across all entries. Test tooling: NOT proved.
"""
import io
import os
import re
import shutil
import sys
import tempfile
import time
import tokenize

DEFAULT_LANGS = ('py', 'js')
PY_EXTRA = [
    ('and to or', r'\band\b', 'or'),
    ('or to and', r'\bor\b', 'and'),
    ('True to False', r'\bTrue\b', 'False'),
    ('False to True', r'\bFalse\b', 'True'),
]
EXT_LANG = {'.py': 'py', '.js': 'js', '.mjs': 'js', '.cjs': 'js', '.ts': 'js', '.mts': 'js', '.cts': 'js',
            '.go': 'c', '.rs': 'c', '.java': 'c', '.cs': 'c'}


def _blank(text, spans):
    chars = list(text)
    for a, b in spans:
        for i in range(a, b):
            if chars[i] != '\n':
                chars[i] = ' '
    return ''.join(chars)


def mask_python(text):
    """Comments and strings (docstrings too) replaced by spaces."""
    starts = [0]
    for line in text.split('\n'):
        starts.append(starts[-1] + len(line) + 1)
    off = lambda rc: starts[rc[0] - 1] + rc[1]
    spans = []
    kinds = {tokenize.COMMENT, tokenize.STRING} | {getattr(tokenize, n) for n in ('FSTRING_START', 'FSTRING_MIDDLE',
                                                                                 'FSTRING_END') if hasattr(tokenize, n)}
    for tok in tokenize.generate_tokens(io.StringIO(text).readline):
        if tok.type in kinds:
            spans.append((off(tok.start), off(tok.end)))
    return _blank(text, spans)


def mask_c_like(text, js=False):
    """Comments and string literals replaced by spaces (JS: '...', "...", `...`; others: "..." only, since ' starts
    a Rust lifetime or a char). Regular-expression literals are not recognised."""
    spans, i, n = [], 0, len(text)
    quotes = '\'"`' if js else '"'
    while i < n:
        c = text[i]
        if text.startswith('//', i):
            j = text.find('\n', i)
            j = n if j < 0 else j
        elif text.startswith('/*', i):
            j = text.find('*/', i + 2)
            j = n if j < 0 else j + 2
        elif c in quotes:
            j = i + 1
            while j < n and text[j] != c and not (c != '`' and text[j] == '\n'):
                j += 2 if text[j] == '\\' else 1
            j = min(j + 1, n)
        else:
            i += 1
            continue
        spans.append((i, j))
        i = j
    return _blank(text, spans)


def sites(path, text):
    """(mutation name, start, end, replacement) for every site outside comments and strings."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from forge_check import MUTATIONS
    kind = EXT_LANG.get(os.path.splitext(path)[1])
    if kind is None:
        return []
    if kind == 'py':
        masked, muts = mask_python(text), MUTATIONS + PY_EXTRA
    else:
        masked, muts = mask_c_like(text, js=kind == 'js'), MUTATIONS
    out = []
    for name, pattern, new in muts:
        for m in re.finditer(pattern, masked):
            out.append((name, m.start(), m.end(), new))
    return sorted(out, key=lambda x: (x[1], x[0]))


def fingerprint(r):
    """What a person sees in the diff report: the verdict, the kinds of wrong answer, the failing decisions."""
    groups = sorted({(d['candidate'], d.get('group', ''), d.get('messy', {}).get('pattern', '') if d.get('messy') else '')
                     for d in r.get('disagreements', [])})
    dec = sorted((f['id'], tuple(f['candidates'])) for f in (r.get('decisions') or {}).get('failing', []))
    return (r.get('verdict'), tuple(groups), tuple(dec))


def _copy_project(root, dest):
    links = [n for n in ('node_modules', '.venv', 'venv') if os.path.isdir(os.path.join(root, n))]
    shutil.copytree(root, dest, symlinks=True,
                    ignore=shutil.ignore_patterns('.git', '__pycache__', 'cache', *links))
    for n in links:  # dependencies are read, never mutated: link them instead of copying
        os.symlink(os.path.join(root, n), os.path.join(dest, n))


def run(root, limit=40, inputs=None, seed=None, langs=None):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import forge_check as fc
    want = None if langs == 'all' else set((langs or ','.join(DEFAULT_LANGS)).split(','))
    seed = 1 if seed is None else seed
    cfg = fc.load_config(root)
    started = time.time()
    out = {'seed': seed, 'entries': {}, 'mutants': [], 'caught': 0, 'survived': 0, 'not_run': 0,
           'skipped_entries': {}}
    jobs = []
    for c in cfg['checks']:
        entries = fc.diff_entries(c)
        for df in entries:
            key = c['name'] if len(entries) == 1 else '%s:%s' % (c['name'], df.get('name') or df['lang'])
            if want is not None and df['lang'] not in want:
                out['skipped_entries'][key] = 'language %s not selected: --langs all, or --langs %s,%s' % (
                    df['lang'], ','.join(sorted(want)), df['lang'])
                continue
            for w in df.get('watch') or []:
                path = fc.inside(root, w)
                if not os.path.isfile(path):
                    continue
                text = fc.read_text(path)
                for site in sites(path, text):
                    jobs.append((key, df, w, text, site))
    total_sites = len(jobs)
    if len(jobs) > limit:  # spread the sample over every file instead of taking the first ones
        step = len(jobs) / float(limit)
        jobs = [jobs[int(i * step)] for i in range(limit)]
    out['sites'], out['sampled'] = total_sites, len(jobs)
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'  # a same-size mutant in the same second must not reuse a .pyc
    with tempfile.TemporaryDirectory(prefix='forge-mutate-app-') as tmp:
        copy = os.path.join(tmp, 'p')
        _copy_project(root, copy)
        reg = os.path.join(copy, fc.REGRESSIONS)
        reg_saved = os.path.join(tmp, 'regressions')
        if os.path.isdir(reg):
            shutil.copytree(reg, reg_saved)

        def diff(key, df, coverage=False):
            if os.path.isdir(reg):
                shutil.rmtree(reg)
            if os.path.isdir(reg_saved):  # every run replays the same saved cases, and nothing found sticks
                shutil.copytree(reg_saved, reg)
            n = inputs if inputs is not None else df.get('inputs_per_run', 1500)
            t0 = time.time()
            r = fc.run_diff(copy, n, 10 ** 9, seed, coverage=coverage, only={key})['checks'][key]
            return r, int((time.time() - t0) * 1000)

        for key in sorted({j[0] for j in jobs}):
            df = next(j[1] for j in jobs if j[0] == key)
            base, ms = diff(key, df, coverage=True)  # coverage: which survivors sit on lines never reached
            out['entries'][key] = {'baseline': base.get('verdict'), 'baseline_ms': ms, 'caught': 0, 'survived': 0,
                                   'not_run': 0, 'inputs': base.get('inputs'), 'fingerprint': fingerprint(base)}
            cov = (base.get('coverage') or {}).get('files') or {}
            out['entries'][key]['_unreached'] = {os.path.normpath(f): set(r.get('unreached_lines') or [])
                                                 for f, r in cov.items()}
            if base.get('verdict') not in ('DIFF_OK', 'DISAGREE'):
                out['entries'][key]['error'] = (base.get('detail') or base.get('error') or '')[:600]
        for key, df, w, text, (mname, start, end, new) in jobs:
            e = out['entries'][key]
            line = text.count('\n', 0, start) + 1
            m = {'entry': key, 'file': w, 'line': line, 'mutation': mname,
                 'text': text.split('\n')[line - 1].strip()[:120]}
            if e.get('error'):
                m['status'], m['detail'] = 'not_run', 'the unmutated code does not run: ' + e['error'][:200]
            else:
                mutated = text[:start] + new + text[end:]
                bad = None
                if w.endswith('.py'):
                    try:
                        compile(mutated, w, 'exec')
                    except SyntaxError as ex:
                        bad = 'syntax error: %s' % ex.msg
                if bad:
                    m['status'], m['detail'] = 'not_run', bad
                else:
                    target = os.path.join(copy, w)
                    with open(target, 'w', encoding='utf-8') as fh:
                        fh.write(mutated)
                    try:
                        r, ms = diff(key, df)
                    finally:
                        with open(target, 'w', encoding='utf-8') as fh:
                            fh.write(text)
                    m['ms'] = ms
                    if r.get('verdict') not in ('DIFF_OK', 'DISAGREE'):
                        m['status'] = 'not_run'
                        why = [l for l in (r.get('detail') or r.get('error') or '').strip().split('\n') if l.strip()]
                        m['detail'] = '%s: %s' % (r.get('verdict'), why[-1].strip()[:200] if why else '')
                    else:
                        m['status'] = 'caught' if fingerprint(r) != e['fingerprint'] else 'survived'
                        m['verdict'] = r.get('verdict')
                        unreached = e['_unreached'].get(os.path.normpath(w))
                        if m['status'] == 'survived' and unreached is not None:
                            m['line_reached'] = line not in unreached
            e[m['status']] += 1
            out[m['status']] += 1
            out['mutants'].append(m)
    for e in out['entries'].values():
        e.pop('fingerprint', None)
        e.pop('_unreached', None)
    out['ms'] = int((time.time() - started) * 1000)
    return out


def report(out):
    scored = out['caught'] + out['survived']
    lines = ['forge mutate --app: %d of %d planted bugs in the shipped code caught by the diff (%d survived, %d not run); '
             'seed %d, %s ms' % (out['caught'], scored, out['survived'], out['not_run'], out['seed'], format(out['ms'], ','))]
    if out['sampled'] < out['sites']:
        lines.append('  sampled %d of %d mutation sites (raise --max for all)' % (out['sampled'], out['sites']))
    for key, e in out['entries'].items():
        lines.append('  %s: %d caught, %d survived, %d not run (unmutated code: %s, %s inputs per run, %d ms per run)'
                     % (key, e['caught'], e['survived'], e['not_run'], e['baseline'], e.get('inputs'), e['baseline_ms']))
        if e.get('error'):
            lines.append('    the unmutated code does not run: ' + e['error'][:300])
    for key, why in out['skipped_entries'].items():
        lines.append('  %s: not mutated (%s)' % (key, why))
    for m in out['mutants']:
        if m['status'] == 'survived':
            where = {True: '  [line reached by the fuzz: equivalent, or the inputs miss the case]',
                     False: '  [line NEVER reached by the fuzz]'}.get(m.get('line_reached'), '')
            lines.append('  SURVIVED %s:%d  %s  in: %s%s' % (m['file'], m['line'], m['mutation'], m['text'][:90], where))
    for m in out['mutants']:
        if m['status'] == 'not_run':
            lines.append('  NOT RUN  %s:%d  %s  (%s)' % (m['file'], m['line'], m['mutation'], m.get('detail', '')[:160]))
    if out['survived']:
        lines.append('A survivor is a change in the shipped code that the fuzz did not notice. Either the inputs never '
                     'reach that case (run diff --coverage, widen the inputs module or add a decision), or the change '
                     'is equivalent to the original under the spec (harmless). This tool cannot tell which: read each one.')
    if not scored:
        lines.append('No mutant was scored: no diff entry with "watch" files in a selected language, or none ran.')
    return '\n'.join(lines)
