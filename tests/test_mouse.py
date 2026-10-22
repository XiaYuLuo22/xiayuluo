import asyncio
from contextlib import contextmanager
import json
from pathlib import Path
import sys

import httpx
import pytest
from pydantic import ValidationError

from assistant.config import Settings
from assistant.mouse import MouseArguments, MouseControl, MousePlan, MousePlanner, _VisualCodex
from assistant.tools import Desktop
from conftest import model_stream
from test_tools import FakeDesktop


def controller():
    desktop = Desktop(False)
    desktop.enabled, desktop.driver = True, FakeDesktop()
    return MouseControl(desktop)


def plan(**extra):
    return {'action': 'click', 'x': 30, 'y': 40, 'scroll': 0, 'message': '定位目标。',
            'width': 200, 'height': 120, **extra}


def test_toggle_starts_off_and_old_snapshots_never_survive_reopening():
    mouse = controller()
    assert mouse.status()['enabled'] is False
    with pytest.raises(ValueError, match='关闭'):
        mouse.capture()
    mouse.set_enabled(True)
    frame = mouse.capture()
    args = mouse.proposal(plan(), frame)
    generation = mouse.begin()
    mouse.set_enabled(False)
    mouse.set_enabled(True)
    with pytest.raises(ValueError, match='改变'):
        mouse.check(generation)
    with pytest.raises(ValueError):
        mouse.execute(args)
    assert mouse.desktop.driver.clicks == []


@pytest.mark.parametrize('text', ['点击右上角的设置', '帮我点一下搜索框', '双击桌面文件夹',
                                 '把鼠标移动到左边按钮', '向下滚动三格', 'Please click the settings button',
                                 'Move the mouse to the blue button'])
def test_explicit_mouse_commands(text):
    assert MouseControl.is_request(text)


@pytest.mark.parametrize('text', ['你好', '不要点击设置', '别动鼠标', '如何点击这个按钮',
                                 '解释鼠标双击', '为什么点击没有效果？', '示例：点击设置',
                                 'Please do not click', 'How do I click?', '如果点击会怎样',
                                 '代码里的 click()', '他说“点击这里”'])
def test_discussion_and_negative_commands_do_not_capture(text):
    assert not MouseControl.is_request(text)


def test_quoted_button_label_is_an_instruction_but_discussion_is_not():
    assert MouseControl.is_request('请帮我点击“设置”按钮')
    assert not MouseControl.is_request('我正在研究点击事件')
    assert not MouseControl.is_request('他说点击这里')


@pytest.mark.parametrize('extra', [{'x': True}, {'x': 1.5}, {'scroll': 20}, {'scroll': 1},
                                 {'action': 'shell'}, {'extra': 'bad'}, {'width': 0}])
def test_strict_plan_rejects_unsafe_shapes(extra):
    with pytest.raises(ValidationError):
        MousePlan.model_validate(plan(**extra))


def test_one_use_target_rejects_changed_pixels_bounds_dimensions_and_expiry():
    mouse = controller()
    mouse.set_enabled(True)
    frame = mouse.capture()
    with pytest.raises(ValueError, match='尺寸'):
        mouse.proposal(plan(width=100), frame)
    with pytest.raises(ValueError, match='范围'):
        mouse.proposal(plan(x=200), frame)
    args = mouse.proposal(plan(), frame)
    mouse.desktop.driver.image.putpixel((30, 40), (0, 0, 0))
    with pytest.raises(ValueError, match='变化'):
        mouse.execute(args)
    mouse.desktop.driver.image.putpixel((30, 40), (255, 255, 255))
    frame['created'] -= 121
    with pytest.raises(ValueError, match='过期'):
        mouse.execute(args)
    frame = mouse.capture()
    args = mouse.proposal(plan(), frame)
    mouse.execute(args)
    with pytest.raises(ValueError, match='过期'):
        mouse.execute(args)
    assert mouse.desktop.driver.clicks == [(30, 40, 'left')]


@pytest.mark.parametrize('action,amount', [('click', 0), ('right_click', 0), ('double_click', 0),
                                        ('move', 0), ('scroll', -3)])
def test_mouse_actions_are_bounded_single_native_dispatch(action, amount):
    mouse = controller()
    driver = mouse.desktop.driver
    recorded = []
    driver.doubleClick = lambda x, y, interval: recorded.append(('double', x, y, interval))
    driver.moveTo = lambda x, y, duration: recorded.append(('move', x, y, duration))
    driver.scroll = lambda count, x, y: recorded.append(('scroll', x, y, count))
    mouse.set_enabled(True)
    frame = mouse.capture()
    result = mouse.execute(mouse.proposal(plan(action=action, scroll=amount), frame))
    assert '已执行' in result['message']
    assert len(recorded) + len(driver.clicks) == 1
    assert not mouse.desktop.snapshots


def test_failed_native_action_consumes_snapshot_no_automatic_replay():
    mouse = controller()
    mouse.set_enabled(True)
    args = mouse.proposal(plan(), mouse.capture())

    def fail(*args, **kwargs):
        raise RuntimeError('driver failed')

    mouse.desktop.driver.click = fail
    with pytest.raises(RuntimeError):
        mouse.execute(args)
    with pytest.raises(ValueError, match='过期'):
        mouse.execute(args)


