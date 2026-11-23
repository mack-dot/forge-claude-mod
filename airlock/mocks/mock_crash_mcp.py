# mock_crash_mcp.py - MOCK stdio upstream for the crash and timeout cases (no real service).
# Tool crashy_job(mode): "ok" answers at once; "crash" exits the process in the middle of the
# call (no answer); "hang" answers after `seconds`; "sleep" too (used while the AIRLOCK is
# killed mid-call). Every executed call is appended to a ledger. NATIVE CHECK, demo quality.
import json
import os
import sys
import time

LEDGER = os.environ.get("CRASH_LEDGER", "/tmp/crash-ledger.jsonl")
TOOL = {"name": "crashy_job", "description": "A job that may crash or hang",
        "inputSchema": {"type": "object", "properties": {"mode": {"type": "string"}, "seconds": {"type": "integer"}},
                        "required": ["mode"]}}


def send(msg):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


for line in sys.stdin:
    try:
        m = json.loads(line)
    except ValueError:
        continue
    mid, method, p = m.get("id"), m.get("method"), m.get("params") or {}
    if mid is None:
        continue
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": mid, "result": {"protocolVersion": p.get("protocolVersion"),
              "capabilities": {"tools": {}}, "serverInfo": {"name": "mock-crash", "version": "0"}}})
    elif method == "tools/list":
        send({"jsonrpc": "2.0", "id": mid, "result": {"tools": [TOOL]}})
    elif method == "tools/call":
        a = p.get("arguments") or {}
        mode = a.get("mode")
        with open(LEDGER, "a") as f:
            f.write(json.dumps({"mode": mode, "ts": time.time()}) + "\n")
        if mode == "crash":
            os._exit(9)  # dies mid-call: the airlock never gets an answer
        if mode in ("hang", "sleep"):
            time.sleep(int(a.get("seconds", 5)))
        send({"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": f"done {mode}"}],
                                                      "isError": False}})
    else:
        send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "no"}})
