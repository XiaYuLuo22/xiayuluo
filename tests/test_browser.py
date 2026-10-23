"""Optional full-browser acceptance tests with controlled model and desktop fixtures.

Install .[test,browser] and set ASSISTANT_BROWSER to your Chromium executable,
or run `python -m playwright install chromium` for the Playwright-managed browser.
"""
import asyncio
import json
import os
from pathlib import Path
import shutil
import socket
import sys
from threading import Thread
import time

import httpx
import pytest
import uvicorn

from assistant.config import Settings
from assistant.server import create_app
from test_tools import FakeDesktop

playwright_api = pytest.importorskip("playwright.sync_api")

MARKDOWN_SAMPLE = '''## 功能检查

- **流式**内容
- 普通条目

```python
print("<script>这只是代码</script>")
```

| 项目 | 状态 | 说明 | 版本 | 来源 | 备注 |
| --- | --- | --- | --- | --- | --- |
| Markdown | 就绪 | 浏览器检查 | 固定 | 官方 | 本地 |

[正常链接](https://github.com/markedjs/marked)
[危险链接](javascript:alert%281%29)
![远程图片](https://blocked.invalid/track.png)

<script>window.markdownExploit = true</script>
<img src="https://blocked.invalid/other.png" onerror="window.markdownExploit = true">
<iframe src="https://blocked.invalid/frame"></iframe>
<form><input name="token"><button onclick="window.markdownExploit = true">操作</button></form>
<p class="sr-only" style="display:none" onclick="window.markdownExploit = true">样式属性不得沿用</p>
'''


class SlowModelStream(httpx.AsyncByteStream):
    def __init__(self, parts=None):
        self.parts = parts or ['你好', '，这是流式', '回复。']

    async def __aiter__(self):
        for content in self.parts:
            payload = {'choices': [{'delta': {'content': content}, 'finish_reason': None}]}
            yield ('data: ' + json.dumps(payload, ensure_ascii=False) + '\n\n').encode()
            await asyncio.sleep(.5)
        yield b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'


@pytest.fixture
def browser_app(tmp_path):
    model_requests = []
    def handler(request):
        model_requests.append(request)
        prompt = json.loads(request.content)['messages'][-1]['content']
        parts = ['## 功能检查\n\n', MARKDOWN_SAMPLE[len('## 功能检查\n\n'):]] if '展示排版测试' in prompt else None
        return httpx.Response(200, stream=SlowModelStream(parts), headers={'Content-Type': 'text/event-stream'})
    app = create_app(Settings(api_key='fixture-only', data_dir=str(tmp_path), codex_workspace=str(tmp_path), approval_mode='manual'),
                     transport=httpx.MockTransport(handler))
    app.state.desktop.enabled = True
    app.state.desktop.driver = FakeDesktop()
    app.state.codex.command_prefix = lambda: [sys.executable, str(Path(__file__).parent / 'fake_codex.py')]
    app.state.codex_chat.command_prefix = app.state.codex.command_prefix
    app.state.model_requests = model_requests
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
    thread = Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(.01)
    assert server.started, 'Test HTTP server failed to start'
    yield f'http://127.0.0.1:{port}', app
    server.should_exit = True
    thread.join(timeout=5)
    sock.close()


@pytest.fixture
def page(browser_app):
    url, _ = browser_app
    with playwright_api.sync_playwright() as playwright:
        executable = os.getenv('ASSISTANT_BROWSER') or shutil.which('chromium') or shutil.which('google-chrome')
        browser = playwright.chromium.launch(executable_path=executable, headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 960})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(url)
        playwright_api.expect(page.locator('#model-status')).to_contain_text('已配置')
        yield page
        assert not errors, errors
        browser.close()


def test_browser_streaming_memory_and_mobile_layout(page):
    page.locator('[data-view="memory"]').click()
    page.locator('#add-memory').click()
    page.locator('#memory-tier').select_option('permanent')
    page.locator('#memory-content').fill('喜欢简洁的中文回答')
    page.locator('#memory-form button[type="submit"]').click()
    playwright_api.expect(page.locator('#memory-list')).to_contain_text('喜欢简洁的中文回答')
    page.locator('[data-view="chat"]').click()
    page.locator('#prompt').fill('你好')
    page.locator('#send').click()
    body = page.locator('.message.assistant .message-body').last
    playwright_api.expect(body).to_have_text('你好')
    playwright_api.expect(body).to_have_text('你好，这是流式回复。')
    playwright_api.expect(page.locator('#stop')).to_be_hidden()
    playwright_api.expect(page.locator('#retrieval-note')).to_contain_text('1 条相关记忆')
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.set_viewport_size({'width': 390, 'height': 844})
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    playwright_api.expect(page.locator('#prompt')).to_be_visible()


def test_browser_shared_workspace_and_bound_confirmation(page, browser_app):
    _, app = browser_app
    workspace = app.state.codex.workspace_status()
    page.locator('[data-view="devices"]').click()
    playwright_api.expect(page.locator('#shared-workspace-name')).to_contain_text(workspace['name'])
    playwright_api.expect(page.locator('#shared-workspace-id')).to_contain_text(workspace['id'])
    playwright_api.expect(page.locator('#shared-workspace-policy')).to_contain_text('只读检查')
    page.locator('[data-view="chat"]').click()
    page.locator('#mode-toggle').click()
    page.locator('#prompt').fill('检查共享工作区')
    page.locator('#send').click()
    page.locator('.action-card button').click()
    playwright_api.expect(page.locator('#action-details')).to_contain_text(workspace['id'])
    playwright_api.expect(page.locator('#action-details')).to_contain_text('目录或权限改变后必须重新提交')


