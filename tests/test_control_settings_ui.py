"""Native control settings are explicit, local, and honest about shadow mode."""
from test_browser import browser_app, page, playwright_api  # noqa: F401


def desktop(page):
    page.evaluate("state.status.runtime.presentation = 'desktop'")


def test_new_controls_are_read_only_when_not_native_and_never_start_on_open(page):
    writes = []
    def controls(route):
        if route.request.method != 'GET': writes.append(route.request.url)
        route.fulfill(json={'available': False, 'enabled': False, 'running': False,
                            'message': '需要电脑原生窗口，未开始观察。'})
    page.route('**/api/autonomy', controls)
    page.route('**/api/visual-loop', controls)
    page.locator('[data-view="settings"]').click()
    for card in ('speech-personalization', 'autonomy-card', 'visual-loop-card'):
        playwright_api.expect(page.locator('#' + card)).to_be_visible()
    for control in ('autonomy-enabled', 'visual-loop-start', 'speech-profile-save'):
        playwright_api.expect(page.locator('#' + control)).to_be_disabled()
    playwright_api.expect(page.locator('#visual-loop-card')).to_contain_text('影子测试不执行动作')
    assert writes == []


def test_voice_profile_is_explicit_and_uses_plain_local_names(page):
    state = {'available': True, 'enabled': True, 'speaking': False, 'generation': 0,
             'voice_id': 'zh-a', 'rate': 175, 'volume': .8, 'phase': 'idle', 'message': '本地声音'}
    calls = []
    def speech(route):
        if route.request.method == 'POST':
            body = route.request.post_data_json
            calls.append(('configure', body))
            state.update(body, generation=state['generation'] + 1)
        route.fulfill(json=state)
    def preview(route):
        calls.append(('preview', route.request.post_data_json))
        state.update(speaking=True, phase='speaking')
        route.fulfill(json=state)
    page.route('**/api/speech', speech)
    page.route('**/api/speech/voices', lambda route: route.fulfill(json={'items': [
        {'id': 'zh-a', 'name': '系统中文甲', 'languages': ['zh']},
        {'id': 'zh-b', 'name': '<img src=x onerror="window.voiceExecuted=true">', 'languages': ['zh']}]}))
    page.route('**/api/speech/speak', preview)
    desktop(page)
    page.evaluate('refreshSpeech()')
    page.locator('[data-view="settings"]').click()
    assert calls == []
    page.locator('#speech-voices-refresh').click()
    playwright_api.expect(page.locator('#speech-voice option')).to_have_count(2)
    page.locator('#speech-voice').select_option('zh-b')
    page.locator('#speech-rate').fill('210')
    page.locator('#speech-volume').fill('55')
    page.evaluate('refreshSpeech()')  # Periodic status must not discard unsaved input.
    playwright_api.expect(page.locator('#speech-rate')).to_have_value('210')
    playwright_api.expect(page.locator('#speech-profile-preview')).to_be_disabled()
    page.locator('#speech-profile-save').click()
    playwright_api.expect(page.locator('#speech-profile-status')).to_contain_text('已保存')
    assert calls == [('configure', {'enabled': True, 'voice_id': 'zh-b', 'rate': 210, 'volume': .55})]
    page.locator('#speech-profile-preview').click()
    playwright_api.expect(page.locator('#speech-profile-stop')).to_be_enabled()
    assert calls[-1] == ('preview', {'text': '你好，我是夏鱼洛。这是当前保存的本地声音。', 'generation': 1})
    assert page.locator('#speech-personalization img').count() == 0
    assert page.evaluate('window.voiceExecuted === undefined')


def test_autonomy_goal_and_decision_only_happen_on_explicit_actions(page):
    state = {'available': True, 'enabled': False, 'phase': 'off', 'goal': None,
             'last_decision': None, 'message': '默认关闭'}
    calls = []
    def controls(route):
        if route.request.method == 'POST':
            body = route.request.post_data_json
            calls.append((route.request.url.rsplit('/', 1)[-1], body))
            if route.request.url.endswith('/autonomy'): state.update(enabled=body['enabled'], phase='waiting')
            elif route.request.url.endswith('/goals'): state.update(goal={**body, 'status': 'active'}, message='本次目标已设置')
            elif route.request.url.endswith('/decide'): state.update(last_decision={'kind': 'ask', 'message': '<img src=x onerror="window.decisionExecuted=true">'})
            elif route.request.url.endswith('/stop'): state.update(goal={**state['goal'], 'status': 'stopped'}, phase='stopped')
        route.fulfill(json=state)
    page.route('**/api/autonomy', controls)
    page.route('**/api/autonomy/*', controls)
    desktop(page)
    page.locator('[data-view="settings"]').click()
    assert calls == []
    page.locator('#autonomy-enabled').check()
    playwright_api.expect(page.locator('#autonomy-goal-save')).to_be_enabled()
    page.locator('#autonomy-goal').fill('帮我整理保存步骤')
    page.locator('#autonomy-criteria').fill('能说明需要核实的结果')
    page.locator('#autonomy-goal-save').click()
    playwright_api.expect(page.locator('#autonomy-decide')).to_be_enabled()
    page.locator('#autonomy-decide').click()
    playwright_api.expect(page.locator('#autonomy-decision')).to_contain_text('<img src=x')
    assert calls == [('autonomy', {'enabled': True}),
                     ('goals', {'goal': '帮我整理保存步骤', 'success_criteria': '能说明需要核实的结果'}),
                     ('decide', {})]
    assert page.locator('#autonomy-decision img').count() == 0
    assert page.evaluate('window.decisionExecuted === undefined')
    page.locator('#autonomy-stop').click()
    playwright_api.expect(page.locator('#autonomy-enabled')).to_be_checked()
    playwright_api.expect(page.locator('#autonomy-decide')).to_be_disabled()
    assert calls[-1] == ('stop', {})
    page.locator('#autonomy-enabled').uncheck()
    playwright_api.expect(page.locator('#autonomy-enabled')).not_to_be_checked()
    assert calls[-1] == ('autonomy', {'enabled': False})


