#!/usr/bin/env bash
# Change the fixture the way a pull request would, so the Action must fail with one verdict.
#   plant.sh KIND DIR    KIND: clean | spec-weakened | bypass | stale-invariants | logic-bug
set -euo pipefail
kind=$1
cd "$2"
git checkout -q -b pr   # the pull request branch; main stays the base
case "$kind" in
  clean) ;;
  spec-weakened)
    # Drop W1 ("the total spent never goes over the cap"): a weaker spec that the old proof still meets.
    python3 - <<'PY'
p = 'spec/WalletSpec.dfy'
s = open(p).read()
assert '    ensures Valid(next)\n' in s
open(p, 'w').write(s.replace('    ensures Valid(next)\n', ''))
PY
    ;;
  bypass)
    # A new function that decides the cap itself instead of calling the proved rule.
    printf 'def refund_then_spend(cap, spent, amount):\n    if spent + amount <= cap:\n        return True\n    return False\n' > refund.py
    ;;
  stale-invariants)
    printf '\n- W5: refunds are always allowed (edited by hand, not generated)\n' >> INVARIANTS.md
    ;;
  logic-bug)
    # A rare bug in the unproved wrapper: an exact-cap spend is rejected when the cap ends in 777.
    # About 1 input in 7,000 hits it, so a 1,500-input run usually misses it and a 50,000-input sweep finds it.
    python3 - <<'PY'
p = 'app.py'
s = open(p).read()
anchor = "    state, outcome = Wallet.default__.Spend("
assert anchor in s
bug = ("    if cap_cents % 1000 == 777 and amount_cents == cap_cents - spent_cents:\n"
       "        return False, spent_cents\n")
open(p, 'w').write(s.replace(anchor, bug + anchor, 1))
PY
    ;;
  *) echo "unknown kind $kind"; exit 2 ;;
esac
git -c user.name=fixture -c user.email=fixture@example.invalid add -A
git -c user.name=fixture -c user.email=fixture@example.invalid commit -q --allow-empty -m "planted: $kind"
echo "planted: $kind"