def test_browser_auto_mode_direct_chat_without_dialog(page, browser_app):
    _, app = browser_app
    dialogs = []
    page.on('dialog', lambda dialog: (dialogs.append(dialog.message), dialog.dismiss()))
    page.locator('[data-view="settings"]').click()
    page.locator('#approval-mode').select_option('auto')
    playwright_api.expect(page.locator('#automation-status')).to_contain_text('已授权默认同意')
    page.locator('#chat-provider').select_option('codex')
    playwright_api.expect(page.locator('#model-status')).to_contain_text('发送后直接调用')
    page.locator('[data-view="chat"]').click()
    page.locator('#prompt').fill('好久不见')
    page.locator('#send').click()
    playwright_api.expect(page.locator('.message.assistant .message-body').last).to_contain_text('chat fixture: 好久不见')
    playwright_api.expect(page.locator('#stop')).to_be_hidden()
    assert page.locator('.action-card').count() == 0
    playwright_api.expect(page.locator('#action-dialog')).not_to_be_visible()
    assert not dialogs
    playwright_api.expect(page.locator('#action-dialog')).not_to_be_visible()
    page.locator('[data-view="settings"]').click()
    page.reload()
    page.locator('[data-view="settings"]').click()
    playwright_api.expect(page.locator('#approval-mode')).to_have_value('auto')
    page.locator('#approval-mode').select_option('manual')
    playwright_api.expect(page.locator('#automation-status')).to_contain_text('当前逐次确认')


def test_browser_automatically_identifies_codex_login_on_open_without_model_request(page, browser_app):
    _, app = browser_app
    requests = []
    page.on('request', lambda request: requests.append(request.url))
    page.evaluate("localStorage.setItem('xiayuluo.chat-provider', 'codex')")
    page.reload()
    playwright_api.expect(page.locator('#model-status')).to_contain_text('ChatGPT 登录已识别')
    assert any(url.endswith('/api/codex/connect') for url in requests)
    assert not app.state.model_requests
    assert not app.state.memory.sessions()
    page.locator('[data-view="settings"]').click()
    playwright_api.expect(page.locator('#codex-connection')).to_contain_text('实际回复为准')
    # Recheck uses a fresh CLI probe, not the successful startup cache.
    app.state.codex_chat.command_prefix = lambda: [sys.executable, str(Path(__file__).parent / 'fake_codex.py'), '--fixture-api-login']
    with page.expect_response('**/api/codex/connect?refresh=true'):
        page.locator('#connect-codex').click()
    playwright_api.expect(page.locator('#codex-connection')).to_contain_text('只接受 ChatGPT 登录')
    playwright_api.expect(page.locator('#model-status')).to_contain_text('登录需处理')
    assert not app.state.model_requests and not app.state.memory.sessions()


def test_browser_legacy_mouse_task_still_requires_confirmation(page, browser_app):
    _, app = browser_app
    # Old pending tasks remain readable, without restoring the removed screenshot page.
    session = page.evaluate("post('/api/sessions')")['id']
    action = {'id': 'legacy-click', 'session_id': session, 'tool': 'desktop_click',
              'title': '桌面点击', 'arguments': {'x': 30, 'y': 40, 'button': 'left', 'screenshot_id': 'legacy-shot'}}
    page.route('**/api/actions', lambda route: route.fulfill(json=[action]))
    page.route('**/api/actions/legacy-click/approve', lambda route: route.fulfill(
        content_type='text/event-stream', body='data: {"type":"text","text":"旧请求已处理"}\n\ndata: {"type":"done"}\n\n'))
    page.locator('[data-view="tasks"]').click()
    page.locator('#pending-tasks button').click()
    playwright_api.expect(page.locator('#action-dialog')).to_be_visible()
    playwright_api.expect(page.locator('#action-details')).to_contain_text('legacy-shot')
    playwright_api.expect(page.locator('#action-details')).to_contain_text('(30, 40)')
    assert app.state.desktop.driver.clicks == []
    page.locator('#approve-action').click()
    playwright_api.expect(page.locator('.message.assistant .message-body').last).to_contain_text('旧请求已处理')
    assert app.state.desktop.driver.clicks == []


def stub_mouse_control(page, *, enabled=False, available=True):
    """Test UI consent against a controlled server, without taking real screenshots."""
    control = {'enabled': enabled, 'available': available, 'reason': '', 'fail': False}
    calls = []

    def status_route(route):
        response = route.fetch()
        status = response.json()
        status['mouse_control'] = {key: value for key, value in control.items() if key != 'fail'}
        route.fulfill(response=response, json=status)

    def control_route(route):
        body = route.request.post_data_json
        calls.append(body)
        if control['fail']:
            return route.fulfill(status=503, json={'detail': '测试服务拒绝切换'})
        control['enabled'] = body['enabled']
        route.fulfill(json={key: value for key, value in control.items() if key != 'fail'})

    page.route('**/api/status*', status_route)
    page.route('**/api/mouse-control', control_route)
    page.reload()
    playwright_api.expect(page.locator('#mouse-control')).to_have_attribute('aria-pressed', str(enabled).lower())
    playwright_api.expect(page.locator('#mouse-control-note')).not_to_contain_text('正在读取')
    return control, calls


def stub_chat(page):
    requests = []

    def chat_route(route):
        requests.append(route.request.post_data_json)
        route.fulfill(content_type='text/event-stream', body='data: {"type":"text","text":"已收到指令"}\n\ndata: {"type":"done"}\n\n')

    page.route('**/api/chat', chat_route)
    return requests


