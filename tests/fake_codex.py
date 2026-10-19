"""Controlled CLI fixture; never used by production code."""
import json
from pathlib import Path
import sys
import time

sys.stdin.reconfigure(encoding='utf-8')
sys.stdout.reconfigure(encoding='utf-8')
if sys.argv[-2:] == ['login', 'status']:
    print('Logged in using an API key - REDACTED_TEST_KEY' if '--fixture-api-login' in sys.argv else 'Logged in using ChatGPT')
    sys.exit(0)
prompt = sys.stdin.read()
dialogue = '--ignore-user-config' in sys.argv
if dialogue:
    messages = json.loads(prompt.rsplit('\n', 1)[-1])
    prompt = messages[-1]['content']
print(json.dumps({"type": "turn.started"}), flush=True)
if prompt == "slow":
    time.sleep(30)
if prompt == "fail":
    print(json.dumps({"type": "turn.failed", "error": {"message": "fixture failure"}}), flush=True)
    sys.exit(1)
if prompt == "fixture-write-workspace":
    assert not dialogue
    assert sys.argv[sys.argv.index('--sandbox') + 1] == 'workspace-write'
    workspace = Path(sys.argv[sys.argv.index('--cd') + 1])
    (workspace / 'phone-result.txt').write_text('approved phone instruction', encoding='utf-8')
print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": ("chat fixture: " if dialogue else "fixture: ") + prompt}}), flush=True)
