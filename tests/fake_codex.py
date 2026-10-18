"""Controlled CLI fixture; never used by production code."""
import json
import sys
import time

prompt = sys.stdin.read()
print(json.dumps({"type": "turn.started"}), flush=True)
if prompt == "slow":
    time.sleep(30)
if prompt == "fail":
    print(json.dumps({"type": "turn.failed", "error": {"message": "fixture failure"}}), flush=True)
    sys.exit(1)
print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "fixture: " + prompt}}), flush=True)
