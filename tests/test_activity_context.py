import asyncio
import json

import pytest

from assistant.activity_context import ActivityContext
from assistant.proactive import ObservationSkipped
from test_proactive import fixture


def test_context_is_bounded_transient_and_has_no_images_titles_or_actions():
    clock = [0]
    context = ActivityContext(clock=lambda: clock[0])
    for index in range(20):
        assert context.record('coding', f'正在检查代码，第{index}步。')
        clock[0] += 1
    items = context.context()
    assert len(items) == 12 and items[-1]['age_seconds'] == 1
    assert items[0]['untrusted'] is True
    assert items[0]['confidence'] == 'limited-observation'
    assert not any(key in json.dumps(items) for key in ('png', 'title', 'command', 'image'))
    clock[0] += 900
    assert context.context() == []


@pytest.mark.parametrize('text', ['https://private.example', r'C:\Private\secret.txt',
    '/private/files/secret', 'person@example.com', 'sk-testsecret',
    '密码：abc', '123456789', 'a' * 241, ''])
def test_context_rejects_common_private_content_and_unbounded_text(text):
    assert not ActivityContext().record('coding', text)


def test_context_rejects_unknown_categories_and_duplicate_is_refreshed_not_appended():
    context = ActivityContext()
    assert not context.record('health', '不要推断身份或健康。')
    assert context.record('reading', '正在阅读一份说明。')
    assert context.record('reading', '正在阅读一份说明。')
    assert len(context.context()) == 1


def test_activity_summary_works_when_notice_probability_is_zero():
    assistant, clock, planner, captures = fixture(random=lambda: 1)
    planner.result = {'kind': 'none', 'message': '', 'category': 'coding', 'activity': '正在查看代码和测试结果。'}
    assistant.set_enabled(True)
    clock.advance(30)
    assert asyncio.run(assistant.tick()) is None
    assert len(captures) == len(planner.calls) == 1
    assert assistant.context()[0]['activity'] == planner.result['activity']
    assert not assistant.events()
    assistant.set_enabled(False)
    assert assistant.context() == []
    assistant.set_enabled(True)
    assert assistant.context() == []


def test_local_screen_change_detection_does_not_upload_every_frame():
    assistant, clock, planner, captures = fixture()
    planner.result = {'kind': 'none', 'message': '', 'category': 'writing', 'activity': '正在修改一段文档。'}
    assistant.set_enabled(True)
    for _ in range(5):
        clock.advance(30)
        asyncio.run(assistant.tick())
    assert len(captures) == 5 and len(planner.calls) == 2
    assert assistant.status()['interval_seconds'] == 30
    assert assistant.status()['analysis_interval_seconds'] == 120
    assert assistant.status()['activity_items'] == 1


def test_analysis_budget_applies_to_noop_and_survives_toggle():
    assistant, clock, planner, captures = fixture()
    planner.result = {'kind': 'none', 'message': ''}
    assistant.set_enabled(True)
    for _ in range(15):
        clock.advance(120)
        asyncio.run(assistant.tick())
    assert len(captures) == 15 and len(planner.calls) == 12
    assert assistant.status()['analysis_budget_exhausted'] is True
    assert assistant.status()['analyses_remaining_this_hour'] == 0
    assistant.set_enabled(False)
    assistant.set_enabled(True)
    clock.advance(120)
    asyncio.run(assistant.tick())
    assert len(planner.calls) == 12
    clock.advance(3600)
    asyncio.run(assistant.tick())
    assert len(planner.calls) == 13
    assert assistant.status()['analysis_budget_exhausted'] is False


def test_privacy_skip_clears_activity_and_returning_screen_can_be_analysed_again():
    assistant, clock, planner, captures = fixture()
    planner.result = {'kind': 'none', 'message': '', 'category': 'reading', 'activity': '正在阅读公开技术文档。'}
    assistant.capture = lambda: {'width': 200, 'height': 120, 'png': b'stable'}
    assistant.set_enabled(True)
    clock.advance(30)
    asyncio.run(assistant.tick())
    assert assistant.context()
    original = assistant.capture
    def private():
        raise ObservationSkipped('密码窗口')
    assistant.capture = private
    clock.advance(30)
    asyncio.run(assistant.tick())
    assert not assistant.context()
    assistant.capture = original
    clock.advance(120)
    asyncio.run(assistant.tick())
    assert assistant.context() and len(planner.calls) == 2


def test_model_cannot_turn_activity_into_tool_arguments_and_invalid_context_is_not_kept():
    assistant, clock, planner, captures = fixture()
    assistant.set_enabled(True)
    planner.result = {'kind': 'none', 'message': '', 'category': 'coding', 'activity': 'C:/Private/secret'}
    clock.advance(30)
    asyncio.run(assistant.tick())
    assert not assistant.context()
    planner.result = {'kind': 'none', 'message': '', 'category': 'coding', 'activity': '写代码', 'action': 'click'}
    clock.advance(120)
    asyncio.run(assistant.tick())
    assert not assistant.context() and assistant.status()['phase'] == 'attention'
