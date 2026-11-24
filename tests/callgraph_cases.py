#!/usr/bin/env python3
"""Tests of the caller check (UNVERIFIED_CALLER), the feeder ranking, and the drift status line (cli/forge_callgraph.py
and `forge_check.py callers` / `status`). A synthetic shop app (TypeScript and Python, written for these tests) is
added to a copy of examples/wallet-cap. Needs Dafny for the `check` cases (PATH or FORGE_DAFNY).
Run alone: python3 tests/callgraph_cases.py"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
CLI = os.path.join(HERE, '..', 'cli', 'forge_check.py')
EXAMPLE = os.path.join(HERE, '..', 'examples', 'wallet-cap')
CONFIG = '.forge/forge.json'
DECISIONS = '.forge/decisions/wallet-cap.md'

SHOP = {
    'shop/wallet-proved.ts': (
        '// The thin, unproved wrapper around the proved wallet rule (test fixture).\n'
        'export interface Outcome { ok: boolean; spent: number }\n\n'
        'export function provedSpend(cap: number, spent: number, amount: number): Outcome {\n'
        '  const room = cap - spent\n'
        '  return amount > 0 && amount <= room ? { ok: true, spent: spent + amount } : { ok: false, spent }\n'
        '}\n'),
    'shop/wallet.ts': (
        'import { provedSpend, type Outcome } from "./wallet-proved.ts"\n\n'
        '/** The governed decision: may this amount be spent? */\n'
        'export function trySpend(cap: number, spent: number, amount: number): Outcome {\n'
        '  return provedSpend(cap, spent, amount)\n'
        '}\n'),
    'shop/index.ts': (
        'export { trySpend as spendOrRefuse } from "./wallet.ts"\n'
        'export * from "./wallet-proved.ts"\n'),
    'shop/accounts.ts': (
        'export interface Account { cap: number; spent: number; overdraft: boolean }\n'
        'const table = new Map<string, Account>()\n\n'
        'export const accountOf = (userId: string): Account =>\n'
        '  table.get(userId) ?? { cap: 0, spent: 0, overdraft: false }\n'),
    'shop/money.ts': 'export function roundCents(raw: number): number {\n  return Math.round(raw * 100) / 100\n}\n',
    # the planted HIGH-leverage feeder: computes all three inputs, branches on the watched field, three callers
    'shop/checkout.ts': (
        'import { spendOrRefuse } from "./index.ts"\n'
        'import { accountOf } from "./accounts.ts"\n'
        'import { roundCents } from "./money.ts"\n\n'
        'export function checkout(userId: string, raw: number) {\n'
        '  const acct = accountOf(userId)\n'
        '  const limit = acct.overdraft ? acct.cap * 2 : acct.cap\n'
        '  if (acct.overdraft && raw > 100) return { ok: false, spent: acct.spent }\n'
        '  return spendOrRefuse(limit, acct.spent, roundCents(raw))\n'
        '}\n'),
    # the planted LOW-leverage feeder: passes two inputs through, computes one, one caller, no branch
    'shop/quick.ts': (
        'import { spendOrRefuse as decide } from "./index.ts"\n\n'
        'export const quickSpend = (cap: number, spent: number, raw: number) => decide(cap, spent, Math.trunc(raw))\n'),
    'shop/cart.ts': (
        'import { checkout } from "./checkout.ts"\n'
        'import { quickSpend } from "./quick.ts"\n\n'
        'export class Cart {\n'
        '  pay(userId: string, total: number) {\n'
        '    return checkout(userId, total)\n'
        '  }\n'
        '  payTwice(userId: string, total: number) {\n'
        '    checkout(userId, total)\n'
        '    return checkout(userId, total)\n'
        '  }\n'
        '  tip(cap: number, spent: number, raw: number) { return quickSpend(cap, spent, raw) }\n'
        '}\n'),
    'shop/cart.test.ts': (
        'import { trySpend } from "./wallet.ts"\n'
        'import { legacySpend } from "../legacy_wallet.ts"\n'
        'trySpend(10, 0, 1); legacySpend(10, 0, 1)\n'),
    'shop_py/orders.py': (
        'from app import spend\n'
        'from shop_py.limits import limit_for\n\n\n'
        'def place_order(user, spent_cents, amount_cents):\n'
        '    cap = limit_for(user)\n'
        '    return spend(cap, spent_cents, amount_cents=amount_cents)\n'),
    'shop_py/limits.py': 'def limit_for(user):\n    return 5000 if user.get("tier") == "gold" else 1000\n',
}
PLANTS = {
    'direct': ('shop/refund.ts', 'import { legacySpend } from "../legacy_wallet.ts"\n\n'
               'export function refund(cap: number, spent: number, amount: number) {\n'
               '  return legacySpend(cap, spent, -amount)\n}\n', 4),
    'alias': ('shop/gifts.ts', 'import { legacySpend as fastPath } from "../legacy_wallet.ts"\n\n'
              'export const gift = (cap: number, spent: number, amount: number) =>\n'
              '  fastPath(cap, spent, amount)\n', 4),
    'reexport': ('shop/report.ts', 'import { oldSpend } from "./compat.ts"\n\n'
                 'export function preview(cap: number, spent: number, amount: number) {\n'
                 '  return oldSpend(cap, spent, amount).ok\n}\n', 4),
    'namespace': ('shop/batch.ts', 'import * as L from "../legacy_wallet.ts"\n\n'
                  'export function batch(cap: number, amounts: number[]) {\n'
                  '  let spent = 0\n  for (const a of amounts) spent = L.legacySpend(cap, spent, a).spent\n'
                  '  return spent\n}\n', 5),
    'same-name': ('shop/promo.ts', 'function trySpend(cap: number, spent: number, amount: number) {\n'
                  '  return { ok: amount <= cap - spent, spent }\n}\n\n'
                  'export const promo = (cap: number) => trySpend(cap, 0, 5)\n', 5),
    'py-alias': ('shop_py/fast.py', 'from legacy_wallet import legacy_spend as quick\n\n\n'
                 'def fast(cap, spent, amount):\n    return quick(cap, spent, amount)\n', 5),
    'py-module': ('shop_py/bulk.py', 'import legacy_wallet as lw\n\n\n'
                  'def bulk(cap, amounts):\n    spent = 0\n    for a in amounts:\n'
                  '        _, spent = lw.legacy_spend(cap, spent, a)\n    return spent\n', 7),
}
CALLERS = [{'decision': 'shop/wallet.ts:trySpend', 'wrapper': 'shop/wallet-proved.ts:provedSpend',
            'replaced': ['legacy_wallet.ts:legacySpend']},
           {'decision': 'app.py:spend', 'replaced': ['legacy_wallet.py:legacy_spend']}]


def run(root, *args):
    r = subprocess.run([sys.executable, CLI] + list(args) + ['--root', root], stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, universal_newlines=True)
    try:
        out = json.loads(r.stdout) if '--json' in args else r.stdout
    except ValueError:
        out = {'error': (r.stdout + r.stderr)[-400:]}
    return r.returncode, out


def write(root, rel, text):
    os.makedirs(os.path.dirname(os.path.join(root, rel)) or root, exist_ok=True)
    with open(os.path.join(root, rel), 'w', encoding='utf-8') as f:
        f.write(text)


def approve(root):
    _, shown = run(root, 'spec', '--json')
    code, _ = run(root, 'approve', '--hashes', json.dumps(shown['hashes']))
    return code


def fixture(tmp, py_only_diff=False):
    """examples/wallet-cap plus the shop app, the "callers" entries, and (py_only_diff) a diff of the correct Python
    wrapper only (the example's legacy code keeps planted bugs), approved through `spec --json` / `approve --hashes`."""
    root = os.path.join(tmp, 'p')
    shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns('cache', 'regressions', '__pycache__', 'last-*.json'))
    for rel, text in SHOP.items():
        write(root, rel, text)
    write(root, 'shop/compat.ts', 'export { legacySpend as oldSpend } from "../legacy_wallet.ts"\n')
    cfg = json.load(open(os.path.join(root, CONFIG)))
    c = cfg['checks'][0]
    c['callers'] = CALLERS
    if py_only_diff:
        c['diff'] = [d for d in c['diff'] if d['lang'] == 'py']
        # no longer a diff adapter, so no longer the rule's own file: its call of legacySpend would be a real finding
        os.remove(os.path.join(root, 'diff/wallet_diff.mjs'))
        src = open(os.path.join(root, 'diff/wallet_diff.py')).read()
        src = src.replace("    'legacy': lambda i: _pair(legacy_wallet.legacy_spend(i['cap'], i['spent'], i['amount'])),\n", '')
        write(root, 'diff/wallet_diff.py', src)
        text = open(os.path.join(root, DECISIONS)).read()
        write(root, DECISIONS, text.replace('- Several wallets sharing one cap; concurrency; persistence.',
                                            '- Several wallets sharing one cap; concurrency; persistence.\n'
                                            '- Input checks before `legacySpend` is called (legacy_wallet.ts).'))
    json.dump(cfg, open(os.path.join(root, CONFIG), 'w'), indent=2)
    assert approve(root) == 0
    return root


