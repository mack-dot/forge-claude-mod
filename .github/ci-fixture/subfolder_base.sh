#!/usr/bin/env bash
# --base with a project in a subfolder (examples/wallet-cap): an edit to the notes part of a decision record is not
# APPROVAL_CHANGED; an edit to a binding part is. Runs on a throwaway clone.  subfolder_base.sh FORGE_SRC WORK
set -euo pipefail
src=$(cd "$1" && pwd)
git clone -q "$src" "$2"
cd "$2"
g() { git -c user.name=fixture -c user.email=fixture@example.invalid "$@"; }
fc="$src/cli/forge_check.py"
ex=examples/wallet-cap
hashes=$(python3 "$fc" spec --json --root $ex | python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin)["hashes"]))')
python3 "$fc" approve --hashes "$hashes" --root $ex   # notes-aware (n:) hash for the decision record
g add -A && g commit -q -m "approve with a notes-aware hash"
rec=$ex/.forge/decisions/wallet-cap.md
printf -- '- a note added under Out of scope\n' >> $rec
python3 "$fc" check --no-write --root $ex --base HEAD | head -2
python3 "$fc" check --no-write --root $ex --base HEAD | grep -q '^✓ wallet-cap: VERIFIED' || { echo "FAIL: a notes edit was not VERIFIED"; exit 1; }
git checkout -q -- $rec
python3 - "$rec" <<'PY'
import sys
p = sys.argv[1]
s = open(p).read()
i = s.index('## Choices')
open(p, 'w').write(s[:i] + '## Choices\n\n- an edited binding choice\n' + s[i + len('## Choices'):])
PY
out=$(python3 "$fc" check --no-write --root $ex --base HEAD || true)
echo "$out" | head -2
echo "$out" | grep -q '^✗ wallet-cap: APPROVAL_CHANGED' || { echo "FAIL: a binding edit was not APPROVAL_CHANGED"; exit 1; }
echo "subfolder --base: notes edit VERIFIED, binding edit APPROVAL_CHANGED"
