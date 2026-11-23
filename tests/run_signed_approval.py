#!/usr/bin/env python3
"""Exit gate of signed approvals (S1): only the holder of a trusted key can make `check` say VERIFIED.

Each case copies examples/wallet-cap, turns on "require_signed_approval" (signed_files: forge.yaml, INVARIANTS.md),
approves it as the owner with `approve --hashes ... --hardware --key <owner key>`, lets tests/rogue_agent.py
attack it, and requires one verdict and one exit code from `check`, in two trust-anchor modes:
  ext   --allowed-signers <file outside the project> (a person's machine)
  base  .forge/allowed_signers at the git ref --base (CI on a pull request; the base branch is protected)

LABEL: FINITE TEST. The keys are SOFTWARE ed25519 keys generated in a temp folder at test time (no hardware in
CI; no key is in the repository). A FIDO2 `sk-ssh-ed25519@openssh.com` key goes through the same code path
(`ssh-keygen -Y sign` / `-Y verify`); only the touch is not tested here. Needs ssh-keygen (OpenSSH 8.2+), git
and Dafny (for the VERIFIED cases).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
CLI = os.path.join(HERE, '..', 'cli', 'forge_check.py')
ROGUE = os.path.join(HERE, 'rogue_agent.py')
EXAMPLE = os.path.join(HERE, '..', 'examples', 'wallet-cap')
FORGE_YAML = """version: 1
upstreams:
  - name: github
    transport: stdio
    command: [npx, -y, "@modelcontextprotocol/server-github"]
tools:
  deny: [delete_repository]
  allow:
    - tool: get_file_contents