def test_browser_mouse_default_off_no_separate_screen_and_narrow_layout(page):
    _, calls = stub_mouse_control(page)
    requests = stub_chat(page)
    screenshots = []
    page.on('request', lambda request: screenshots.append(request.url) if '/api/desktop/' in request.url else None)
    switch = page.locator('#mouse-control')
    playwright_api.expect(switch).to_have_text('鼠标控制：关')
    assert page.locator('[data-view="desktop"], [data-open="desktop"], #view-desktop, #attach-screen, #capture-screen').count() == 0
    page.locator('#prompt').fill('你好，今天怎么样')
    with page.expect_response('**/api/chat'):
        page.locator('#send').click()
    playwright_api.expect(page.locator('#stop')).to_be_hidden()
    assert requests[-1]['mouse_control'] is False
    assert 'screenshot_id' not in requests[-1]
    assert not calls and not screenshots
    page.set_viewport_size({'width': 390, 'height': 844})
    playwright_api.expect(switch).to_be_visible()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    enable_developer(page)
    playwright_api.expect(switch).to_be_visible()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')


def test_browser_mouse_toggle_failure_uses_real_status(page):
    control, calls = stub_mouse_control(page)
    control['fail'] = True
    page.locator('#mouse-control').click()
    playwright_api.expect(page.locator('#toast')).to_contain_text('设置失败')
    playwright_api.expect(page.locator('#mouse-control')).to_have_attribute('aria-pressed', 'false')
    control['fail'] = False
    page.locator('#mouse-control').click()
    playwright_api.expect(page.locator('#mouse-control')).to_have_attribute('aria-pressed', 'true')
    control['fail'] = True
    page.locator('#mouse-control').click()
    playwright_api.expect(page.locator('#mouse-control-note')).to_contain_text('仅明确的鼠标指令')
    playwright_api.expect(page.locator('#mouse-control')).to_have_attribute('aria-pressed', 'true')
    assert calls == [{'enabled': True}, {'enabled': True}, {'enabled': False}]


def test_browser_mouse_unavailable_cannot_enable(page):
    control, calls = stub_mouse_control(page, available=False)
    control['reason'] = '测试桌面驱动不可用'
    page.reload()
    playwright_api.expect(page.locator('#mouse-control')).to_be_disabled()
    playwright_api.expect(page.locator('#mouse-control-note')).to_contain_text('测试桌面驱动不可用')
    assert not calls


def test_browser_mouse_on_chat_flag_but_not_programming(page):
    _, calls = stub_mouse_control(page)
    requests = stub_chat(page)
    dialogs = []
    page.on('dialog', lambda dialog: (dialogs.append(dialog.message), dialog.dismiss()))
    page.locator('#mouse-control').click()
    playwright_api.expect(page.locator('#mouse-control')).to_have_text('鼠标控制：开')
    playwright_api.expect(page.locator('#mouse-control-note')).to_contain_text('不是持续录屏')
    page.locator('#prompt').fill('点击屏幕右上角的关闭按钮')
    with page.expect_response('**/api/chat'):
        page.locator('#send').click()
    playwright_api.expect(page.locator('#stop')).to_be_hidden()
    assert requests[-1]['mouse_control'] is True
    assert 'screenshot_id' not in requests[-1]
    page.locator('[data-view="settings"]').click()
    page.locator('#chat-provider').select_option('codex')
    page.locator('[data-view="chat"]').click()
    page.locator('#prompt').fill('移动鼠标到屏幕中央')
    with page.expect_response('**/api/chat'):
        page.locator('#send').click()
    playwright_api.expect(page.locator('#stop')).to_be_hidden()
    assert requests[-1]['provider'] == 'codex' and requests[-1]['mouse_control'] is True
    page.locator('#mode-toggle').click()
    # An already-enabled switch can still be revoked while in programming mode.
    playwright_api.expect(page.locator('#mouse-control')).to_be_enabled()
    page.locator('#prompt').fill('检查项目')
    with page.expect_response('**/api/chat'):
        page.locator('#send').click()
    playwright_api.expect(page.locator('#stop')).to_be_hidden()
    assert requests[-1]['mode'] == 'codex' and requests[-1]['mouse_control'] is False
    page.locator('#mouse-control').click()
    playwright_api.expect(page.locator('#mouse-control')).to_have_attribute('aria-pressed', 'false')
    playwright_api.expect(page.locator('#mouse-control')).to_be_disabled()
    assert calls == [{'enabled': True}, {'enabled': False}] and not dialogs


def test_browser_mouse_busy_off_revokes_before_aborting(page):
    _, calls = stub_mouse_control(page)
    page.locator('#mouse-control').click()
    playwright_api.expect(page.locator('#mouse-control')).to_have_attribute('aria-pressed', 'true')
    # Keep a deterministic local stream open until AbortController cancels it.
    page.evaluate('''() => {
        const original = window.fetch;
        window.mouseTimeline = [];
        window.fetch = (path, options) => {
            if (path === '/api/chat') {
                window.mouseChatBody = JSON.parse(options.body);
                return Promise.resolve(new Response(new ReadableStream({start(controller) {
                    controller.enqueue(new TextEncoder().encode('data: {"type":"status","text":"等待"}\\n\\n'));
                    options.signal.addEventListener('abort', () => {
                        mouseTimeline.push('aborted');
                        controller.error(new DOMException('Stopped', 'AbortError'));
                    });
                }}), {headers: {'Content-Type': 'text/event-stream'}}));
            }
            if (path === '/api/mouse-control') return original(path, options).then(response => {
                if (response.ok && !JSON.parse(options.body).enabled) mouseTimeline.push('revoked');
                return response;
            });
            return original(path, options);
        };
    }''')
    page.locator('#prompt').fill('移动鼠标到屏幕中央')
    page.locator('#send').click()
    playwright_api.expect(page.locator('#stop')).to_be_visible()
    playwright_api.expect(page.locator('#mouse-control')).to_be_enabled()
    page.locator('#mouse-control').click()
    playwright_api.expect(page.locator('#mouse-control')).to_have_attribute('aria-pressed', 'false')
    playwright_api.expect(page.locator('#stop')).to_be_hidden()
    playwright_api.expect(page.locator('.message.assistant .message-body').last).to_contain_text('已停止')
    assert page.evaluate('window.mouseTimeline') == ['revoked', 'aborted']
    assert page.evaluate('window.mouseChatBody.mouse_control') is True
    assert calls == [{'enabled': True}, {'enabled': False}]