def cases():
    out = []
    with tempfile.TemporaryDirectory() as tmp:
        root = fixture(tmp)
        code, res = run(root, 'callers', '--json')
        r = (res.get('callers') or {}).get('wallet-cap', {})
        ver = sorted((v['file'], v['function']) for v in r.get('verified', []))
        out.append(('callers: all callers through the decision (a re-export alias, an import alias, Python): exit 0, '
                    'none unverified, the test file not gated',
                    code == 0 and r.get('verdict') == 'CALLERS_OK' and not r.get('unverified')
                    and ('shop/checkout.ts', 'checkout') in ver and ('shop/quick.ts', 'quickSpend') in ver
                    and ('shop_py/orders.py', 'place_order') in ver and r.get('tests') == 2
                    and [d['path_to_wrapper'] for d in r['decisions']][0] == ['trySpend', 'provedSpend']))
        cexit, chk = run(root, 'check', '--json', '--no-write')
        out.append(('gate: all callers through the wrapper: VERIFIED (exit 0)',
                    cexit == 0 and list(chk.get('verdicts', {}).values()) == ['VERIFIED']))
        # the feeder ranking: the planted high-leverage feeder over the low-leverage one
        cfg = json.load(open(os.path.join(root, CONFIG)))
        cfg['checks'][0]['watchedFields'] = ['overdraft']
        json.dump(cfg, open(os.path.join(root, CONFIG), 'w'), indent=2)
        _, res = run(root, 'callers', '--json')
        fs = (res.get('callers') or {}).get('wallet-cap', {}).get('feeders', [])
        by = {f['function']: f for f in fs}
        top = fs[0] if fs else {}
        out.append(('feeders: the planted high-leverage feeder (checkout: 3 inputs, 3 callers, branches on the watched '
                    'field) ranks first, over the low-leverage one (quickSpend: 1 input, 1 caller); Python feeders traced',
                    top.get('function') == 'checkout' and top.get('params') == ['amount', 'cap', 'spent']
                    and top.get('fan_in') == 3 and top.get('watched_branches') == 2
                    and by.get('quickSpend', {}).get('params') == ['amount'] and by['quickSpend']['fan_in'] == 1
                    and by['quickSpend']['score'] < top['score']
                    and by.get('accountOf', {}).get('params') == ['cap', 'spent'] and by['accountOf']['role'] == 'provider'
                    and 'Math' not in by and 'trunc' not in by
                    and by.get('place_order', {}).get('params') == ['cap_cents'] and by['place_order']['role'] == 'caller'
                    and by.get('limit_for', {}).get('params') == ['cap_cents'] and by['limit_for']['role'] == 'provider'))
        cfg['checks'][0].pop('watchedFields')
        json.dump(cfg, open(os.path.join(root, CONFIG), 'w'), indent=2)
        approve(root)
        _, chk = run(root, 'check', '--json', '--no-write')
        nxt = (chk.get('next_rule') or {}).get('wallet-cap', {})
        out.append(('next rule: check names the top feeder (checkout) from the call graph',
                    (nxt.get('feeder') or {}).get('function') == 'checkout' and 'checkout()' in nxt['feeder'].get('text', '')))
        # negative controls: each planted path to the old logic is UNVERIFIED_CALLER (exit 1), named by file:line
        for kind, (rel, text, line) in PLANTS.items():
            write(root, rel, text)
            cexit, chk = run(root, 'check', '--json', '--no-write')
            os.remove(os.path.join(root, rel))
            unv = [(h['file'], h['line']) for h in ((chk.get('callers') or {}).get('wallet-cap') or {}).get('unverified', [])]
            out.append(('gate: planted %s caller of the old logic: UNVERIFIED_CALLER (exit 1) at %s:%d' % (kind, rel, line),
                        cexit == 1 and list(chk.get('verdicts', {}).values()) == ['UNVERIFIED_CALLER'] and unv == [(rel, line)]
                        and '%s:%d' % (rel, line) in chk['detail'].get('wallet-cap', '')))
        # a decision that no longer reaches its wrapper
        write(root, 'shop/wallet.ts', SHOP['shop/wallet.ts'].replace('return provedSpend(cap, spent, amount)',
                                                                       'return { ok: amount <= cap - spent, spent }'))
        code, res = run(root, 'callers', '--json')
        unv = (res.get('callers') or {}).get('wallet-cap', {}).get('unverified', [])
        out.append(('callers: a decision that stops calling the wrapper is unverified (exit 1)',
                    code == 1 and [(h['file'], h['kind']) for h in unv] == [('shop/wallet.ts', 'decision-skips-wrapper')]))
        write(root, 'shop/wallet.ts', SHOP['shop/wallet.ts'])
        # the allow-list: a reviewed caller, recorded by line hash, then approved again
        rel, text, line = PLANTS['direct']
        write(root, rel, text)
        rcode, _ = run(root, 'callers', '--rule', 'wallet-cap', '--record', '%s:%d' % (rel, line), '--note', 'test: reviewed')
        c1, chk1 = run(root, 'check', '--json', '--no-write')
        approve(root)
        c2, chk2 = run(root, 'check', '--json', '--no-write')
        edit = text.replace('-amount', '-amount - 0')
        write(root, rel, edit)
        c3, chk3 = run(root, 'check', '--json', '--no-write')
        out.append(('allow-list: a recorded caller needs approval again (CONFIG_CHANGED), then VERIFIED; editing the line '
                    'makes it UNVERIFIED_CALLER again',
                    rcode == 0 and list(chk1['verdicts'].values()) == ['CONFIG_CHANGED'] and c2 == 0
                    and list(chk2['verdicts'].values()) == ['VERIFIED'] and c3 == 1
                    and list(chk3['verdicts'].values()) == ['UNVERIFIED_CALLER']))
        # other languages: said, not hidden
        cfg = json.load(open(os.path.join(root, CONFIG)))
        cfg['checks'][0]['callers'] = [{'decision': 'legacygo/legacy.go:Spend'}]
        json.dump(cfg, open(os.path.join(root, CONFIG), 'w'), indent=2)
        code, res = run(root, 'callers', '--json')
        r = (res.get('callers') or {}).get('wallet-cap', {})
        out.append(('callers: a Go decision says "callers not checked" (NOT_CHECKED, never a pass)',
                    r.get('verdict') == 'NOT_CHECKED' and 'callers not checked' in ' '.join(r.get('not_checked', []))))
        cfg['checks'][0]['callers'] = [{'decision': 'shop/wallet.ts'}]
        json.dump(cfg, open(os.path.join(root, CONFIG), 'w'), indent=2)
        code, _ = run(root, 'callers', '--json')
        out.append(('callers: a decision without ":function" is a configuration error (exit 2)', code == 2))
    out += status_cases()
    return out


