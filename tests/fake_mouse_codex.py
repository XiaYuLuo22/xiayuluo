"""Controlled visual CLI fixture. Does not connect to a model or operate the OS."""
import json
import os
from pathlib import Path
import sys
import time

sys.stdin.reconfigure(encoding='utf-8')
sys.stdout.reconfigure(encoding='utf-8')
if sys.argv[-2:] == ['login', 'status']:
    print('Logged in using ChatGPT')
    sys.exit(0)
assert '--ignore-user-config' in sys.argv and '--strict-config' in sys.argv
assert sys.argv[sys.argv.index('--sandbox') + 1] == 'read-only'
assert 'forced_login_method="chatgpt"' in sys.argv
assert not any(os.environ.get(key) for key in ('OPENAI_API_KEY', 'CODEX_API_KEY', 'ASSISTANT_MODEL_KEY'))
workspace = Path(sys.argv[sys.argv.index('--cd') + 1])
image = Path(sys.argv[sys.argv.index('--image') + 1])
schema = Path(sys.argv[sys.argv.index('--output-schema') + 1])
assert image.parent == schema.parent == workspace
assert image.read_bytes().startswith(b'\x89PNG')
assert json.loads(schema.read_text(encoding='utf-8'))['additionalProperties'] is False
assert sorted(item.name for item in workspace.iterdir()) == ['plan-schema.json', 'screen.png']
prompt = sys.stdin.read()
assert '点击' in prompt
print(json.dumps({'type': 'turn.started'}), flush=True)
if '慢速' in prompt:
    time.sleep(30)
plan = {'action': 'click', 'x': 30, 'y': 40, 'scroll': 0,
        'message': '定位一个按钮。', 'width': 200, 'height': 120}
print(json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': json.dumps(plan)}}), flush=True)