def test_visual_shadow_reports_actual_fields_and_stop_invalidates_late_start(page):
    state = {'available': True, 'running': False, 'phase': 'off', 'policy_ready': False,
             'capture_fps': 0., 'inference_fps': 0., 'p95_capture_ms': None,
             'p95_inference_ms': None, 'dropped_frames': 0, 'execution_permitted': False,
             'message': '影子模式默认关闭'}
    pending, stops = [], []
    page.route('**/api/visual-loop', lambda route: route.fulfill(json=state))
    page.route('**/api/visual-loop/start', lambda route: pending.append(route))
    def stop(route):
        stops.append(route.request.post_data_json)
        state.update(running=False, phase='stopped', message='已停止本地观察')
        route.fulfill(json=state)
    page.route('**/api/visual-loop/stop', stop)
    page.route('**/api/autonomy/stop', lambda route: route.fulfill(json={'available': True, 'enabled': False}))
    desktop(page)
    page.locator('[data-view="settings"]').click()
    playwright_api.expect(page.locator('#visual-loop-goal')).to_have_attribute('maxlength', '128')
    playwright_api.expect(page.locator('#visual-loop-card')).to_contain_text('3秒准备时间')
    page.locator('#visual-loop-goal').fill('检查屏幕变化')
    page.locator('#visual-loop-frequency').fill('20')
    page.locator('#visual-loop-start').click()
    assert len(pending) == 1 and pending[0].request.post_data_json == {'goal': '检查屏幕变化', 'frequency_hz': 20}
    page.locator('#visual-loop-stop').click()
    playwright_api.expect(page.locator('#visual-loop-status')).to_contain_text('已停止')
    pending[0].fulfill(json={**state, 'running': True, 'capture_fps': 29.9, 'message': '过时运行回执'})
    page.wait_for_timeout(100)
    playwright_api.expect(page.locator('#visual-loop-status')).not_to_contain_text('过时运行回执')
    playwright_api.expect(page.locator('#visual-loop-status')).to_contain_text('没有已加载策略')
    playwright_api.expect(page.locator('#visual-loop-inference-fps')).to_have_text('0.0 次/秒')
    assert stops == [{}]


def test_phone_path_hides_new_local_controls_without_reading_local_state(page):
    reads = []
    page.route('**/api/autonomy', lambda route: (reads.append('autonomy'), route.fulfill(json={})))
    page.route('**/api/visual-loop', lambda route: (reads.append('visual'), route.fulfill(json={})))
    page.evaluate("history.replaceState(null, '', '/mobile/settings')")
    page.locator('[data-view="settings"]').click()
    for card in ('speech-personalization', 'autonomy-card', 'visual-loop-card'):
        playwright_api.expect(page.locator('#' + card)).to_be_hidden()
    assert reads == []


def test_visual_test_requires_mouse_grant_and_unknown_running_state_can_still_stop(page):
    state = {'available': True, 'running': False, 'phase': 'off', 'policy_ready': False,
             'requires_mouse_control': True, 'mouse_control_enabled': False}
    fail = [False]
    def status(route):
        if fail[0]: route.fulfill(status=503, json={'detail': '状态暂不可读'})
        else: route.fulfill(json=state)
    page.route('**/api/visual-loop', status)
    desktop(page)
    page.locator('[data-view="settings"]').click()
    playwright_api.expect(page.locator('#visual-loop-start')).to_be_disabled()
    playwright_api.expect(page.locator('#visual-loop-status')).to_contain_text('请先在聊天栏开启鼠标控制')
    state.update(mouse_control_enabled=True, running=True, phase='starting')
    page.locator('#local-controls-refresh').click()
    playwright_api.expect(page.locator('#visual-loop-status')).to_contain_text('3秒内切到目标窗口')
    fail[0] = True
    page.locator('#local-controls-refresh').click()
    playwright_api.expect(page.locator('#visual-loop-status')).to_contain_text('上次状态为运行；当前未核实')
    playwright_api.expect(page.locator('#visual-loop-start')).to_be_disabled()
    playwright_api.expect(page.locator('#visual-loop-stop')).to_be_enabled()
    page.locator('[data-view="chat"]').click()
    playwright_api.expect(page.locator('#local-controls-stop')).to_be_visible()
