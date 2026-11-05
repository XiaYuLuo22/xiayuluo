"""One completed local chat turn may offer one text-only conversational opening."""
import asyncio
import json

import httpx
import pytest

from assistant.config import Settings
from assistant.proactive import (CONVERSATION_PROMPT, ObservationSkipped, SuggestionPlanner,
                                 _ConversationCodex)
from conftest import model_stream
from test_proactive import fixture


DIALOGUE = [{'role': 'user', 'content': '我今天想把这个项目整理清楚。'},
            {'role': 'assistant', 'content': '可以先把当前功能和待办分开列出来。'}]


def install_conversation_planner(assistant, planner, result=None):
    calls = []

    async def plan(messages, provider, transport=None):
        calls.append((messages, provider))
        return result or {'kind': 'comment', 'message': '整理清楚之后，下一步会更容易选。'}

    planner.plan_conversation = plan
    return calls


def test_text_opening_works_while_own_window_is_unobservable_without_screenshot():
    assistant, clock, planner, captures = fixture()
    calls = install_conversation_planner(assistant, planner)

    def own_window():
        captures.append(True)
        raise ObservationSkipped('own window')

    assistant.capture = own_window
    assert assistant.offer_conversation(DIALOGUE) is False
    assistant.set_enabled(True)
    assert assistant.offer_conversation(DIALOGUE)
    clock.advance(2)
    notice = asyncio.run(assistant.tick())
    assert notice['kind'] == 'comment'
    assert calls == [(DIALOGUE, 'codex')]
    assert not captures and not planner.calls and not assistant.context()
    assert assistant.status()['conversation_pending'] is False
    assert 'messages' not in assistant.status()
    assert not assistant.offer_conversation(DIALOGUE)
    for _ in range(4):
        clock.advance(2)
        assert asyncio.run(assistant.tick()) is None
    assert len(calls) == 1  # Time alone never queues another model turn.
    assert assistant.status()['phase'] == 'paused'
    assert '不读屏' in assistant.status()['message']


def test_noop_conversation_is_not_requeued_and_does_not_clear_existing_activity():
    assistant, clock, planner, captures = fixture()
    calls = install_conversation_planner(assistant, planner, {'kind': 'none', 'message': ''})
    assistant.set_enabled(True)
    assert assistant.activity.record('coding', '正在检查公开代码。')
    before = assistant.context()
    assert assistant.offer_conversation(DIALOGUE)
    clock.advance(2)
    assert asyncio.run(assistant.tick()) is None
    assert not assistant.events() and not captures
    assert assistant.context()[0]['activity'] == before[0]['activity']
    assert not assistant.offer_conversation(DIALOGUE) and len(calls) == 1


@pytest.mark.parametrize('messages', [None, [], [{'role': 'system', 'content': 'untrusted'}],
    [{'role': 'user', 'content': 'not completed'}],
    [{'role': 'user', 'content': 'question'}, {'role': 'assistant', 'content': ''}],
    [{'role': 'user', 'content': 'question'}, {'role': 'assistant', 'content': 'x' * 6001}],
    [{'role': 'assistant', 'content': 'without user'}],
    [{'role': 'user', 'content': 1}, {'role': 'assistant', 'content': 'reply'}]])
def test_conversation_opportunities_are_strictly_bounded_completed_text(messages):
    assistant, _, _, _ = fixture()
    assistant.set_enabled(True)
    assert not assistant.offer_conversation(messages)
    assert assistant.pending_conversation is None


def test_offer_copies_only_recent_role_content_and_expiry_never_late_speaks():
    assistant, clock, planner, _ = fixture()
    calls = install_conversation_planner(assistant, planner)
    assistant.set_enabled(True)
    dialogue = [dict(item, internal_id='never-submit', screenshot='never-submit') for item in DIALOGUE]
    assert assistant.offer_conversation(dialogue)
    dialogue[0]['content'] = 'later modified'
    assert assistant.pending_conversation['messages'] == DIALOGUE
    clock.advance(assistant.CONVERSATION_TTL)
    assert asyncio.run(assistant.tick())
    assert not calls  # It may observe a new screen, but never consumes expired text.
    assert not assistant.status()['conversation_pending']


