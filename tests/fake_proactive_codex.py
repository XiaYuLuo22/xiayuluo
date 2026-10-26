"""Strict subprocess fixture, never an actual model/account."""
import json
import os
from pathlib import Path
import sys
import time


args = sys.argv[1:]
if args == ['login', 'status']:
    print('Logged in using ChatGPT')
    raise SystemExit(0)

assert args[0] == 'exec' and args[-1] == '-'
for flag in ('--json', '--ephemeral', '--ignore-user-config', '--strict-config', '--skip-git-repo-check'):
    assert flag in args
assert args[args.index('--sandbox') + 1] == 'read-only'
assert 'approval_policy="never"' in args and 'forced_login_method="chatgpt"' in args
assert 'mcp_servers={}' in args and 'web_search="disabled"' in args
for flag in ('shell_tool', 'apps', 'plugins', 'browser_use', 'computer_use', 'view_image',
             'multi_agent', 'code_mode', 'hooks', 'js_repl'):
    assert ['--disable', flag] in [args[index:index + 2] for index in range(len(args) - 1)]
for key in ('OPENAI_API_KEY', 'CODEX_API_KEY', 'ASSISTANT_MODEL_KEY'):
    assert key not in os.environ
workspace = Path(args[args.index('--cd') + 1])
assert workspace.name.startswith('xiayuluo-observation-')
image = Path(args[args.index('--image') + 1])
assert image.parent == workspace and image.is_file()
schema = json.loads(Path(args[args.index('--output-schema') + 1]).read_text(encoding='utf-8'))
assert set(schema['properties']) == {'kind', 'message', 'category', 'activity'}
assert schema['additionalProperties'] is False
instructions = next(value for value in args if value.startswith('developer_instructions='))
assert '只读主动关怀观察器' in instructions and '不调用工具' in instructions
assert '单次鼠标定位规划器' not in instructions
assert '前台窗口图尺寸' in sys.stdin.read()
print(json.dumps({'type': 'turn.started'}), flush=True)
if os.environ.get('PROACTIVE_FIXTURE_SLOW'):
    time.sleep(30)
print(json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message', 'text':
    json.dumps({'kind': 'question', 'message': '需要我帮你梳理下一步吗？'}, ensure_ascii=False)}}), flush=True)
