import asyncio
from contextlib import aclosing, contextmanager
from io import BytesIO
import json
import os
from pathlib import Path
import sys

import httpx
from PIL import Image
import pytest
from pydantic import ValidationError

from assistant.config import Settings
from assistant.proactive import (ObservationSkipped, ProactiveAssistant, Suggestion,
                                 SuggestionPlanner, WindowsScreenObserver, _ObservationCodex)
from conftest import model_stream


class Clock:
    def __init__(self):
        self.now = 0

    def __call__(self):
        return self.now

    def advance(self, seconds=120):
        self.now += seconds


class Planner:
    def __init__(self, result=None):
        self.calls = []
        self.result = result or {'kind': 'question', 'message': '需要我帮你整理一下思路吗？'}

    async def plan(self, snapshot, provider, transport=None):
        self.calls.append((snapshot, provider))
        return self.result


def fixture(**kwargs):
    clock, planner, captures = Clock(), Planner(), []

    def capture():
        captures.append(True)
        return {'width': 200, 'height': 120, 'png': b'fixture-' + bytes([len(captures)])}

    assistant = ProactiveAssistant(Settings(), capture=capture, available=lambda: (True, ''),
        planner=planner, clock=clock, random=kwargs.pop('random', lambda: 0.0),
        eligible=kwargs.pop('eligible', lambda: True), **kwargs)
    return assistant, clock, planner, captures


@pytest.mark.parametrize('result', [
    {'kind': 'click', 'message': '点击'}, {'kind': 'none', 'message': '不应输出'},
    {'kind': 'question', 'message': ' '}, {'kind': 'suggestion', 'message': 'x' * 301},
    {'kind': 'question', 'message': '你好', 'x': 20}, {'kind': 'question', 'message': True},
])
def test_schema_has_no_actions_and_rejects_unbounded_or_missing_text(result):
    with pytest.raises(ValidationError):
        Suggestion.model_validate(result)


def test_off_by_default_native_unavailable_and_strict_enable():
    assistant, clock, planner, captures = fixture()
    clock.advance(500)
    assert asyncio.run(assistant.tick()) is None
    assert not assistant.status()['enabled'] and not captures and not planner.calls
    assert assistant.events() == []
    with pytest.raises(ValueError):
        assistant.set_enabled('true')
    unavailable = ProactiveAssistant(Settings(), available=lambda: (False, '仅本机'))
    with pytest.raises(ValueError, match='仅本机'):
        unavailable.set_enabled(True)
    assert not unavailable.status()['enabled']


def test_observation_interval_is_independent_of_notice_probability():
    samples = iter([0.9, 0.1])
    assistant, clock, planner, captures = fixture(random=lambda: next(samples))
    assistant.set_enabled(True)
    assert asyncio.run(assistant.tick()) is None
    clock.advance()
    assert asyncio.run(assistant.tick()) is None
    assert len(captures) == len(planner.calls) == 1
    clock.advance(29)
    assert asyncio.run(assistant.tick()) is None
    assert len(captures) == 1
    clock.advance(91)
    notice = asyncio.run(assistant.tick())
    assert notice == {'id': 1, 'generation': 1, 'kind': 'question', 'message': '需要我帮你整理一下思路吗？'}
    assert len(captures) == len(planner.calls) == 2
    assert assistant.events(0) == [notice]
    assert assistant.events(1) == []
    assert 'png' not in assistant.status() and 'message' not in assistant.events(1)


def test_only_transient_text_is_kept_and_off_or_expiry_removes_notices():
    assistant, clock, planner, captures = fixture()
    assistant.set_enabled(True)
    clock.advance()
    notice = asyncio.run(assistant.tick())
    assert set(assistant.events()[0]) == {'id', 'kind', 'message', 'generation'}
    assert assistant.status()['cursor'] == 1
    clock.advance(assistant.EVENT_TTL)
    assert assistant.events() == []
    clock.advance()
    asyncio.run(assistant.tick())
    assert assistant.events()
    assistant.set_enabled(False)
    assert assistant.events() == [] and assistant.status()['cursor'] == 2
    assistant.set_enabled(True)
    assert assistant.events() == []
    assert not any(isinstance(value, bytes) for value in vars(assistant).values())