def status_cases():
    out = []
    with tempfile.TemporaryDirectory() as tmp:
        root = fixture(tmp, py_only_diff=True)
        run(root, 'check', '--json')
        dcode, _ = run(root, 'diff', '--json', '--seed', '3', '--inputs', '400')

        def st():
            return run(root, 'status', '--json')[1]

        def green(s):
            return (s.get('level') == 'green' and s['line'].startswith('forge ✓ model PROVED · 0 bypasses · fuzz ')
                    and ' ok · callers ' in s['line'] and s['line'].endswith('records current')
                    and 'end-to-end' not in s['line'] and 'end to end' not in s['line'])

        s0 = st()
        out.append(('status: green after check and a clean fuzz: "model PROVED · 0 bypasses · fuzz N ok · callers N ok · '
                    'records current"', dcode == 0 and green(s0)))
        # drift 1: the code a decision note describes changes
        path = os.path.join(root, 'legacy_wallet.ts')
        text = open(path).read()
        write(root, 'legacy_wallet.ts', text + '// edited after approval\n')
        s1 = st()
        out.append(('status: yellow ⚠ naming the stale decision note',
                    s1.get('level') == 'yellow' and s1['line'].startswith('forge ⚠ DRIFT: decision note stale: ')
                    and 'legacy_wallet.ts' in s1['line'] and [r['kind'] for r in s1['reasons']] == ['DECISION_NOTE_STALE']))
        approve(root)
        out.append(('status: green again after the note is approved again', green(st())))
        # drift 2: the wrapper changes after the last approval / fuzz
        p2 = os.path.join(root, 'shop/wallet-proved.ts')
        write(root, 'shop/wallet-proved.ts', open(p2).read().replace('const room = cap - spent', 'const room = cap - spent  // tuned'))
        s2 = st()
        out.append(('status: yellow ⚠ naming the changed wrapper',
                    s2.get('level') == 'yellow' and 'wrapper changed since last approval/fuzz: shop/wallet-proved.ts' in s2['line']
                    and [r['kind'] for r in s2['reasons']] == ['WRAPPER_CHANGED']))
        dcode2, _ = run(root, 'diff', '--json', '--seed', '4', '--inputs', '400')
        out.append(('status: green again after a clean fuzz run', dcode2 == 0 and green(st())))
        # drift 3: a new unverified caller
        rel, text, line = PLANTS['direct']
        write(root, rel, text)
        ccode, _ = run(root, 'check', '--json')
        s3 = st()
        out.append(('status: yellow ⚠ naming the new unverified caller (check exits 1)',
                    ccode == 1 and s3.get('level') == 'yellow' and ('unverified caller %s:%d' % (rel, line)) in s3['line']
                    and [r['kind'] for r in s3['reasons']] == ['UNVERIFIED_CALLER']))
        os.remove(os.path.join(root, rel))
        ccode2, _ = run(root, 'check', '--json')
        out.append(('status: green again after the caller is removed and check runs', ccode2 == 0 and green(st())))
        # negative control: a failed proof is red ✗, not yellow
        impl = os.path.join(root, 'impl/Wallet.dfy')
        write(root, 'impl/Wallet.dfy', open(impl).read().replace('s.spent + amount <= s.cap {', 's.spent + amount <= s.cap + 1 {'))
        run(root, 'check', '--json')
        s4 = st()
        out.append(('status: a failed proof is red ✗ (not drift)', s4.get('level') == 'red' and s4['line'].startswith('forge ✗ FAILED')))
    return out


if __name__ == '__main__':
    failed = 0
    for name, ok in cases():
        failed += not ok
        print('%s  %s' % ('ok  ' if ok else 'FAIL', name))
    print('%d failed' % failed)
    sys.exit(1 if failed else 0)
