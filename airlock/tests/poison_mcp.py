# poison_mcp.py - stdio MCP mock for the F6 regression test (no real service).
# Tool echo(text): "poison" first prints lines that are valid JSON but not a response
# ([], 42, NaN, an object with a list id); "hang" sleeps `seconds`, then appends "hang-done"
# to the ledger file given as argv[1] and answers. FINITE TEST fixture, demo quality.
import json
import sys
import time

LEDGER = sys.argv[1]
for line in sys.stdin:
    m = json.loads(line)
    mid = m.get("id")
    if mid is None:
        continue
    if m["method"] == "initialize":
        r = {"protocolVersion": "2025-06-18", "capabilities": {}, "serverInfo": {"name": "poison", "version": "0"}}
    elif m["method"] == "tools/list":
        r = {"tools": [{"name": "echo", "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}}}]}
    else:
        a = m["params"].get("arguments") or {}
        if a.get("text") == "poison":
            sys.stdout.write('[]\n42\nNaN\n{"id": [1], "result": {}}\n')
        if a.get("text") == "hang":
            time.sleep(int(a.get("seconds", 3)))
            with open(LEDGER, "a") as f:
                f.write("hang-done\n")
        r = {"content": [{"type": "text", "text": "echo " + str(a.get("text"))}]}
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": mid, "result": r}) + "\n")
    sys.stdout.flush()
