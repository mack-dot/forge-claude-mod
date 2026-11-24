#!/usr/bin/env bash
# Make a stand-alone repository from examples/wallet-cap for the Action's negative controls (selftest.yml).
# The example keeps planted bugs in its legacy code on purpose, so the fixture fuzzes only the app's wrapper
# (app.py, correct) against the proved rule, in Python only. The base commit is pushed to a local bare "origin"
# as branch main, so the Action finds a base the same way it does in a real pull request.
#   make.sh FORGE_SRC DEST
set -euo pipefail
src=$(cd "$1" && pwd)
dest=$2
mkdir -p "$dest"
dest=$(cd "$dest" && pwd)
cp -R "$src/examples/wallet-cap/." "$dest/"
[ -e "$dest/.git" ] && { echo "$dest is already a git repository"; exit 2; }
rm -rf "$dest/.forge/cache" "$dest/.forge/regressions" "$dest"/.forge/last-*.json "$dest/.forge/diff-stats.json"
cd "$dest"
python3 - <<'PY'
import json
cfg = json.load(open('.forge/forge.json'))
c = cfg['checks'][0]
c['build']['targets'] = ['py']
c['diff'] = [d for d in c['diff'] if d['lang'] == 'py']
# The governed decision is the app's wrapper; the legacy function is the logic it replaced (UNVERIFIED_CALLER control).
c['callers'] = {'decision': 'app.py:spend', 'replaced': ['legacy_wallet.py:legacy_spend']}
json.dump(cfg, open('.forge/forge.json', 'w'), indent=2)
src = open('diff/wallet_diff.py').read()
# Only the wrapper the app calls; the legacy function keeps its planted bugs for the example's own tests.
src = src.replace("    'legacy': lambda i: _pair(legacy_wallet.legacy_spend(i['cap'], i['spent'], i['amount'])),\n", '')
assert "'legacy'" not in src
open('diff/wallet_diff.py', 'w').write(src)
PY
fc="$src/cli/forge_check.py"
hashes=$(python3 "$fc" spec --json | python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin)["hashes"]))')
python3 "$fc" approve --hashes "$hashes"
rm -rf verified
python3 "$fc" build --no-write
python3 "$fc" invariants
# forge-src/: the checkout of this repository when the fixture is the workspace itself (selftest.yml).
printf '.forge/cache/\n.forge/regressions/\n.forge/last-*.json\n.forge/diff-stats.json\n__pycache__/\nforge-src/\n' > .gitignore
git init -q -b main
git -c user.name=fixture -c user.email=fixture@example.invalid add -A
git -c user.name=fixture -c user.email=fixture@example.invalid commit -q -m "fixture base"
git init -q --bare "$dest.origin.git"
git remote add origin "$dest.origin.git"
git push -q origin main
# A stacked pull request has a base other than main: the Action fetches that name, so the fixture has it too.
if [ -n "${GITHUB_BASE_REF:-}" ] && [ "$GITHUB_BASE_REF" != main ]; then git push -q origin "main:refs/heads/$GITHUB_BASE_REF"; fi
echo "fixture ready: $dest (origin $dest.origin.git)"