def test_cooldown_and_three_notice_hourly_cap_survive_toggle():
    assistant, clock, planner, captures = fixture()
    assistant.set_enabled(True)
    clock.advance()
    for index in range(3):
        assert asyncio.run(assistant.tick())['id'] == index + 1
        clock.advance(599)
        assert asyncio.run(assistant.tick()) is None
        clock.advance(120)
    assert len(planner.calls) == len(captures) == 6
    assistant.set_enabled(False)
    assistant.set_enabled(True)
    clock.advance()
    assert asyncio.run(assistant.tick()) is None
    assert len(captures) == 7
    clock.advance(3600)
    assert asyncio.run(assistant.tick())['id'] == 4
    assert assistant.status()['max_analyses_per_hour'] == 12
    assert assistant.status()['max_notices_per_hour'] == 3


def test_identical_screen_and_model_noop_do_not_create_messages():
    assistant, clock, planner, captures = fixture()
    assistant.capture = lambda: {'width': 200, 'height': 120, 'png': b'unchanged'}
    planner.result = {'kind': 'none', 'message': ''}
    assistant.set_enabled(True)
    for _ in range(5):
        clock.advance()
        assert asyncio.run(assistant.tick()) is None
    assert len(planner.calls) == 1 and not assistant.events()


def test_busy_ineligible_never_captures_and_late_eligibility_change_discards_result():
    async def run():
        ready = [False]
        assistant, clock, planner, captures = fixture(eligible=lambda: ready[0])
        assistant.set_enabled(True)
        clock.advance()
        assert await assistant.tick() is None
        assert not captures
        ready[0] = True
        began, finish = asyncio.Event(), asyncio.Event()

        async def plan(*args):
            began.set()
            await finish.wait()
            return {'kind': 'suggestion', 'message': '这是旧建议。'}

        planner.plan = plan
        task = asyncio.create_task(assistant.tick())
        await asyncio.wait_for(began.wait(), 2)
        ready[0] = False
        finish.set()
        assert await task is None
        assert not assistant.events()
        await assistant.close()

    asyncio.run(run())


@pytest.mark.parametrize('reopen', [False, True])
def test_off_cancels_owned_analysis_cleanup_and_never_replays_after_reenable(reopen):
    async def run():
        assistant, clock, planner, captures = fixture()
        began, cleaned = asyncio.Event(), asyncio.Event()

        async def slow(*args):
            began.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.set()

        planner.plan = slow
        assistant.set_enabled(True)
        clock.advance()
        task = asyncio.create_task(assistant.tick())
        await asyncio.wait_for(began.wait(), 2)
        await asyncio.to_thread(assistant.set_enabled, False)
        if reopen:
            assistant.set_enabled(True)
        assert await asyncio.wait_for(task, 2) is None
        assert cleaned.is_set() and not assistant.events() and assistant.task is None
        await assistant.close()

    asyncio.run(run())


def test_interrupt_preserves_switch_but_cancels_for_user_work_and_close_owns_scheduler():
    async def run():
        assistant, clock, planner, captures = fixture()
        began, cleaned = asyncio.Event(), asyncio.Event()

        async def slow(*args):
            began.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.set()

        planner.plan = slow
        assistant.set_enabled(True)
        await assistant.start()
        clock.advance()
        pending = asyncio.create_task(assistant.tick())
        await asyncio.wait_for(began.wait(), 2)
        previous = assistant.status()['generation']
        assistant.interrupt()
        assert await asyncio.wait_for(pending, 2) is None
        assert cleaned.is_set() and assistant.enabled
        assert assistant.status()['generation'] == previous + 1
        assert await assistant.tick() is None
        scheduler = assistant.scheduler
        await assistant.close()
        assert scheduler.done() and not assistant.enabled and not assistant.events()
        with pytest.raises(ValueError, match='退出'):
            assistant.set_enabled(True)

    asyncio.run(run())


def test_background_failure_is_generic_and_does_not_disable_chat_or_expose_screen_text():
    assistant, clock, planner, captures = fixture()

    async def fail(*args):
        raise RuntimeError('secret title/path/content')

    planner.plan = fail
    assistant.set_enabled(True)
    clock.advance()
    assert asyncio.run(assistant.tick()) is None
    assert assistant.status()['phase'] == 'attention'
    assert 'secret' not in str(assistant.status()) and assistant.enabled
    assert not assistant.events()


class ScreenDriver:
    def __init__(self):
        self.regions = []

    def size(self):
        return 2560, 1440

    def screenshot(self, *, region):
        self.regions.append(region)
        return Image.new('RGB', (region[2], region[3]), 'white')


