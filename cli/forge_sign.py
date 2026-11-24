#!/usr/bin/env python3
"""forge sign: signed approvals with an SSH key that only a person can use (a FIDO2 security key, for example a
YubiKey, or a macOS Secure Enclave key held by an ssh-agent).

Usage:
  forge_sign.py call   [--repo DIR] [--key KEY] [--once]   (airlock: sign a held tool call; a person's act)
  forge_sign.py verify --signers FILE --namespace NS --sig SIG MESSAGE_FILE   (exit 0 only if valid)

`forge_check.py approve --hardware` signs the canonical content of .forge/approved.json with
`ssh-keygen -Y sign -n forge-approval`. `forge_check.py check` verifies it with `ssh-keygen -Y verify` against an
allowed-signers list that is NOT taken from the working tree (see `trust_anchor` in forge_check.py).

The airlock (`require_human_approval: hardware` in forge.yaml) holds a tool call until a signature over the
exact call (tool, arguments hash, step id, run id, call id) verifies, namespace `forge-call-approval`.
`forge_sign.py call` shows each held call on the host, asks for `yes`, signs it and hands the signature to the
airlock. The airlock checks it itself: a wrong, old or other-call signature is refused.

Key file (--key, else FORGE_APPROVAL_KEY): what `ssh-keygen -Y sign -f` takes:
  - a FIDO2 key handle (`ssh-keygen -t ed25519-sk`): the key blinks; touch it;
  - the PUBLIC key file (.pub) of a key held in an ssh-agent (SSH_AUTH_SOCK), for example a Secure Enclave key.

LIMIT (NATIVE CHECK): the signature check is OpenSSH's (`ssh-keygen -Y verify`), not proved. A valid signature
shows that the holder of a listed key signed these bytes. It shows a touch only for `sk-` keys (OpenSSH checks
the user-presence flag unless the signer line says `no-touch-required`); for a Secure Enclave or software key it
shows nothing about how the key is kept. Restrict to `sk-` keys with "signer_key_types" in forge.json.
Python 3.8+, standard library only.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unicodedata

APPROVAL_NAMESPACE = 'forge-approval'
CALL_NAMESPACE = 'forge-call-approval'
SK_TYPES = ('sk-ssh-ed25519@openssh.com', 'sk-ecdsa-sha2-nistp256@openssh.com')
KEY_TYPES = SK_TYPES + ('ssh-ed25519', 'ecdsa-sha2-nistp256', 'ecdsa-sha2-nistp384', 'ecdsa-sha2-nistp521',
                        'rsa-sha2-256', 'rsa-sha2-512', 'ssh-rsa')
TIMEOUT_S = 120  # a person has to touch the key


class SignError(Exception):
    pass


def canonical(value):
    """The bytes that are signed: sorted keys, no spaces, ASCII only, one final newline."""
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True) + '\n').encode('ascii')


def ssh_keygen():
    """FORGE_SSH_KEYGEN (for example Homebrew's OpenSSH on macOS, whose ssh-keygen has FIDO2 support), else PATH."""
    import shutil
    path = os.environ.get('FORGE_SSH_KEYGEN') or shutil.which('ssh-keygen')
    if not path:
        raise SignError('ssh-keygen (OpenSSH 8.2 or newer) is not on PATH')
    return path


def sign(key, message, namespace):
    """Signature (armored text) of message by the key file; the person may have to touch the key."""
    if not key:
        raise SignError('no signing key: pass --key KEY or set FORGE_APPROVAL_KEY (a FIDO2 key handle from '
                        '`ssh-keygen -t ed25519-sk`, or the .pub file of a key in your ssh-agent)')
    if not os.path.isfile(key):
        raise SignError('signing key %s does not exist' % key)
    sys.stderr.write('forge: signing with %s (namespace %s). If it is a security key, touch it now.\n'
                     % (os.path.basename(key), namespace))
    sys.stderr.flush()
    try:
        r = subprocess.run([ssh_keygen(), '-Y', 'sign', '-q', '-f', key, '-n', namespace], input=message,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        raise SignError('ssh-keygen -Y sign timed out after %d s (no touch?)' % TIMEOUT_S) from None
    sig = r.stdout.decode('ascii', 'replace')
    if r.returncode != 0 or '-----BEGIN SSH SIGNATURE-----' not in sig:
        raise SignError('ssh-keygen -Y sign failed (exit %d): %s' % (r.returncode, r.stderr.decode(errors='replace')
                                                                     .strip()[-400:]))
    return sig


def signer_lines(text):
    """(principals, options, key type, key blob) for each line of an allowed-signers file."""
    out = []
    for raw in (text or '').splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        parts = line.split()
        # principals [options] keytype base64 [comment]
        idx = next((i for i, p in enumerate(parts) if p in KEY_TYPES), None)
        if idx is None or idx + 1 >= len(parts) or idx < 1:
            continue
        out.append((parts[0], parts[1:idx], parts[idx], parts[idx + 1]))
    return out


def verify(signers_text, message, sig_text, namespace):
    """Checks sig_text over message against the allowed signers. Returns (ok, detail); detail names the principal
    and key type on success. Fails closed on every error."""
    if not signers_text or not signer_lines(signers_text):
        return False, 'the allowed-signers list is empty'
    if not sig_text or '-----BEGIN SSH SIGNATURE-----' not in sig_text:
        return False, 'no SSH signature'
    with tempfile.TemporaryDirectory(prefix='forge-sig-') as tmp:
        signers = os.path.join(tmp, 'allowed_signers')
        sig = os.path.join(tmp, 'msg.sig')
        with open(signers, 'w', encoding='utf-8') as f:
            f.write(signers_text if signers_text.endswith('\n') else signers_text + '\n')
        with open(sig, 'w', encoding='ascii', errors='replace') as f:
            f.write(sig_text)
        try:
            fp = subprocess.run([ssh_keygen(), '-Y', 'find-principals', '-s', sig, '-f', signers],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        except (subprocess.TimeoutExpired, SignError) as e:
            return False, str(e)
        principals = [p.strip() for p in fp.stdout.decode(errors='replace').splitlines() if p.strip()]
        if fp.returncode != 0 or not principals:
            return False, 'the signing key is not in the allowed-signers list'
        why = ''
        for principal in principals:
            r = subprocess.run([ssh_keygen(), '-Y', 'verify', '-f', signers, '-I', principal, '-n', namespace,
                                '-s', sig], input=message, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               timeout=30)
            text = (r.stdout + r.stderr).decode(errors='replace')
            if r.returncode == 0 and 'Good "%s" signature' % namespace in text:
                ktype = key_type_of(sig_text)
                return True, 'signed by %s (%s)' % (principal, ktype or 'unknown key type')
            why = text.strip()[-300:]
        return False, 'ssh-keygen -Y verify refused the signature: ' + why


def key_type_of(sig_text):
    """The key type inside an SSH signature (the public key it carries)."""
    import base64
    import struct
    try:
        body = ''.join(l for l in sig_text.strip().splitlines() if not l.startswith('-----'))
        blob = base64.b64decode(body)
        if blob[:6] != b'SSHSIG':
            return None
        off = 10  # magic + version
        (n,) = struct.unpack('>I', blob[off:off + 4])
        pub = blob[off + 4:off + 4 + n]
        (m,) = struct.unpack('>I', pub[:4])
        return pub[4:4 + m].decode('ascii')
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------- airlock calls (host side)
def call_request_bytes(req):
    """The exact bytes the airlock verifies for a held call: the canonical request without its display fields."""
    keep = {k: req[k] for k in ('v', 'run', 'call', 'step', 'tool', 'args_sha256')}
    return canonical(keep)


def airlock_projects(repo):
    """Running compose projects of the airlock for this repository (forge_airlock.py names them
    <prefix>-<repo key>-<run>)."""
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, os.path.join(here, '..', 'airlock'))
    import forge_airlock  # noqa: E402
    prefix = '%s-%s-' % (forge_airlock.PREFIX, forge_airlock.repo_key(forge_airlock.git_root(repo)))
    r = subprocess.run(['docker', 'ps', '--filter', 'label=com.docker.compose.service=airlock', '--format',
                        '{{.Label "com.docker.compose.project"}}'], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       universal_newlines=True)
    if r.returncode != 0:
        raise SignError('docker ps failed: ' + r.stderr.strip()[-300:])
    return sorted({p for p in r.stdout.split() if p.startswith(prefix)})


def desk(project, *args, data=None):
    r = subprocess.run(['docker', 'compose', '-p', project, 'exec', '-T', 'airlock', 'python3', '-I',
                        '/forge/airlock/human_approval.py'] + list(args), input=data, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE)
    return r.returncode, r.stdout.decode(errors='replace'), r.stderr.decode(errors='replace')


def shown_json(value):
    """JSON for a person to read: readable non-ASCII text, but every control, format (bidi override, zero width),
    separator, private-use and surrogate character as a \\uXXXX escape. Such characters come from the agent
    and can hide or reorder text in a terminal. The result is still JSON of the same value."""
    text = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False)
    hits = [0]

    def one(ch):
        if ch != '\n' and unicodedata.category(ch) in ('Cc', 'Cf', 'Zl', 'Zp', 'Co', 'Cs'):
            hits[0] += 1
            return json.dumps(ch)[1:-1]
        return ch
    return ''.join(one(ch) for ch in text), hits[0]


def show_call(req):
    args = req.get('arguments')
    ok = hashlib.sha256(json.dumps(args, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                                   allow_nan=False).encode()).hexdigest() == req.get('args_sha256')
    shown, hidden = shown_json(args)
    return '\n'.join([
        '=== held tool call %s (run %s) ===' % (req.get('call'), req.get('run')),
        'tool:      %s' % req.get('tool'),
        'step id:   %s' % req.get('step'),
        'arguments (exactly what the upstream receives):',
        shown,
    ] + (['note: %d control or invisible character(s) in the arguments are shown as \\u escapes' % hidden]
         if hidden else []) + [
        'arguments sha256: %s (%s)' % (req.get('args_sha256'), 'matches the arguments above' if ok
                                       else 'DOES NOT MATCH the arguments above: do not sign'),
    ]), ok


def cmd_call(a):
    key = a.key or os.environ.get('FORGE_APPROVAL_KEY')
    projects = airlock_projects(a.repo)
    if not projects:
        print('forge: no running airlock for this repository (start it with ./forge-airlock up -- ...)')
        return 1
    signed = 0
    for project in projects:
        code, out, err = desk(project, 'list')
        if code != 0:
            print('forge: %s: cannot list held calls: %s' % (project, err.strip()[-300:]))
            continue
        for req in json.loads(out or '[]'):
            text, ok = show_call(req)
            print(text)
            if not ok:
                continue
            if not (sys.stdin.isatty() and sys.stdout.isatty()):
                print('forge: signing a held call needs a person at a terminal.')
                return 2
            if input('Allow exactly this call? Type yes: ').strip() != 'yes':
                print('not signed (the call stays held until its timeout, then it is refused)')
                continue
            sig = sign(key, call_request_bytes(req), CALL_NAMESPACE)
            code, out, err = desk(project, 'submit', req['call'], data=sig.encode('ascii'))
            print((out or err).strip())
            signed += code == 0
            if a.once:
                return 0 if signed else 1
    if not signed:
        print('forge: no call was signed')
    return 0 if signed else 1


def cmd_verify(a):
    with open(a.signers, encoding='utf-8') as f:
        signers = f.read()
    with open(a.sig, encoding='ascii', errors='replace') as f:
        sig = f.read()
    with open(a.message, 'rb') as f:
        msg = f.read()
    ok, detail = verify(signers, msg, sig, a.namespace)
    print(('GOOD ' if ok else 'BAD ') + detail)
    return 0 if ok else 1


def main(argv=None):
    p = argparse.ArgumentParser(prog='forge_sign.py', description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='sub', required=True)
    c = sub.add_parser('call')
    c.add_argument('--repo', default=os.getcwd())
    c.add_argument('--key')
    c.add_argument('--once', action='store_true', help='stop after the first signed call')
    v = sub.add_parser('verify')
    v.add_argument('--signers', required=True)
    v.add_argument('--namespace', required=True)
    v.add_argument('--sig', required=True)
    v.add_argument('message')
    a = p.parse_args(argv)
    try:
        return {'call': cmd_call, 'verify': cmd_verify}[a.sub](a)
    except SignError as e:
        print('forge: ' + str(e), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