def test_browser_chat_mouse_end_to_end_single_native_action(page, browser_app):
    _, app = browser_app
    plans = []

    async def plan(message, snapshot, provider, transport=None):
        plans.append((message, provider))
        return {'action': 'click', 'x': 30, 'y': 40, 'scroll': 0,
                'width': snapshot['width'], 'height': snapshot['height'], 'message': '仅规划。'}

    app.state.mouse_planner.plan = plan
    page.evaluate("async () => { await post('/api/automation', {mode: 'auto'}); await refreshStatus(); }")
    page.locator('#mouse-control').click()
    playwright_api.expect(page.locator('#mouse-control')).to_have_attribute('aria-pressed', 'true')
    page.locator('#prompt').fill('请点击“设置”按钮')
    page.locator('#send').click()
    playwright_api.expect(page.locator('.message.assistant .message-body').last).to_contain_text('已执行左键点击 (30, 40)')
    playwright_api.expect(page.locator('#stop')).to_be_hidden()
    assert app.state.desktop.driver.clicks == [(30, 40, 'left')]
    assert plans == [('请点击“设置”按钮', 'api')]
    assert not app.state.model_requests and not app.state.desktop.snapshots
    assert page.locator('.action-card, #view-desktop').count() == 0
    page.locator('#mouse-control').click()
    playwright_api.expect(page.locator('#mouse-control')).to_have_attribute('aria-pressed', 'false')


def test_browser_codex_confirmation_and_stop(page):
    page.locator('#mode-toggle').click()
    page.locator('#prompt').fill('slow')
    page.locator('#send').click()
    playwright_api.expect(page.locator('#stop')).to_be_hidden()
    page.locator('.action-card button').click()
    playwright_api.expect(page.locator('#action-details')).to_contain_text('read-only')
    page.locator('#approve-action').click()
    playwright_api.expect(page.locator('#stop')).to_be_visible()
    playwright_api.expect(page.locator('.message-label').last).to_contain_text('Codex 正在处理任务')
    page.locator('#stop').click()
    playwright_api.expect(page.locator('#stop')).to_be_hidden()
    playwright_api.expect(page.locator('.message-body').last).to_contain_text('已停止')
    page.locator('#prompt').fill('restart')
    page.locator('#send').click()
    playwright_api.expect(page.locator('.action-card button').last).to_be_visible()


def test_browser_markdown_stream_history_and_sanitization(page):
    requests = []
    page.on('request', lambda request: requests.append(request.url))
    page.locator('#prompt').fill('展示排版测试 <img src="https://blocked.invalid/user.png">')
    page.locator('#send').click()
    body = page.locator('.message.assistant .message-body').last
    playwright_api.expect(body.locator('h2')).to_have_text('功能检查')
    playwright_api.expect(page.locator('#stop')).to_be_hidden()
    playwright_api.expect(body.locator('strong')).to_have_text('流式')
    playwright_api.expect(body.locator('pre code')).to_have_text('print("<script>这只是代码</script>")')
    playwright_api.expect(body.locator('.markdown-table table')).to_be_visible()
    good_link = body.locator('a', has_text='正常链接')
    assert good_link.get_attribute('href') == 'https://github.com/markedjs/marked'
    assert good_link.get_attribute('target') == '_blank'
    assert 'noopener' in good_link.get_attribute('rel')
    assert body.locator('a', has_text='危险链接').get_attribute('href') is None
    assert body.locator('script,img,iframe,form,input,button,[onclick],[style],.sr-only').count() == 0
    assert page.evaluate('window.markdownExploit === undefined')
    assert not any('blocked.invalid' in request for request in requests)
    user = page.locator('.message.user .message-body').last
    assert user.locator('img').count() == 0
    assert '<img src=' in user.inner_text()
    page.set_viewport_size({'width': 390, 'height': 844})
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.reload()
    playwright_api.expect(page.locator('.message.assistant .message-body h2')).to_have_text('功能检查')
    assert page.locator('.message.assistant .message-body img').count() == 0


def test_browser_missing_bundles_keeps_plain_text_chat(page):
    page.route('**/static/vendor/*.js', lambda route: route.abort())
    page.reload()
    playwright_api.expect(page.locator('#model-status')).to_contain_text('已配置')
    page.locator('#prompt').fill('展示排版测试')
    page.locator('#send').click()
    playwright_api.expect(page.locator('#stop')).to_be_hidden()
    body = page.locator('.message.assistant .message-body').last
    playwright_api.expect(body).to_contain_text('## 功能检查')
    assert body.locator('h2,script,img').count() == 0
    assert page.evaluate('window.markdownExploit === undefined')


def exported_trace(page):
    with page.expect_download() as download:
        page.locator('#export-events').click()
    return json.loads(Path(download.value.path()).read_text(encoding='utf-8'))


