#!/usr/bin/env python3
# egress_proxy.py - the agent's ONLY way out, apart from the airlock: an HTTP CONNECT proxy
# that opens a TCP tunnel to exactly the host:port pairs in forge.yaml `agent.egress_allow`
# (for example the model API that `claude` needs). Every other request gets 403.
# It never sees plaintext (TLS goes end to end) and holds no credential.
# Usage: egress_proxy.py <port> [<host:port> ...] [--private <host:port> ...]
# A --private target (the model provider of forge.yaml `llm`) is opened only for a CONNECT
# that carries "Proxy-Authorization: Bearer $FORGE_EGRESS_TOKEN". Only the airlock holds that
# per-run token; the agent does not, so the agent cannot reach a private target itself.
# Label: NATIVE CHECK (hand-written, not proved). An allowed host can still carry data out:
# the allowlist limits WHERE the agent can talk, not WHAT it says there.
import hmac
import os
import select
import socket
import sys
import threading

ALLOW = set()
PRIVATE = set()
TOKEN = os.environ.get("FORGE_EGRESS_TOKEN", "")


def pipe(a, b):
    socks = [a, b]
    try:
        while True:
            r, _, x = select.select(socks, [], socks, 300)
            if x or not r:
                return
            for s in r:
                data = s.recv(65536)
                if not data:
                    return
                (b if s is a else a).sendall(data)
    except OSError:
        return
    finally:
        for s in socks:
            try:
                s.close()
            except OSError:
                pass


def handle(c):
    try:
        c.settimeout(10)
        head = b""
        while b"\r\n\r\n" not in head and len(head) < 8192:
            chunk = c.recv(4096)
            if not chunk:
                return c.close()
            head += chunk
        line = head.split(b"\r\n", 1)[0].decode("latin-1")
        parts = line.split()
        target = parts[1] if len(parts) == 3 and parts[0] == "CONNECT" else None
        auth = ""
        for h in head.split(b"\r\n")[1:]:
            k, _, v = h.decode("latin-1").partition(":")
            if k.strip().lower() == "proxy-authorization":
                auth = v.strip()
        private_ok = (target is not None and target.lower() in PRIVATE and len(TOKEN) >= 32
                      and hmac.compare_digest(auth.encode(), ("Bearer " + TOKEN).encode()))
        if target is None or (target.lower() not in ALLOW and not private_ok):
            print(f"[egress] refuse {line[:120]!r}", flush=True)
            c.sendall(b"HTTP/1.1 403 Forbidden\r\ncontent-length: 0\r\n\r\n")
            return c.close()
        host, port = target.rsplit(":", 1)
        up = socket.create_connection((host, int(port)), timeout=10)
        c.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
        c.settimeout(None)
        up.settimeout(None)
        print(f"[egress] tunnel {target}", flush=True)
        pipe(c, up)
    except OSError:
        try:
            c.sendall(b"HTTP/1.1 502 Bad Gateway\r\ncontent-length: 0\r\n\r\n")
            c.close()
        except OSError:
            pass


def main():
    port = int(sys.argv[1])
    args, private = sys.argv[2:], False
    for x in args:
        if x == "--private":
            private = True
            continue
        (PRIVATE if private else ALLOW).add(x.lower())
        private = False
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", port))
    s.listen(128)
    print(f"[egress] up on :{port}; allow {sorted(ALLOW)}; airlock-only {sorted(PRIVATE)}", flush=True)
    while True:
        c, _ = s.accept()
        threading.Thread(target=handle, args=(c,), daemon=True).start()


if __name__ == "__main__":
    main()