def observer(title='Editor', rect=(-20, 40, 2500, 1500), pid=-1):
    driver = ScreenDriver()
    capture = WindowsScreenObserver(True, platform='win32', driver=driver,
        window_info=lambda: {'title': title, 'rect': rect, 'pid': pid})
    return capture, driver


def test_observer_crops_primary_foreground_and_downscales_without_mouse_cache_or_title():
    capture, driver = observer()
    frame = capture.capture()
    assert driver.regions == [(0, 40, 2500, 1400)]
    assert frame['width'] == 1280 and frame['height'] <= 1280
    assert set(frame) == {'width', 'height', 'png', 'fingerprint'}
    with Image.open(BytesIO(frame['png'])) as image:
        assert image.size == (frame['width'], frame['height'])
    assert not hasattr(capture, 'snapshots') and not hasattr(capture, 'click')


@pytest.mark.parametrize('title', ['1Password', 'My bank - banking', '支付', '账号登录',
                                  'Private Browsing', '浏览器 - 无痕', '验证码'])
def test_private_titles_are_skipped_before_screenshot(title):
    capture, driver = observer(title=title)
    with pytest.raises(ObservationSkipped):
        capture.capture()
    assert not driver.regions


def test_own_window_off_primary_small_or_disabled_are_never_read():
    for kwargs in ({'pid': os.getpid()}, {'rect': (3000, 0, 3400, 300)}, {'rect': (0, 0, 20, 20)}):
        capture, driver = observer(**kwargs)
        with pytest.raises(ObservationSkipped):
            capture.capture()
        assert not driver.regions
    for enabled, platform in [(False, 'win32'), (True, 'linux')]:
        capture = WindowsScreenObserver(enabled, platform=platform)
        assert capture.available()[0] is False
        with pytest.raises(ValueError):
            capture.capture()


def test_api_sends_only_one_image_no_tools_and_no_other_conversations():
    records = []
    response = {'kind': 'question', 'message': '要不要先列出下一步？'}

    def handler(request):
        records.append(json.loads(request.content))
        return httpx.Response(200, text=model_stream({'content': json.dumps(response)}))

    frame = {'width': 100, 'height': 80, 'png': b'fixture'}
    planner = SuggestionPlanner(Settings(api_key='fixture'))
    planned = asyncio.run(planner.plan(frame, 'api', httpx.MockTransport(handler)))
    assert {key: planned[key] for key in response} == response
    assert planned['category'] == 'unknown' and planned['activity'] == ''
    assert len(records) == 1 and 'tools' not in records[0]
    assert len(records[0]['messages']) == 2
    content = records[0]['messages'][1]['content']
    assert len(content) == 2 and content[1]['image_url']['url'].startswith('data:image/png;base64,')
    assert '不可信观察数据' in records[0]['messages'][0]['content']


@pytest.mark.parametrize('stream', [model_stream({'content': 'not json'}),
    model_stream({'content': '{"kind":"question","message":"你好","action":"click"}'}),
    model_stream({'content': '{"kind":"question","message":"你好"}'}, finish=None),
    model_stream({'content': '{"kind":"question","message":"你好"}'}, finish='length'),
    model_stream({'tool_calls': [{'function': {'name': 'desktop_mouse'}}]}),
    model_stream({'content': 'x' * 4097})])
def test_invalid_truncated_or_tool_output_is_not_a_suggestion(stream):
    planner = SuggestionPlanner(Settings(api_key='fixture'))
    with pytest.raises(ValueError):
        asyncio.run(planner.plan({'width': 100, 'height': 80, 'png': b'fixture'}, 'api',
                                httpx.MockTransport(lambda request: httpx.Response(200, text=stream))))


def test_codex_isolated_tool_free_image_and_schema_are_removed(monkeypatch):
    helper = Path(__file__).with_name('fake_proactive_codex.py').resolve()
    monkeypatch.setattr(_ObservationCodex, 'command_prefix', lambda self: [sys.executable, str(helper)])
    monkeypatch.setenv('OPENAI_API_KEY', 'never-send-to-cli')
    monkeypatch.setenv('CODEX_API_KEY', 'never-send-to-cli')
    monkeypatch.setenv('ASSISTANT_MODEL_KEY', 'never-send-to-cli')
    original, paths = _ObservationCodex.execution_directory, []

    @contextmanager
    def record(self):
        with original(self) as directory:
            paths.append(directory)
            yield directory

    monkeypatch.setattr(_ObservationCodex, 'execution_directory', record)
    result = asyncio.run(SuggestionPlanner(Settings()).plan({'width': 100, 'height': 80, 'png': b'fixture'}, 'codex'))
    assert result['kind'] == 'question'
    assert paths and all(not path.exists() for path in paths)