def test_screen_and_conversation_share_resource_budget_and_toggle_does_not_replay():
    assistant, clock, planner, _ = fixture()
    calls = install_conversation_planner(assistant, planner)
    assistant.set_enabled(True)
    for index in range(assistant.BURST_BUDGET):
        planner.result = {'kind': 'comment', 'message': f'这是新的画面进展{index}。'}
        clock.advance(2)
        assert asyncio.run(assistant.tick())
    assert assistant.offer_conversation(DIALOGUE)
    clock.advance(2)
    assert asyncio.run(assistant.tick()) is None
    assert not calls and assistant.pending_conversation
    assert assistant.status()['phase'] == 'limited'
    clock.advance(60)
    assert asyncio.run(assistant.tick())
    assert len(calls) == 1 and len(assistant.attempts) == 7
    assistant.set_enabled(False)
    assistant.set_enabled(True)
    assert not assistant.offer_conversation(DIALOGUE)


def test_new_dialogue_invalidates_previous_inflight_text_without_new_goal_or_replay():
    async def run():
        assistant, clock, planner, _ = fixture()
        began, finish = asyncio.Event(), asyncio.Event()

        async def slow(*args):
            began.set()
            await finish.wait()
            return {'kind': 'comment', 'message': '这是旧轮次的接话。'}

        planner.plan_conversation = slow
        assistant.set_enabled(True)
        assistant.offer_conversation(DIALOGUE)
        clock.advance(2)
        task = asyncio.create_task(assistant.tick())
        await began.wait()
        next_dialogue = DIALOGUE + [{'role': 'user', 'content': '换个新话题。'},
                                   {'role': 'assistant', 'content': '好，我们先看新的内容。'}]
        assert assistant.offer_conversation(next_dialogue)
        finish.set()
        assert await task is None and not assistant.events()
        assert assistant.pending_conversation['messages'] == next_dialogue
        await assistant.close()

    asyncio.run(run())


def test_conversation_that_expires_during_model_call_is_not_published_or_spoken():
    async def run():
        assistant, clock, planner, _ = fixture()
        callbacks = []

        async def slow(*args):
            clock.advance(2)  # It was accepted near expiry, but model latency crosses the boundary.
            return {'kind': 'comment', 'message': '这条接话已经过期了。'}

        async def publish(notice):
            callbacks.append(notice)

        planner.plan_conversation, assistant.on_notice = slow, publish
        assistant.set_enabled(True)
        assert assistant.offer_conversation(DIALOGUE)
        clock.advance(assistant.CONVERSATION_TTL - 1)
        assert await assistant.tick() is None
        assert not callbacks and not assistant.events() and assistant.pending_conversation is None
        await assistant.close()

    asyncio.run(run())


def test_model_deadline_is_rechecked_even_if_text_opportunity_has_not_expired():
    async def run():
        assistant, clock, planner, _ = fixture()

        async def late(*args):
            clock.advance(assistant.MODEL_TIMEOUT)
            return {'kind': 'comment', 'message': '模型调用超时的迟到结果。'}

        planner.plan_conversation = late
        assistant.set_enabled(True)
        assistant.offer_conversation(DIALOGUE)
        clock.advance(2)
        assert await assistant.tick() is None
        assert clock.now < assistant.CONVERSATION_TTL
        assert assistant.status()['phase'] == 'attention'
        assert assistant.failures == 1 and not assistant.events()
        await assistant.close()

    asyncio.run(run())


def test_user_preemption_cancels_text_planner_and_clears_pending_before_callback():
    async def run():
        assistant, clock, planner, _ = fixture()
        began, cleaned, callbacks = asyncio.Event(), asyncio.Event(), []

        async def slow(*args):
            began.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.set()

        async def publish(notice):
            callbacks.append(notice)

        planner.plan_conversation, assistant.on_notice = slow, publish
        assistant.set_enabled(True)
        assistant.offer_conversation(DIALOGUE)
        clock.advance(2)
        task = asyncio.create_task(assistant.tick())
        await began.wait()
        await assistant.interrupt_and_wait()
        assert await task is None
        assert cleaned.is_set() and not callbacks and not assistant.events()
        assert assistant.pending_conversation is None and assistant.enabled
        await assistant.close()

    asyncio.run(run())