"""


def git(root, *args):
    subprocess.run(['git', '-C', root, '-c', 'user.name=t', '-c', 'user.email=t@example.invalid',
                    '-c', 'commit.gpgsign=false'] + list(args), check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def keygen(path, comment):
    subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-C', comment, '-f', path], check=True)
    t, b = open(path + '.pub', encoding='ascii').read().split()[:2]
    return 'owner@forge.test namespaces="forge-approval,forge-call-approval" %s %s\n' % (t, b)


def run(args, **kw):
    return subprocess.run([sys.executable] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True, **kw)


def approve(root, key):
    shown = json.loads(run([CLI, 'spec', '--root', root, '--json']).stdout)['hashes']
    r = run([CLI, 'approve', '--root', root, '--hashes', json.dumps(shown), '--hardware', '--key', key])
    if r.returncode != 0:
        raise SystemExit('owner approval failed: %s %s' % (r.stdout, r.stderr))


def owner_project(tmp, owner_line, key, extra_cfg=None):
    root = os.path.join(tmp, 'p')
    shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns('cache', 'regressions', '__pycache__'))
    open(os.path.join(root, 'forge.yaml'), 'w').write(FORGE_YAML)
    open(os.path.join(root, '.forge/allowed_signers'), 'w').write(owner_line)
    path = os.path.join(root, '.forge/forge.json')
    cfg = json.load(open(path))
    cfg.update({'require_signed_approval': True, 'signed_files': ['forge.yaml', 'INVARIANTS.md']}, **(extra_cfg or {}))
    json.dump(cfg, open(path, 'w'), indent=2)
    approve(root, key)
    return root


def check(root, mode, anchor):
    cmd = [CLI, 'check', '--root', root, '--json', '--no-write']
    cmd += ['--allowed-signers', anchor] if mode == 'ext' else ['--base', 'base'] if mode == 'base' else []
    env = dict(os.environ)
    env.pop('FORGE_ALLOWED_SIGNERS', None)
    r = run(cmd, env=env)
    try:
        res = json.loads(r.stdout)
        v = sorted(set(res['verdicts'].values()))
        why = '; '.join(res['detail'].values())[:160] if v != ['VERIFIED'] else ''
    except (ValueError, KeyError):
        v, why = ['<no result>'], (r.stdout + r.stderr)[-200:]
    return ','.join(v), r.returncode, why


# (name, attack or None, anchor modes, wanted verdict, wanted exit)
CASES = [
    ('positive: owner-signed approval, no change', None, ('ext', 'base'), 'VERIFIED', 0),
    ('rogue: weakens the spec', 'spec', ('ext', 'base'), 'APPROVAL_UNSIGNED', 2),
    ('rogue: rewrites forge.yaml (airlock policy)', 'yaml', ('ext', 'base'), 'APPROVAL_UNSIGNED', 2),
    ('rogue: rewrites INVARIANTS.md', 'invariants', ('ext', 'base'), 'APPROVAL_UNSIGNED', 2),
    ('rogue: rewrites approved.json, keeps the old .sig', 'approved', ('ext', 'base'), 'APPROVAL_UNSIGNED', 2),
    ('rogue: deletes the .sig', 'sig-delete', ('ext', 'base'), 'APPROVAL_UNSIGNED', 2),
    ('rogue: replaces the .sig (its key, other bytes)', 'sig-garbage', ('ext', 'base'), 'APPROVAL_UNSIGNED', 2),
    ('rogue: adds its key to allowed_signers, re-signs', 'own-key', ('ext', 'base'), 'APPROVAL_UNSIGNED', 2),
    ('rogue: switches require_signed_approval off', 'disable', ('ext', 'base'), 'APPROVAL_UNSIGNED', 2),
    ('rogue: everything at once (spec+yaml+inv+key+sig)', 'full', ('ext', 'base'), 'APPROVAL_UNSIGNED', 2),
    ('no trust anchor outside the project', None, ('none',), 'APPROVAL_UNSIGNED', 2),
    # LIMIT, measured so the documentation stays true: with no anchor at all and the switch off, nothing is signed.
    ('LIMIT: no anchor anywhere + rogue switches it off', 'disable', ('none',), 'VERIFIED', 0),
]


def main():
    if not shutil.which('ssh-keygen'):
        print('FAIL  ssh-keygen is not on PATH')
        return 1
    failed = total = 0
    with tempfile.TemporaryDirectory(prefix='forge-sig-test-') as keys:
        owner_key = os.path.join(keys, 'owner')
        owner_line = keygen(owner_key, 'forge-test-owner (software key)')
        anchor_dir = os.path.join(keys, 'anchor')
        os.makedirs(anchor_dir)
        anchor = os.path.join(anchor_dir, 'allowed_signers')
        open(anchor, 'w').write(owner_line)
        for name, attack, modes, want_v, want_x in CASES:
            for mode in modes:
                with tempfile.TemporaryDirectory() as tmp:
                    root = owner_project(tmp, owner_line, owner_key)
                    if mode == 'base':
                        git(root, 'init', '-q')
                        git(root, 'add', '.')
                        git(root, 'commit', '-q', '-m', 'base')
                        git(root, 'branch', 'base')
                    if attack:
                        r = subprocess.run([sys.executable, ROGUE, attack, '--root', root,
                                            '--keys', os.path.join(tmp)], stdout=subprocess.PIPE,
                                           stderr=subprocess.STDOUT, universal_newlines=True)
                        if r.returncode != 0:
                            print('FAIL  rogue script %s: %s' % (attack, r.stdout[-300:]))
                            failed += 1
                            total += 1
                            continue
                    v, x, why = check(root, mode, anchor)
                    ok = v == want_v and x == want_x
                    total += 1
                    failed += not ok
                    print('%s  %-52s %-4s want %s/%d  got %s/%d%s' % ('ok  ' if ok else 'FAIL', name, mode, want_v,
                          want_x, v, x, ('  (' + why + ')') if why and not ok else ''))
        # the owner changes a rule and signs again: VERIFIED (ext); a key restriction to sk- keys refuses software keys
        with tempfile.TemporaryDirectory() as tmp:
            root = owner_project(tmp, owner_line, owner_key)
            p = os.path.join(root, '.forge/decisions/wallet-cap.md')
            open(p, 'a').write('\n## Choices addendum\n\nOwner note: re-approved.\n')
            v1, x1, _ = check(root, 'ext', anchor)
            approve(root, owner_key)
            v2, x2, _ = check(root, 'ext', anchor)
            ok = (v1, x1, v2, x2) == ('APPROVAL_UNSIGNED', 2, 'VERIFIED', 0)
            total += 1
            failed += not ok
            print('%s  %-57s want APPROVAL_UNSIGNED/2 then VERIFIED/0  got %s/%d then %s/%d' % (
                'ok  ' if ok else 'FAIL', 'owner edits a rule, then signs again (ext)', v1, x1, v2, x2))
        with tempfile.TemporaryDirectory() as tmp:
            root = owner_project(tmp, owner_line, owner_key,
                                 {'signer_key_types': ['sk-ssh-ed25519@openssh.com']})
            v, x, _ = check(root, 'ext', anchor)
            ok = (v, x) == ('APPROVAL_UNSIGNED', 2)
            total += 1
            failed += not ok
            print('%s  %-57s want APPROVAL_UNSIGNED/2  got %s/%d' % (
                'ok  ' if ok else 'FAIL', 'signer_key_types [sk-ssh-ed25519]: software key refused', v, x))
        # the mod regenerates INVARIANTS.md after an approval: the signed copy must equal a fresh generation
        with tempfile.TemporaryDirectory() as tmp:
            root = owner_project(tmp, owner_line, owner_key)
            r = run([CLI, 'invariants', '--root', root, '--check'])
            run([CLI, 'invariants', '--root', root])
            v, x, _ = check(root, 'ext', anchor)
            ok = r.returncode == 0 and (v, x) == ('VERIFIED', 0)
            total += 1
            failed += not ok
            print('%s  %-57s want INVARIANTS_OK, then VERIFIED/0  got %d, %s/%d' % (
                'ok  ' if ok else 'FAIL', 'signed INVARIANTS.md = a fresh generation', r.returncode, v, x))
        # bootstrap in CI: the base branch has only allowed_signers; the pull request turns signing on and signs.
        # The signature verifies against the base's keys; the approval change itself still needs review.
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, 'p')
            shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns('cache', 'regressions', '__pycache__'))
            open(os.path.join(root, '.forge/allowed_signers'), 'w').write(owner_line)
            git(root, 'init', '-q')
            git(root, 'add', '.')
            git(root, 'commit', '-q', '-m', 'base')
            git(root, 'branch', 'base')
            open(os.path.join(root, 'forge.yaml'), 'w').write(FORGE_YAML)
            path = os.path.join(root, '.forge/forge.json')
            cfg = json.load(open(path))
            cfg.update({'require_signed_approval': True, 'signed_files': ['forge.yaml', 'INVARIANTS.md']})
            json.dump(cfg, open(path, 'w'), indent=2)
            approve(root, owner_key)
            v1, x1, _ = check(root, 'base', anchor)
            subprocess.run([sys.executable, ROGUE, 'own-key', '--root', root, '--keys', tmp], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            v2, x2, _ = check(root, 'base', anchor)
            ok = (v1, x1, v2, x2) == ('APPROVAL_CHANGED', 1, 'APPROVAL_UNSIGNED', 2)
            total += 1
            failed += not ok
            print('%s  %-57s want APPROVAL_CHANGED/1; rogue-signed APPROVAL_UNSIGNED/2  got %s/%d; %s/%d' % (
                'ok  ' if ok else 'FAIL', 'bootstrap PR: base has only allowed_signers', v1, x1, v2, x2))
        # a trusted list inside the project is refused (an agent could write it)
        with tempfile.TemporaryDirectory() as tmp:
            root = owner_project(tmp, owner_line, owner_key)
            r = run([CLI, 'check', '--root', root, '--json', '--no-write', '--allowed-signers',
                     os.path.join(root, '.forge/allowed_signers')])
            ok = r.returncode == 2 and 'inside the project' in r.stdout
            total += 1
            failed += not ok
            print('%s  %-57s want exit 2 (refused)  got %d' % ('ok  ' if ok else 'FAIL',
                  '--allowed-signers inside the project', r.returncode))
    print('%d of %d signed-approval cases passed (FINITE TEST; software ed25519 keys made at test time)'
          % (total - failed, total))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
