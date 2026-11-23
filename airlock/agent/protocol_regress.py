# protocol_regress.py - Docker regression of the protocol red-team findings, through the REAL
# airlock (policy proof/policies/concurrency.yaml). "pre": before the canary (window open);
# "post": after the canary passed, with FORGE_CANARY_TOKEN unset (as the agent's command).
# Exit 0 only if every expectation holds. Label: FINITE TEST.
import json
import os
import socket
import sys

H, P = "airlock", 8700
FAILS = []


def expect(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f" {detail}"), flush=True)
    if not cond:
        FAILS.append(label)


def raw(body=b"", head=None, path="/mcp", method="POST"):
    s = socket.create_connection((H, P), timeout=15)
    head = f"content-length: {len(body)}\r\n" if head is None else head
    try:
        s.sendall(f"{method} {path} HTTP/1.1\r\nhost: airlock\r\ncontent-type: application/json\r\n{head}\r\n".encode()
                  + body)
        s.shutdown(socket.SHUT_WR)
    except OSError:
        pass
    parts = []
    try:
        while True:
            c = s.recv(1 << 16)
            if not c:
                break
            parts.append(c)
    except OSError:
        pass
    s.close()
    out = b"".join(parts)
    return out.split(b"\r\n", 1)[0][9:12].decode(errors="replace"), out.partition(b"\r\n\r\n")[2]


def code_of(body):
    try:
        e = json.loads(body).get("error") or {}
        return (e.get("data") or {}).get("forge_code") or e.get("message")
    except ValueError:
        return None


def call(name, args):
    st, b = raw(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                            "params": {"name": name, "arguments": args}}).encode())
    return st, b, code_of(b)


if sys.argv[1] == "pre":
    st, _ = raw(method="GET", path="/forge/canary/plan", head="")
    expect("F7: canary plan without the token -> 403 (window open)", st == "403", st)
    st, _ = raw(b'{"result":"PASS","breaches":[]}', path="/forge/canary/report")
    expect("F7: forged PASS report without the token -> 403", st == "403", st)
else:
    expect("F7: the agent's command does not hold the canary token", not os.environ.get("FORGE_CANARY_TOKEN"))
    st, _ = raw(method="GET", path="/forge/canary/plan", head="")
    expect("F7: canary plan after PASS without the token -> 403", st == "403", st)
    st, _ = raw(b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}', head="content-length: -1\r\n")
    expect("F1: content-length -1 -> 4xx", st in ("400", "411", "413"), st)
    st, _ = raw(b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}', head="")
    expect("F1: missing content-length -> 411", st == "411", st)
    st, _ = raw(b"{}", head=f"content-length: {2 << 20}\r\n")
    expect("F1: declared 2 MiB body -> 413", st == "413", st)
    st, b = raw(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                            "params": {"name": "Z" * 200000, "arguments": {}}}).encode())
    expect("F2: a 200 kB tool name gives a short reply", st == "200" and len(b) < 4000, (st, len(b)))
    st, _ = raw(b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"get_status","arguments":{"x":NaN}}}')
    expect("F5: NaN in a request -> 400", st == "400", st)
    _, _, c = call("charge", {"amount": 1, "invoice_id": "RT-1", "Amount": 5000})
    expect("F3: undeclared argument (Amount) -> FORGE_ARG_REFUSED", c == "FORGE_ARG_REFUSED", c)
    _, _, c = call("get_status", {"service": "s", "extra": 1})
    expect("F3: undeclared argument on an allowed tool -> FORGE_ARG_REFUSED", c == "FORGE_ARG_REFUSED", c)
    for v in (1, [1], True, {"id": 1}):
        _, _, c = call("charge", {"amount": 1, "invoice_id": v})
        expect(f"F4: invoice_id {v!r} -> FORGE_ARG_REFUSED", c == "FORGE_ARG_REFUSED", c)
    st, b, c = call("get_status", {"service": "after"})
    expect("allowed call still works", st == "200" and b'"result"' in b, (st, c))
print(f"[protocol_regress] {sys.argv[1]}: {len(FAILS)} failed", flush=True)
sys.exit(1 if FAILS else 0)