def test_cancelled_codex_analysis_removes_temp_inputs_and_releases_shared_lock(monkeypatch):
    async def run():
        helper = Path(__file__).with_name('fake_proactive_codex.py').resolve()
        monkeypatch.setattr(_ObservationCodex, 'command_prefix', lambda self: [sys.executable, str(helper)])
        monkeypatch.setenv('PROACTIVE_FIXTURE_SLOW', 'true')
        began, paths = asyncio.Event(), []
        original = _ObservationCodex._events

        async def record(self, prompt, workspace):
            paths.append(workspace)
            async with aclosing(original(self, prompt, workspace)) as stream:
                async for event in stream:
                    if event['type'] == 'status':
                        began.set()
                    yield event

        monkeypatch.setattr(_ObservationCodex, '_events', record)
        planner = SuggestionPlanner(Settings())
        clock = Clock()
        assistant = ProactiveAssistant(Settings(), planner=planner, clock=clock, random=lambda: 0,
            eligible=lambda: True, available=lambda: (True, ''),
            capture=lambda: {'width': 100, 'height': 80, 'png': b'fixture'})
        assistant.set_enabled(True)
        clock.advance()
        task = asyncio.create_task(assistant.tick())
        await asyncio.wait_for(began.wait(), 5)
        assert paths and (paths[0] / 'screen.png').exists()
        assistant.interrupt()
        assert await asyncio.wait_for(task, 5) is None
        assert not planner.lock.locked() and all(not path.exists() for path in paths)
        await assistant.close()

    asyncio.run(run())


def test_repeated_off_interrupt_close_do_not_cancel_asynchronous_cleanup_twice():
    async def run():
        assistant, clock, planner, _ = fixture()
        began, cleaning, finish, cleaned = (asyncio.Event() for _ in range(4))

        async def plan(*args):
            began.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaning.set()
                await finish.wait()
                cleaned.set()

        planner.plan = plan
        assistant.set_enabled(True)
        await assistant.start()
        clock.advance()
        analysis = asyncio.create_task(assistant.tick())
        await began.wait()
        assistant.set_enabled(False)
        await cleaning.wait()
        assistant.interrupt()
        preempt = asyncio.create_task(assistant.interrupt_and_wait())
        close = asyncio.create_task(assistant.close())
        await asyncio.sleep(0)
        assert not cleaned.is_set() and not preempt.done() and not close.done()
        finish.set()
        await asyncio.wait_for(asyncio.gather(analysis, preempt, close), 2)
        assert cleaned.is_set() and assistant.scheduler.done()
        assert not assistant.events()

    asyncio.run(run())


def test_cancelled_user_preemption_propagates_without_stopping_owned_cleanup_twice():
    async def run():
        assistant, clock, planner, _ = fixture()
        began, cleaning, finish = (asyncio.Event() for _ in range(3))

        async def plan(*args):
            began.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaning.set()
                await finish.wait()

        planner.plan = plan
        assistant.set_enabled(True)
        clock.advance()
        analysis = asyncio.create_task(assistant.tick())
        await began.wait()
        preempt = asyncio.create_task(assistant.interrupt_and_wait())
        await cleaning.wait()
        preempt.cancel()
        with pytest.raises(asyncio.CancelledError):
            await preempt
        assert not analysis.done()
        finish.set()
        assert await asyncio.wait_for(analysis, 2) is None
        await assistant.close()

    asyncio.run(run())


def test_changed_foreground_window_during_capture_is_discarded_before_upload():
    capture, driver = observer()
    original = capture.window_info()
    screens = iter([original, {**original, 'title': '密码管理器'}])
    capture.window_info = lambda: next(screens)
    with pytest.raises(ObservationSkipped, match='变化'):
        capture.capture()
    assert len(driver.regions) == 1


def test_rejected_api_stream_is_closed_before_planner_returns(monkeypatch):
    closed = []

    async def stream(*args):
        try:
            yield {'delta': {'tool_calls': [{'function': {'name': 'unsafe'}}]}}
        finally:
            closed.append(True)

    monkeypatch.setattr('assistant.proactive.provider_events', stream)

    async def run():
        with pytest.raises(ValueError, match='不能调用工具'):
            await SuggestionPlanner(Settings()).plan({'width': 80, 'height': 80, 'png': b'image'}, 'api')
        assert closed == [True]

    asyncio.run(run())
