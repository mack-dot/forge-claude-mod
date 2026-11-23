# Approvals, INVARIANTS.md and what a green check covers

What you approve, what Forge writes from it, signed approvals with a hardware key (optional), and the exact scope
of a green status line.

[Back to the README](../README.md)

# INVARIANTS.md: every approved rule in one place

`/forge invariants` (and every `/forge approve yes`) writes `INVARIANTS.md`: one table of all approved rules, then
one section per rule with the owner's own words, the guarantees in plain words (the spec's numbered clause
comments), the decisions, what the rule does not guarantee, the code it covers, the languages it is compiled to
and the shipped code it is fuzzed against, plus the trust base every rule rests on. It is generated only from the
approved files, so it cannot drift from them. The Action checks it by default: a pull request fails when
the file is out of date, so any change to a rule shows up in review as a readable diff. See
`examples/wallet-cap/INVARIANTS.md`.

# What a green check covers (and what it does not)

- **Status line:** `model PROVED` (the Dafny model meets the approved spec) · `fuzzed N cases, wrapper NOT proved` (a finite test of the code people run) · bypass count, once a rule names its fields · `records current` or `decision record may be stale`.
- **Stale decision records:** at approval Forge remembers the code a decision record names (file paths, and functions in backticks defined in at most two non-test files). When that code changes, `check` prints `DECISION_NOTE_STALE` (a warning; the proof approval stands).
- **Notes do not need re-approval:** an approval hashes the binding part of a decision record (the rule in words, the defaults, the choices). Sections headed Out of scope, Notes, Background or History can be fixed freely. Approvals written before 0.13 hold the full-file hash; approve once more to get this.
- **Next rule:** `check` names the first Out-of-scope item that points at code in your repo, as the next `/forge:add`.
- **Build sanity step:** `build` first parses every app file a check touches and every JS/TS file that imports them, directly or not (`APP_SYNTAX_ERROR`). `check` reports the same as a warning. Optional `"typecheck": ["npx", "--no-install", "tsc", "--noEmit"]` in a check's build entry runs a real type check. Parsing is not proving: it catches a missing brace, not a wrong type.
- **Shorter approval screen:** `/forge approve` shows a digest: every spec clause in full (predicate and function bodies included; lemma and method bodies left out), the answers by id, the whole decision record, and files that are already approved and unchanged as one line. `/forge approve --full` prints every file. The approval always covers the complete files by hash.

# Signed approvals: only a person with a hardware key can approve (optional)

Without this, an approval is a file. An agent with a shell can write `.forge/approved.json` itself. With signed
approvals, `check` says `VERIFIED` only for an approval that a listed key signed, and the list of keys is read from
a place the agent cannot write.

Set up once (the owner, in a terminal; do not let an agent do it):

```
ssh-keygen -t ed25519-sk -C forge-approval -f ~/.ssh/forge_approval_sk   # FIDO2 key, for example a YubiKey; touch it
mkdir -p ~/.config/forge
echo "you@example.com namespaces=\"forge-approval,forge-call-approval\" $(cut -d' ' -f1,2 ~/.ssh/forge_approval_sk.pub)" \
  | tee .forge/allowed_signers > ~/.config/forge/allowed_signers
export FORGE_APPROVAL_KEY=~/.ssh/forge_approval_sk FORGE_ALLOWED_SIGNERS=~/.config/forge/allowed_signers
```