def test_revocation_cancels_only_owned_planning_and_off_on_invalidates_generation():
    async def run():
        mouse = controller()
        mouse.set_enabled(True)
        generation = mouse.begin()
        started, cleaned = asyncio.Event(), asyncio.Event()
        unrelated = asyncio.create_task(asyncio.sleep(10))

        async def slow():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.set()

        pending = asyncio.create_task(mouse.run_plan(slow(), generation))
        await started.wait()
        await asyncio.to_thread(mouse.set_enabled, False)
        mouse.set_enabled(True)
        with pytest.raises(ValueError, match='改变'):
            await pending
        assert cleaned.is_set() and not mouse.tasks
        assert not unrelated.done()
        unrelated.cancel()
        with pytest.raises(asyncio.CancelledError):
            await unrelated

    asyncio.run(run())


def test_api_visual_plan_sends_only_one_frame_no_tools_and_requires_completion():
    mouse = controller()
    mouse.set_enabled(True)
    frame = mouse.capture()
    records = []

    def handler(request):
        records.append(json.loads(request.content))
        return httpx.Response(200, text=model_stream({'content': json.dumps(plan())}))

    result = asyncio.run(MousePlanner(Settings(api_key='fixture')).plan('点击设置', frame, 'api', httpx.MockTransport(handler)))
    assert result == plan()
    assert 'tools' not in records[0]
    content = records[0]['messages'][1]['content']
    assert len(content) == 2 and content[1]['image_url']['url'].startswith('data:image/png;base64,')


@pytest.mark.parametrize('stream', [model_stream({'content': 'not json'}),
    model_stream({'content': json.dumps(plan(width=100))}),
    model_stream({'content': json.dumps(plan(x=201))}),
    model_stream({'content': json.dumps(plan())}, finish=None),
    model_stream({'content': json.dumps(plan())}, finish='length'),
    model_stream({'tool_calls': [{'function': {'name': 'desktop_mouse'}}]})])
def test_invalid_or_truncated_visual_output_never_yields_action(stream):
    mouse = controller()
    mouse.set_enabled(True)
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=stream))
    with pytest.raises(ValueError):
        asyncio.run(MousePlanner(Settings(api_key='fixture')).plan('点击设置', mouse.capture(), 'api', transport))
    assert mouse.desktop.driver.clicks == []


def test_codex_visual_inputs_are_isolated_tool_free_and_removed(monkeypatch):
    mouse = controller()
    mouse.set_enabled(True)
    fixture = Path(__file__).with_name('fake_mouse_codex.py').resolve()
    monkeypatch.setattr(_VisualCodex, 'command_prefix', lambda self: [sys.executable, str(fixture)])
    monkeypatch.setenv('OPENAI_API_KEY', 'never-send-to-cli')
    monkeypatch.setenv('CODEX_API_KEY', 'never-send-to-cli')
    monkeypatch.setenv('ASSISTANT_MODEL_KEY', 'never-send-to-cli')
    original = _VisualCodex.execution_directory
    paths = []

    @contextmanager
    def capture_path(self):
        with original(self) as directory:
            paths.append(directory)
            yield directory

    monkeypatch.setattr(_VisualCodex, 'execution_directory', capture_path)
    result = asyncio.run(MousePlanner(Settings()).plan('点击设置', mouse.capture(), 'codex'))
    assert result['action'] == 'click'
    assert paths and all(not path.exists() for path in paths)
    assert mouse.desktop.driver.clicks == []  # A plan is not execution.


def test_cancelling_visual_cli_removes_its_images_and_releases_shared_lock(monkeypatch):
    async def run():
        mouse = controller()
        mouse.set_enabled(True)
        fixture = Path(__file__).with_name('fake_mouse_codex.py').resolve()
        monkeypatch.setattr(_VisualCodex, 'command_prefix', lambda self: [sys.executable, str(fixture)])
        started = asyncio.Event()
        paths = []
        original = _VisualCodex._events

        async def observe(self, prompt, workspace):
            from contextlib import aclosing
            paths.append(workspace)
            async with aclosing(original(self, prompt, workspace)) as stream:
                async for event in stream:
                    if event['type'] == 'status':
                        started.set()
                    yield event

        monkeypatch.setattr(_VisualCodex, '_events', observe)
        planner = MousePlanner(Settings())
        task = asyncio.create_task(mouse.run_plan(planner.plan('慢速点击设置', mouse.capture(), 'codex'), mouse.begin()))
        await asyncio.wait_for(started.wait(), 5)
        assert paths and (paths[0] / 'screen.png').exists()
        await asyncio.to_thread(mouse.set_enabled, False)
        with pytest.raises(ValueError, match='改变|关闭'):
            await asyncio.wait_for(task, 5)
        assert not planner.lock.locked()
        assert all(not path.exists() for path in paths)

    asyncio.run(run())


def test_arguments_forbid_unbounded_scroll_and_boolean_coordinates():
    for extra in ({'x': True}, {'scroll': 11, 'action': 'scroll'}, {'action': 'click', 'scroll': 1}):
        with pytest.raises(ValidationError):
            MouseArguments.model_validate({'action': 'click', 'x': 2, 'y': 3, 'scroll': 0,
                                          'screenshot_id': 'fixture', **extra})
