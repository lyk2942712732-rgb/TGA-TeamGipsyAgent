from __future__ import annotations

import json
import sys
import time


mode = sys.argv[1]
for line in sys.stdin:
    message = json.loads(line)
    if mode == "timeout":
        time.sleep(5)
    elif mode == "invalid-json":
        print("this is not json", flush=True)
    elif mode == "rpc-error":
        print(json.dumps({"jsonrpc": "2.0", "id": message.get("id"), "error": {"code": -32000, "message": "initialize failed"}}), flush=True)
    elif mode == "exit":
        raise SystemExit(3)
