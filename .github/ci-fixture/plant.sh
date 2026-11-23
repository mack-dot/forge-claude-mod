#!/usr/bin/env bash
# Change the fixture the way a pull request would, so the Action must fail with one verdict.
#   plant.sh KIND DIR    KIND: clean | spec-weakened | bypass | stale-invariants | logic-bug | unverified-caller
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
  unverified-caller)
    # A new caller of the replaced logic through an import alias: no field is read, so only the caller gate sees it.
    printf 'from legacy_wallet import legacy_spend as quick\n\n\ndef fast_path(cap, spent, amount):\n    return quick(cap, spent, amount)\n' > fast_path.py
    ;;
  stale-invariants)
    printf '\n- W5: refunds are always allowed (edited by hand, not generated)\n' >> INVARIANTS.md
    ;;
  logic-bug)
    # A rare bug in the unproved wrapper: an exact-cap spend is rejected when cap % 250 == 27.
    # About 4 inputs in 10,000 hit it (counted over 2,000,000 generated inputs): a 1,500-input run misses it about
    # half the time; a 50,000-input run misses it with probability about 2e-9. (A first version, cap % 1000 == 777,
    # hit 0.9 in 10,000 and was missed by one 50,000-input CI run: run 45113930328.)
    python3 - <<'PY'
p = 'app.py'
s = open(p).read()
anchor = "    state, outcome = Wallet.default__.Spend("
assert anchor in s
bug = ("    if cap_cents % 250 == 27 and amount_cents == cap_cents - spent_cents:\n"
       "        return False, spent_cents\n")
open(p, 'w').write(s.replace(anchor, bug + anchor, 1))
PY
    ;;
  *) echo "unknown kind $kind"; exit 2 ;;
esac
git -c user.name=fixture -c user.email=fixture@example.invalid add -A
git -c user.name=fixture -c user.email=fixture@example.invalid commit -q --allow-empty -m "planted: $kind"
echo "planted: $kind"