def test_browser_developer_metrics_events_and_export(page):
    enable_developer(page)
    assert page.title() == '夏鱼洛 · 你的日常助手'
    playwright_api.expect(page.locator('.brand')).to_contain_text('夏鱼洛')
    playwright_api.expect(page.locator('#metric-state')).to_have_text('IDLE')
    page.locator('#prompt').fill('private-debug-message')
    page.locator('#send').click()
    playwright_api.expect(page.locator('#metric-state')).to_have_text('RUNNING')
    playwright_api.expect(page.locator('#metric-state')).to_have_text('DONE')
    playwright_api.expect(page.locator('#metric-events')).to_have_text('5')
    playwright_api.expect(page.locator('#metric-output')).to_have_text(str(len('你好，这是流式回复。')))
    assert page.locator('#metric-first').inner_text() != '—'
    assert page.locator('#metric-duration').inner_text() != '—'
    page.locator('[data-view="debug"]').click()
    page.locator('#event-filter').select_option('sse')
    playwright_api.expect(page.locator('.event-entry')).to_have_count(5)
    trace = exported_trace(page)
    assert trace['application'] == '夏鱼洛'
    assert trace['source'] == 'client-observed'
    assert len([event for event in trace['events'] if event['category'] == 'sse']) == 5
    serialized = json.dumps(trace, ensure_ascii=False)
    assert 'private-debug-message' not in serialized
    assert '[text omitted' in serialized
    page.locator('#clear-events').click()
    playwright_api.expect(page.locator('#export-events')).to_be_disabled()
    playwright_api.expect(page.locator('#event-list')).to_contain_text('暂无匹配事件')
    page.locator('#new-chat').click()
    playwright_api.expect(page.locator('#metric-state')).to_have_text('IDLE')
    playwright_api.expect(page.locator('#metric-first')).to_have_text('—')


def test_browser_trace_optin_redaction_and_scrubbing(page):
    enable_developer(page)
    page.locator('[data-view="debug"]').click()
    page.locator('#record-payload').check()
    page.locator('[data-view="chat"]').click()
    page.locator('#prompt').fill('user-visible-debug-marker')
    page.locator('#send').click()
    playwright_api.expect(page.locator('#metric-state')).to_have_text('DONE')
    page.locator('[data-view="debug"]').click()
    page.evaluate('''DebugConsole.record('tool', 'redaction fixture', {
        api_key: 'fixture-sensitive-key', token: 'fixture-sensitive-token',
        authorization: 'Bearer fixture-sensitive-auth', image: 'data:image/png;base64,fixture',
        arguments: {prompt: 'opted-in-tool-text'}
    })''')
    trace = exported_trace(page)
    serialized = json.dumps(trace)
    assert 'user-visible-debug-marker' in serialized
    assert 'opted-in-tool-text' in serialized
    assert 'fixture-sensitive' not in serialized
    assert 'data:image/' not in serialized
    page.locator('#record-payload').uncheck()
    scrubbed = json.dumps(exported_trace(page))
    assert 'user-visible-debug-marker' not in scrubbed
    assert 'opted-in-tool-text' not in scrubbed
    assert '[redacted]' in scrubbed
    assert '[image omitted]' in scrubbed


def test_browser_inspector_keyboard_and_narrow_screen(page):
    enable_developer(page)
    playwright_api.expect(page.locator('#inspector')).to_be_visible()
    page.locator('#tab-runtime').focus()
    page.keyboard.press('ArrowRight')
    playwright_api.expect(page.locator('#tab-context')).to_have_attribute('aria-selected', 'true')
    playwright_api.expect(page.locator('#memory-summary')).to_contain_text('永久记忆')
    page.set_viewport_size({'width': 390, 'height': 844})
    playwright_api.expect(page.locator('#inspector')).to_be_hidden()
    page.locator('#toggle-inspector').click()
    playwright_api.expect(page.locator('#inspector')).to_be_visible()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.locator('#close-inspector').click()
    playwright_api.expect(page.locator('#inspector')).to_be_hidden()
    playwright_api.expect(page.locator('#toggle-inspector')).to_be_focused()


def enable_developer(page):
    page.locator('[data-view="settings"]').click()
    page.locator('#developer-mode').check()
    page.locator('[data-view="chat"]').click()


def test_browser_everyday_app_default_and_developer_switch(page):
    playwright_api.expect(page.locator('#welcome')).to_contain_text('你好，我是夏鱼洛')
    playwright_api.expect(page.locator('#inspector')).to_be_hidden()
    playwright_api.expect(page.locator('.run-metrics')).to_be_hidden()
    playwright_api.expect(page.locator('[data-view="debug"]')).to_be_hidden()
    playwright_api.expect(page.locator('#connection-hint')).to_be_hidden()
    page.locator('[data-view="plugins"]').click()
    playwright_api.expect(page.locator('.schema-details').first).to_be_hidden()
    enable_developer(page)
    playwright_api.expect(page.locator('.run-metrics')).to_be_visible()
    page.locator('[data-view="debug"]').click()
    page.locator('#record-payload').check()
    page.locator('[data-view="settings"]').click()
    page.locator('#developer-mode').uncheck()
    playwright_api.expect(page.locator('#record-payload')).not_to_be_checked()
    playwright_api.expect(page.locator('#inspector')).to_be_hidden()
    page.reload()
    playwright_api.expect(page.locator('#developer-mode')).not_to_be_checked()
    playwright_api.expect(page.locator('[data-view="debug"]')).to_be_hidden()


