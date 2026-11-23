"""selftest.yml: did the Action give the verdict each planted change must give?  expect.py KIND OUTCOME REPORT"""
import json
import sys

kind, outcome, path = sys.argv[1:4]
r = json.load(open(path, encoding='utf-8'))
v = r['verdicts']
check = (v.get('check') or {}).get('wallet-cap')
want = {
    'clean': ('success', lambda: check == 'VERIFIED' and v['build'] == {'wallet-cap': 'BUILT'}
              and v['invariants'] == 'INVARIANTS_OK' and v['diff'] == {'wallet-cap': 'DIFF_OK'}
              and r['diff']['wallet-cap']['inputs'] == 50000),
    'spec-weakened': ('failure', lambda: check == 'APPROVAL_CHANGED'),
    'spec-weakened-nobase': ('failure', lambda: check == 'SPEC_CHANGED'),
    'bypass': ('failure', lambda: check == 'UNAPPROVED_BYPASS' and r['bypasses']['unreviewed'] == 1),
    'stale-invariants': ('failure', lambda: check == 'VERIFIED' and v['invariants'] == 'INVARIANTS_STALE'),
    'logic-bug': ('failure', lambda: check == 'VERIFIED' and v['diff'] == {'wallet-cap': 'DISAGREE'}
                  and r['diff']['wallet-cap']['disagreements'] >= 1),
}[kind]
print(json.dumps({'kind': kind, 'outcome': outcome, 'verdicts': v, 'diff': r.get('diff')}, indent=2))
if outcome != want[0] or not want[1]():
    print('NEGATIVE CONTROL FAILED: %s expected outcome %s with its verdict' % (kind, want[0]))
    sys.exit(1)
print('as expected: %s -> %s' % (kind, outcome))