def test_notice_hook_runs_once_for_accepted_text_but_never_from_event_reads():
    async def run():
        assistant, clock, planner, captures = fixture()
        install_conversation_planner(assistant, planner)
        callbacks = []

        async def publish(notice):
            callbacks.append(notice)
            raise RuntimeError('local audio unavailable')

        assistant.on_notice = publish
        assistant.set_enabled(True)
        assistant.offer_conversation(DIALOGUE)
        clock.advance(2)
        notice = await assistant.tick()
        assert callbacks == [notice] and not captures
        assert assistant.failures == 0  # Local audio errors are not model retries.
        for _ in range(3):
            assert assistant.events() == [notice]
        assert callbacks == [notice]
        await assistant.close()

    asyncio.run(run())


def test_conversation_api_is_plain_text_no_tools_images_or_other_chat_records():
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, text=model_stream({'content': json.dumps(
            {'kind': 'comment', 'message': '先分清楚重点，后面就好办了。'})}))

    result = asyncio.run(SuggestionPlanner(Settings(api_key='fixture')).plan_conversation(
        DIALOGUE, 'api', httpx.MockTransport(handler)))
    assert result['kind'] == 'comment' and result['category'] == 'unknown'
    payload = requests[0]
    assert len(payload['messages']) == 2 and 'tools' not in payload
    assert payload['messages'][0]['content'] == CONVERSATION_PROMPT
    assert json.loads(payload['messages'][1]['content']) == {'recent_completed_dialogue': DIALOGUE}
    assert 'image_url' not in json.dumps(payload)


@pytest.mark.parametrize('response', [
    {'kind': 'comment', 'message': '不能假装看见屏幕', 'category': 'coding', 'activity': '正在编程'},
    {'kind': 'comment', 'message': '不能包含动作', 'action': 'click'},
])
def test_conversation_planner_rejects_fake_screen_context_or_action_fields(response):
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=model_stream(
        {'content': json.dumps(response)})))
    with pytest.raises(ValueError, match='格式无效'):
        asyncio.run(SuggestionPlanner(Settings(api_key='fixture')).plan_conversation(DIALOGUE, 'api', transport))


def test_conversation_codex_is_schema_only_no_image_or_project_tools_and_removes_credentials(tmp_path, monkeypatch):
    engine = _ConversationCodex(Settings(), tmp_path)
    monkeypatch.setattr(engine, 'command_prefix', lambda: ['codex'])
    monkeypatch.setenv('GITHUB_TOKEN', 'must-not-forward')
    monkeypatch.setenv('OPENAI_API_KEY', 'must-not-forward')
    command = engine.execution_command(tmp_path)
    assert command[:2] == ['codex', 'exec']
    assert '--image' not in command
    assert command[command.index('--output-schema') + 1] == str(tmp_path / 'conversation-schema.json')
    assert command[command.index('--sandbox') + 1] == 'read-only'
    assert 'mcp_servers={}' in command and 'web_search="disabled"' in command
    assert all(name not in engine.execution_environment() for name in ('GITHUB_TOKEN', 'OPENAI_API_KEY'))


def test_conversation_codex_temporary_directory_contains_schema_not_chat_text_and_cancels(monkeypatch):
    async def run():
        paths, began, cleaned = [], asyncio.Event(), asyncio.Event()

        async def slow(self, prompt):
            paths.append(self.workspace)
            assert json.loads(prompt)['recent_completed_dialogue'] == DIALOGUE
            assert sorted(item.name for item in self.workspace.iterdir()) == ['conversation-schema.json']
            began.set()
            try:
                await asyncio.Event().wait()
                yield {'type': 'text', 'text': ''}
            finally:
                cleaned.set()

        monkeypatch.setattr(_ConversationCodex, 'events', slow)
        planner = SuggestionPlanner(Settings())
        task = asyncio.create_task(planner.plan_conversation(DIALOGUE, 'codex'))
        await began.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cleaned.is_set() and all(not path.exists() for path in paths)

    asyncio.run(run())