def test_browser_history_search_and_reply_copy(page):
    page.context.grant_permissions(['clipboard-read', 'clipboard-write'])
    page.locator('#prompt').fill('第一段日常聊天')
    page.locator('#send').click()
    playwright_api.expect(page.locator('.message.assistant .message-body').last).to_have_text('你好，这是流式回复。')
    playwright_api.expect(page.locator('#stop')).to_be_hidden()
    page.locator('.copy-message').last.click()
    playwright_api.expect(page.locator('#toast')).to_contain_text('回复已复制')
    assert page.evaluate('navigator.clipboard.readText()') == '你好，这是流式回复。'
    page.locator('#new-chat').click()
    page.locator('#prompt').fill('第二段关于代码的聊天')
    page.locator('#send').click()
    playwright_api.expect(page.locator('.message.assistant .message-body').last).to_have_text('你好，这是流式回复。')
    playwright_api.expect(page.locator('#stop')).to_be_hidden()
    page.locator('#session-search').fill('第一段')
    playwright_api.expect(page.locator('.session-row')).to_have_count(1)
    page.locator('.session-row > button').first.click()
    playwright_api.expect(page.locator('.message.user .message-body')).to_have_text('第一段日常聊天')
    page.locator('#session-search').fill('没有这种标题')
    playwright_api.expect(page.locator('#session-list')).to_contain_text('没有找到相关会话')
    page.locator('#session-search').fill('')
    playwright_api.expect(page.locator('.session-row')).to_have_count(2)


def test_browser_first_use_connection_hint(page):
    def unconfigured(route):
        response = route.fetch()
        status = response.json()
        status['model']['configured'] = False
        status['model']['api_configured'] = False
        status['model']['codex_chat_available'] = False
        status['model']['provider'] = 'api'
        route.fulfill(response=response, json=status)
    page.route('**/api/status*', unconfigured)
    page.reload()
    playwright_api.expect(page.locator('#model-status')).to_have_text('API 待配置')
    page.locator('#connection-hint').click()
    playwright_api.expect(page.locator('#view-settings')).to_be_visible()
    playwright_api.expect(page.locator('#settings-status')).to_contain_text('尚未连接')
    page.locator('#view-settings details:not(.developer-only) summary').click()
    playwright_api.expect(page.locator('#view-settings pre').last).to_contain_text('OPENAI_API_KEY=')


def test_browser_codex_chat_selector_confirmation_and_no_api(page, browser_app):
    _, app = browser_app
    page.locator('[data-view="settings"]').click()
    page.locator('#chat-provider').select_option('codex')
    playwright_api.expect(page.locator('#model-status')).to_contain_text('Codex')
    page.locator('[data-view="chat"]').click()
    page.locator('#prompt').fill('使用会员问答')
    page.locator('#send').click()
    button = page.locator('#messages .action-card button').last
    playwright_api.expect(button).to_be_visible()
    assert not app.state.model_requests
    button.click()
    playwright_api.expect(page.locator('#action-description')).to_contain_text('ChatGPT 登录而非 API 密钥')
    page.locator('#approve-action').click()
    playwright_api.expect(page.locator('.message.assistant .message-body').last).to_contain_text('chat fixture: 使用会员问答')
    assert not app.state.model_requests


def test_download_update_button_reports_missing_source_without_navigation(page):
    page.route('**/api/updates', lambda route: route.fulfill(status=409,
        json={'detail': '签名更新源尚未发布，无法下载更新；当前版本保留。'}))
    opened, installed = [], []
    page.context.on('page', lambda popup: opened.append(popup))
    page.route('**/api/updates/install', lambda route: installed.append(route.request))
    original = page.url
    page.locator('[data-view="settings"]').click()
    button = page.get_by_role('button', name='↓ 下载更新', exact=True)
    playwright_api.expect(button).to_be_visible()
    assert button.get_attribute('href') is None
    button.click()
    playwright_api.expect(page.locator('#update-status')).to_contain_text('当前版本保留')
    assert page.url == original
    assert not opened and not installed
    playwright_api.expect(page.locator('#install-update')).to_be_hidden()


@pytest.mark.parametrize('available,supported,expected', [(True, True, 1), (False, True, 0), (True, False, 0)])
def test_download_update_button_checks_then_installs_inside_app(page, available, supported, expected):
    page.evaluate("async () => { await post('/api/automation', {mode: 'auto'}); await refreshStatus(); }")
    page.route('**/api/updates', lambda route: route.fulfill(json={'available': available,
        'version': '9.0.0', 'current': '0.1.11', 'notes': '测试签名发布', 'install_supported': supported}))
    installed, opened = [], []

    def install(route):
        installed.append(route.request.post_data_json)
        route.fulfill(json={'restarting': True})

    page.route('**/api/updates/install', install)
    page.context.on('page', lambda popup: opened.append(popup))
    page.locator('[data-view="settings"]').click()
    page.locator('#download-update').click()
    if expected:
        playwright_api.expect(page.locator('#update-status')).to_contain_text('正在重启安装')
    elif available:
        playwright_api.expect(page.locator('#update-status')).to_contain_text('源码版')
    else:
        playwright_api.expect(page.locator('#update-status')).to_contain_text('暂无更高版本')
    assert len(installed) == expected and not opened
    if expected:
        assert installed == [{'confirm': True}]


def attach_speech(page, app):
    from test_speech_api import SpeechStub
    native = SpeechStub()
    app.state.speech = native
    page.evaluate('refreshSpeech()')
    playwright_api.expect(page.locator('#reply-speech')).to_have_text('朗读：开')
    return native


def test_reply_speech_automatically_reads_new_answer_without_microphone_and_not_history(page, browser_app):
    _, app = browser_app
    native = attach_speech(page, app)
    page.locator('#prompt').fill('你好')
    page.locator('#send').click()
    playwright_api.expect(page.locator('.message.assistant .message-body').last).to_have_text('你好，这是流式回复。')
    playwright_api.expect(page.locator('#stop')).to_be_hidden()
    playwright_api.expect(page.locator('#stop-speaking')).to_be_visible()
    assert native.calls and len(native.calls) == 1
    assert native.calls[0][0] == '你好，这是流式回复。'
    assert app.state.voice is None
    page.evaluate('syncViews()')
    page.reload()
    playwright_api.expect(page.locator('#reply-speech')).to_have_text('朗读：开')
    playwright_api.expect(page.locator('#messages')).to_contain_text('你好，这是流式回复。')
    assert len(native.calls) == 1