On macOS the system `ssh-keygen` has no FIDO2 support (measured on macOS 27.0.1: "No FIDO SecurityKeyProvider
specified"). Use Homebrew's OpenSSH (`brew install openssh`; set `FORGE_SSH_KEYGEN=/opt/homebrew/bin/ssh-keygen`
or put it first in PATH), or try Apple's provider: `SSH_SK_PROVIDER=/usr/lib/ssh-keychain.dylib ssh-keygen -t
ecdsa-sk ...` (the library exports the security-key API and refers to LocalAuthentication; whether it keeps the key
in the Secure Enclave behind Touch ID is NOT tested here).

Then in `.forge/forge.json`:

```json
"require_signed_approval": true,
"signed_files": ["forge.yaml", "INVARIANTS.md"],
"signer_key_types": ["sk-ssh-ed25519@openssh.com", "sk-ecdsa-sha2-nistp256@openssh.com"]
```

and approve with `/forge approve yes --hardware` (or `forge_check.py approve --hardware`). The key blinks; touch it.

- **What is signed:** the canonical content of `approved.json` (sorted keys, no spaces, ASCII), with
  `ssh-keygen -Y sign -n forge-approval`, into `.forge/approved.json.sig`. In signed mode `approved.json` also holds
  the SHA-256 of `.forge/allowed_signers` and of every `signed_files` entry (byte for byte; `INVARIANTS.md` is
  generated at approval, then covered).
- **What is verified:** `check` runs `ssh-keygen -Y find-principals` and `-Y verify` against a **trust anchor that is
  not the working tree**: `--allowed-signers FILE` or `FORGE_ALLOWED_SIGNERS` (a file outside the project; one inside
  is refused), else `.forge/allowed_signers` at the `--base` git ref (CI on a pull request: the protected branch).
  Then every covered file must match its signed hash. Anything else is `APPROVAL_UNSIGNED`, **exit 2**: no anchor,
  missing or broken `.sig`, a key that is not in the anchor, a key type outside `signer_key_types`, or any covered
  file changed. The requirement holds if the working `forge.json`, **or the `--base` ref's `forge.json`**, or an
  anchor on the command line asks for it, so a pull request cannot switch it off.
- **Keys:** a FIDO2 `sk-ssh-ed25519@openssh.com` key (OpenSSH checks the touch flag in every signature unless the
  signer line says `no-touch-required`; add `verify-required` to also demand the PIN). On macOS, a Secure Enclave key
  in an ssh-agent (for example the open-source Secretive app) uses the same path (`ssh-keygen -Y sign -f <its .pub>`
  with `SSH_AUTH_SOCK` set; NOT tested here). Such a key is an ordinary `ecdsa-sha2-nistp256` key to the verifier:
  the signature does not show that a person was present, so with `signer_key_types` set to `sk-` types only, it is
  refused. A PIN prompt needs a terminal: use `forge_check.py approve --hardware` there, not the mod.
- **CI:** the Action runs `check --base <base>` on pull requests, so the anchor is the base branch. A push run has no
  base: pass `FORGE_ALLOWED_SIGNERS`, or run `check --base HEAD` on the protected branch. `build` and `mutate` do not
  check the signature (they run after `check`). To turn signing on in a repository with CI, merge
  `.forge/allowed_signers` first (one pull request), then the signed approval with `"require_signed_approval": true`
  (a second one): the second is checked against the keys the first put on the base branch.

Exit gate (`tests/run_signed_approval.py`, also run by `tests/run_tests.py`): a rogue agent with full write access
to the project (`tests/rogue_agent.py`) rewrites the spec, `forge.yaml`, `INVARIANTS.md`, `approved.json` and the
`.sig`, deletes or replaces the `.sig`, switches the requirement off, and adds its own key to `allowed_signers`
and signs with it through the checker's own `approve --hardware`. Every attack gives `APPROVAL_UNSIGNED`, exit 2,
with the anchor outside the project and with the anchor at `--base`; the owner's signed approval gives `VERIFIED`,
exit 0. LABEL: FINITE TEST with **software** ed25519 keys made in a temp folder at test time (no hardware in CI, no
key in the repository); a FIDO2 key goes through the same `ssh-keygen -Y` code path, but its touch is not tested.

Limits (what S1 still trusts): OpenSSH's `ssh-keygen` (NATIVE CHECK, not proved); the anchor (a file outside the
project that the same OS user can still write, or branch protection on the base branch); the owner's key and how it
is kept (only `sk-` keys show a touch); the person reading what they sign. With no anchor and the requirement
switched off in the working tree, nothing is signed (the test measures this too). A signature has no date: an old
signed approval of old files verifies again (rollback); on a pull request `APPROVAL_CHANGED` still shows it.
