import asyncio
from contextlib import aclosing
from pathlib import Path
import sys
import time

from PIL import Image
import pytest

from assistant.config import Settings
from assistant.tools import ClickArguments, Codex, Desktop
from conftest import events


class FakeDesktop:
    def __init__(self):
        self.image = Image.new('RGB', (200, 120), 'white')
        self.clicks = []

    def screenshot(self):
        return self.image.copy()

    def size(self):
        return self.image.size

    def click(self, x, y, button):
        self.clicks.append((x, y, button))


def desktop():
    tool = Desktop(False)
    tool.enabled = True
    tool.driver = FakeDesktop()
    return tool


def test_click_requires_fresh_unchanged_target():
    tool = desktop()
    snapshot = tool.capture()
    args = ClickArguments(x=30, y=40, screenshot_id=snapshot['id'])
    tool.click(args)
    assert tool.driver.clicks == [(30, 40, 'left')]
    with pytest.raises(ValueError, match='过期'):
        tool.click(args)
    changed = tool.capture()
    tool.driver.image.putpixel((30, 40), (0, 0, 0))
    with pytest.raises(ValueError, match='变化'):
        tool.click(ClickArguments(x=30, y=40, screenshot_id=changed['id']))
    assert len(tool.driver.clicks) == 1


def test_out_of_bounds_expired_and_scale_mismatch_are_rejected():
    tool = desktop()
    snapshot = tool.capture()
    with pytest.raises(ValueError, match='范围'):
        tool.validate_click(ClickArguments(x=200, y=0, screenshot_id=snapshot['id']))
    snapshot['created'] -= 121
    with pytest.raises(ValueError, match='过期'):
        tool.validate_click(ClickArguments(x=20, y=20, screenshot_id=snapshot['id']))
    tool.driver.size = lambda: (100, 60)
    with pytest.raises(ValueError, match='比例'):
        tool.capture()


def test_desktop_api_proposal_does_not_click(client_factory):
    client, _ = client_factory()
    tool = desktop()
    client.app.state.desktop.enabled = True
    client.app.state.desktop.driver = tool.driver
    session = client.post('/api/sessions').json()['id']
    screenshot = client.post('/api/desktop/screenshot').json()
    action = client.post('/api/actions', json={'session_id': session, 'tool': 'desktop_click',
        'arguments': {'x': 30, 'y': 40, 'screenshot_id': screenshot['id']}}).json()
    assert tool.driver.clicks == []
    assert events(client.post(f"/api/actions/{action['id']}/approve"))[-1]['type'] == 'done'
    assert tool.driver.clicks == [(30, 40, 'left')]


def make_codex(tmp_path):
    tool = Codex(Settings(codex_workspace=str(tmp_path)))
    tool.command_prefix = lambda: [sys.executable, str(Path(__file__).parent / 'fake_codex.py')]
    return tool


async def test_codex_real_subprocess_stream_and_failure(tmp_path):
    tool = make_codex(tmp_path)
    result = [event async for event in tool.events('a prompt with quotes " and $()')]
    assert result[0]['type'] == 'status'
    assert result[-1]['text'] == 'fixture: a prompt with quotes " and $()\n'
    with pytest.raises(ValueError, match='失败'):
        _ = [event async for event in tool.events('fail')]
    assert not tool.lock.locked()


async def test_codex_stop_closes_process_and_releases_lock(tmp_path):
    tool = make_codex(tmp_path)
    start = time.monotonic()
    async with aclosing(tool.events('slow')) as stream:
        assert (await anext(stream))['type'] == 'status'
    assert time.monotonic() - start < 6
    assert not tool.lock.locked()
    assert [item async for item in tool.events('restart')][-1]['text'] == 'fixture: restart\n'


async def test_codex_blocks_concurrent_task(tmp_path):
    tool = make_codex(tmp_path)
    async with aclosing(tool.events('slow')) as stream:
        await anext(stream)
        with pytest.raises(ValueError, match='正在执行'):
            await anext(tool.events('other'))