def test_reply_speech_stop_and_switch_are_independent_of_microphone(page, browser_app):
    _, app = browser_app
    native = attach_speech(page, app)
    native.speaking = True
    page.evaluate('refreshSpeech()')
    page.locator('#stop-speaking').click()
    playwright_api.expect(page.locator('#stop-speaking')).to_be_hidden()
    assert not native.speaking and native.enabled
    page.locator('#reply-speech').click()
    playwright_api.expect(page.locator('#reply-speech')).to_have_attribute('aria-pressed', 'false')
    page.locator('#prompt').fill('静音问题')
    page.locator('#send').click()
    playwright_api.expect(page.locator('.message.assistant .message-body').last).to_have_text('你好，这是流式回复。')
    playwright_api.expect(page.locator('#stop')).to_be_hidden()
    assert not native.calls and not native.enabled and app.state.voice is None
    page.locator('[data-view="settings"]').click()
    page.locator('#reply-speech-setting').click()
    playwright_api.expect(page.locator('#reply-speech-setting')).to_be_checked()
    assert native.enabled


@pytest.mark.parametrize('kind', ['incomplete', 'error', 'pending'])
def test_reply_speech_never_reads_unsuccessful_or_pending_replies(page, browser_app, kind):
    _, app = browser_app
    native = attach_speech(page, app)
    packets = [{'type': 'text', 'text': '未完成的内容'}]
    if kind == 'error': packets.append({'type': 'error', 'message': '本轮失败'})
    if kind == 'pending':
        packets.extend([{'type': 'action', 'action': {'id': 'fixture', 'tool': 'remember',
            'title': '保存记忆', 'arguments': {'tier': 'second', 'kind': 'fact', 'content': '测试'}}}, {'type': 'done'}])
    page.route('**/api/chat', lambda route: route.fulfill(content_type='text/event-stream',
        body=''.join('data: ' + json.dumps(item) + '\n\n' for item in packets)))
    page.locator('#prompt').fill('测试停止或待确认')
    page.locator('#send').click()
    if kind == 'pending':
        playwright_api.expect(page.locator('.action-card')).to_be_visible()
    else:
        playwright_api.expect(page.locator('.message.assistant.error')).to_be_visible()
    playwright_api.expect(page.locator('#stop')).to_be_hidden()
    assert native.calls == []


def test_reply_speech_failure_preserves_successful_text_and_session_switch_stops_audio(page, browser_app):
    _, app = browser_app
    native = attach_speech(page, app)
    native.fail = RuntimeError('private driver error')
    page.locator('#prompt').fill('朗读失败仍然显示回复')
    page.locator('#send').click()
    playwright_api.expect(page.locator('.message.assistant .message-body').last).to_have_text('你好，这是流式回复。')
    playwright_api.expect(page.locator('#stop')).to_be_hidden()
    playwright_api.expect(page.locator('.message.assistant .message-body').last).to_have_text('你好，这是流式回复。')
    assert page.locator('.message.assistant.error').count() == 0
    playwright_api.expect(page.locator('#toast')).to_contain_text('文字回复已完成')
    native.speaking = True
    previous = native.generation
    page.locator('#new-chat').click()
    playwright_api.expect(page.locator('#welcome')).to_be_visible()
    assert not native.speaking and native.generation > previous


def test_reply_speech_off_then_on_during_generation_never_reads_old_reply(page, browser_app):
    _, app = browser_app
    native = attach_speech(page, app)
    page.locator('#prompt').fill('回复时关闭再开启声音')
    page.locator('#send').click()
    playwright_api.expect(page.locator('#stop')).to_be_visible()
    page.locator('#reply-speech').click()
    playwright_api.expect(page.locator('#reply-speech')).to_have_attribute('aria-pressed', 'false')
    page.locator('#reply-speech').click()
    playwright_api.expect(page.locator('#reply-speech')).to_have_attribute('aria-pressed', 'true')
    playwright_api.expect(page.locator('#stop')).to_be_hidden()
    assert native.enabled and not native.calls


def test_startup_history_restore_does_not_override_user_navigation(page, browser_app):
    _, app = browser_app
    session = page.evaluate("post('/api/sessions')")['id']
    app.state.memory.append(session, 'user', '之前的对话')
    app.state.memory.append(session, 'assistant', '只恢复文字，不抢走当前页面。')
    held = []
    page.route(f'**/api/sessions/{session}', lambda route: held.append((route, route.fetch())))
    with page.expect_request(f'**/api/sessions/{session}'):
        page.reload()
    page.locator('[data-view="settings"]').click()
    playwright_api.expect(page.locator('#view-settings')).to_be_visible()
    assert held
    route, response = held[0]
    route.fulfill(response=response)
    playwright_api.expect(page.locator('#messages')).to_contain_text('只恢复文字')
    playwright_api.expect(page.locator('#view-settings')).to_be_visible()


def test_private_source_mode_never_auto_checks_or_imports_updates(page):
    requests = []
    page.on('request', lambda request: requests.append(request.url))
    page.reload()
    page.locator('[data-view="settings"]').click()
    playwright_api.expect(page.locator('#import-update')).to_be_disabled()
    playwright_api.expect(page.locator('#update-status')).to_contain_text('签名版本')
    assert not any(url.endswith('/api/updates') or url.endswith('/api/updates/import') for url in requests)


@pytest.mark.parametrize('outcome', ['cancel', 'error', 'restart'])
def test_private_native_import_button_reports_real_outcome_without_client_paths(page, outcome):
    requests = []
    def native_status(route):
        response = route.fetch()
        status = response.json()
        status['runtime']['update_supported'] = True
        status['automation']['mode'] = 'auto'
        route.fulfill(response=response, json=status)
    def imported(route):
        requests.append(route.request.post_data_json)
        if outcome == 'error':
            route.fulfill(status=409, json={'detail': '签名无效，原版保留。'})
        else:
            route.fulfill(json={'restarting': outcome == 'restart', 'cancelled': outcome == 'cancel'})
    page.route('**/api/status*', native_status)
    page.route('**/api/updates', lambda route: route.fulfill(json={'available': False, 'current': '0.1.9',
        'version': '0.1.9', 'notes': '', 'install_supported': True}))
    page.route('**/api/updates/import', imported)
    with page.expect_response('**/api/updates'):
        page.reload()
    page.locator('[data-view="settings"]').click()
    playwright_api.expect(page.locator('#import-update')).to_be_enabled()
    playwright_api.expect(page.locator('#check-update')).to_be_enabled()
    page.locator('#import-update').click()
    expected = {'cancel': '已取消', 'error': '签名无效', 'restart': '正在重启更新'}[outcome]
    playwright_api.expect(page.locator('#update-status')).to_contain_text(expected)
    assert requests == [{'confirm': True}]


@pytest.mark.parametrize('outcome', ['new', 'current', 'unpublished'])
def test_native_startup_checks_updates_without_automatically_installing(page, outcome):
    requests = []
    def native_status(route):
        response = route.fetch()
        status = response.json()
        status['runtime']['update_supported'] = True
        route.fulfill(response=response, json=status)
    def check(route):
        requests.append(route.request.method)
        if outcome == 'unpublished':
            route.fulfill(status=409, json={'detail': '更新源尚未发布'})
        else:
            route.fulfill(json={'available': outcome == 'new', 'version': '0.2.0' if outcome == 'new' else '0.1.9',
                                'current': '0.1.9', 'notes': '修复测试', 'install_supported': True})
    page.route('**/api/status*', native_status)
    page.route('**/api/updates', check)
    with page.expect_response('**/api/updates'):
        page.reload()
    badge = page.locator('#update-available')
    if outcome == 'new':
        playwright_api.expect(badge).to_be_visible()
        badge.click()
        playwright_api.expect(page.locator('#install-update')).to_be_visible()
        playwright_api.expect(page.locator('#update-status')).to_contain_text('发现 0.2.0')
    else:
        playwright_api.expect(badge).to_be_hidden()
        page.locator('[data-view="settings"]').click()
        expected = '暂无更高版本' if outcome == 'current' else '更新源尚未发布'
        playwright_api.expect(page.locator('#update-status')).to_contain_text(expected)
    assert requests == ['GET']


def test_voice_opt_in_start_stop_controls(page):
    requests = []
    running = [False]
    def voice_status(route):
        route.fulfill(content_type='application/json', body=json.dumps({'available': True,
            'running': running[0], 'message': '正在本地监听' if running[0] else '语音未开启'}))
    def voice_start(route):
        requests.append(route.request.post_data_json)
        running[0] = True
        route.fulfill(content_type='application/json', body='{}')
    def voice_stop(route):
        running[0] = False
        route.fulfill(content_type='application/json', body='{"stopped":true}')
    page.route('**/api/voice', voice_status)
    page.route('**/api/voice/start', voice_start)
    page.route('**/api/voice/stop', voice_stop)
    page.locator('[data-view="settings"]').click()
    start = page.locator('#voice-start')
    playwright_api.expect(start).to_be_enabled(timeout=6000)
    page.once('dialog', lambda dialog: dialog.dismiss())
    start.click()
    assert not requests
    page.once('dialog', lambda dialog: dialog.accept())
    start.click()
    playwright_api.expect(page.locator('#voice-status')).to_contain_text('正在本地监听')
    assert requests == [{'confirm': True}]
    playwright_api.expect(page.locator('#voice-indicator')).to_have_attribute('aria-label', '停止监听：正在本地监听')
    page.locator('[data-view="chat"]').click()
    page.locator('#voice-indicator').click()  # Global stop works outside Settings too.
    page.locator('[data-view="settings"]').click()
    playwright_api.expect(page.locator('#voice-status')).to_contain_text('语音未开启')


def test_browser_devices_and_remote_task_confirmation(page, browser_app):
    _, app = browser_app
    page.locator('[data-view="devices"]').click()
    playwright_api.expect(page.locator('#invite-device')).to_be_disabled()
    playwright_api.expect(page.locator('#mobile-setup-note')).to_contain_text('MOBILE_ENABLED=true')
    session = app.state.memory.create_session()
    app.state.actions.propose('remember', {'tier': 'permanent', 'content': '从其他客户端提出的记忆'}, session['id'])
    page.locator('[data-view="tasks"]').click()
    playwright_api.expect(page.locator('#pending-tasks')).to_contain_text('从其他客户端提出的记忆')
    page.locator('#pending-tasks button').click()
    playwright_api.expect(page.locator('#action-dialog')).to_be_visible()
    assert app.state.memory.stats()['permanent'] == 0
    page.locator('#approve-action').click()
    playwright_api.expect(page.locator('.message.assistant .message-body').last).to_contain_text('已保存')
    assert app.state.memory.stats()['permanent'] == 1


def test_browser_polls_shared_history_without_page_reload(page, browser_app):
    _, app = browser_app
    session = app.state.memory.create_session()
    app.state.memory.append(session['id'], 'user', '手机上的同一段对话')
    button = page.locator('.session-row > button').filter(has_text='手机上的同一段对话')
    playwright_api.expect(button).to_be_visible(timeout=8000)
    button.click()
    app.state.memory.append(session['id'], 'assistant', '电脑与开发者模式也能继续查看')
    playwright_api.expect(page.locator('.message.assistant .message-body').last).to_have_text('电脑与开发者模式也能继续查看', timeout=8000)
