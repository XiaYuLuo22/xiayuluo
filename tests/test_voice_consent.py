import asyncio
from contextlib import aclosing
from pathlib import Path
import sys

import pytest

from assistant.voice_consent import VoiceConsents
from conftest import events
from test_mobile import mobile_client, pair, phone


def engine_fixture(client):
    client.app.state.codex_chat.command_prefix = lambda: [sys.executable, str(Path(__file__).parent / 'fake_codex.py')]


def test_once_authorized_voice_is_codex_only_and_ordinary_chat_still_confirms(client_factory):
    client, api_calls = client_factory()
    engine_fixture(client)
    assert client.post('/api/voice/sessions', json={'confirm': False}).status_code == 400
    lease = client.post('/api/voice/sessions', json={'confirm': True}).json()
    body = {**lease, 'message': '你好'}
    result = events(client.post('/api/chat', json=body))
    assert result[-1]['type'] == 'done'
    assert any('chat fixture: 你好' in packet.get('text', '') for packet in result)
    assert not any(packet['type'] == 'action' for packet in result)
    assert not api_calls
    for extra in ({'mode': 'codex'}, {'provider': 'api'}, {'screenshot_id': 'fixture'}):
        assert client.post('/api/chat', json={**body, **extra}).status_code == 403
    other = client.post('/api/sessions').json()['id']
    assert client.post('/api/chat', json={**body, 'session_id': other}).status_code == 403
    normal = events(client.post('/api/chat', json={'session_id': other, 'message': '普通文字问答', 'provider': 'codex'}))
    assert any(item['type'] == 'action' for item in normal)
    assert not api_calls
    assert client.post('/api/voice/revoke', json={'voice_token': lease['voice_token']}).status_code == 200
    assert client.post('/api/chat', json=body).status_code == 403


def test_voice_lease_is_device_bound_and_cannot_be_spoofed_or_approve_tools(client_factory, tmp_path):
    client = mobile_client(client_factory, tmp_path)
    engine_fixture(client)
    one, two = pair(client, 'one'), pair(client, 'two')
    base = 'https://phone.example/mobile/v1'
    first = phone(client, one['token']); second = phone(client, two['token'])
    lease = client.post(base + '/voice/sessions', headers=first, json={'confirm': True}).json()
    body = {**lease, 'message': '你好'}
    assert client.post(base + '/chat', headers=second, json=body).status_code == 403
    assert client.post('/api/chat', json=body).status_code == 403
    forged = {**second, 'X-Xiayuluo-Voice-Device': 'phone:' + one['device']['id']}
    assert client.post(base + '/chat', headers=forged, json=body).status_code == 403
    assert client.post(base + '/voice/heartbeat', headers=first, json={'voice_token': lease['voice_token']}).status_code == 200
    result = events(client.post(base + '/chat', headers=first, json=body))
    assert result[-1]['type'] == 'done'
    assert any('chat fixture: 你好' in packet.get('text', '') for packet in result)
    assert client.post(base + '/actions/fake/approve', headers=first).status_code == 403
    assert client.delete('/api/devices/' + one['device']['id']).status_code == 200
    assert not client.app.state.voice_consents.valid(lease['voice_token'], 'phone:' + one['device']['id'])


def test_voice_lease_is_hashed_expires_and_start_replaces_previous(store):
    now = [100.0]
    consents = VoiceConsents(store, clock=lambda: now[0])
    first = consents.start('desktop')
    assert first['voice_token'] not in str(consents.items)
    assert consents.valid(first['voice_token'], 'desktop', first['session_id'])
    second = consents.start('desktop')
    assert not consents.valid(first['voice_token'], 'desktop')
    now[0] += 100
    consents.renew(second['voice_token'], 'desktop')
    now[0] += 100
    assert consents.valid(second['voice_token'], 'desktop')
    now[0] += 51
    assert not consents.valid(second['voice_token'], 'desktop')
    with pytest.raises(ValueError): consents.renew(second['voice_token'], 'desktop')


@pytest.mark.asyncio
async def test_revoking_stops_slow_inflight_chat_without_waiting_for_text(store):
    consents = VoiceConsents(store)
    lease = consents.start('desktop')
    closed = asyncio.Event()
    class Slow:
        async def events(self, prompt):
            try:
                await asyncio.Event().wait()
                yield {'type': 'text', 'text': 'never'}
            finally: closed.set()
    async with aclosing(consents.events(Slow(), 'fixture', lease['voice_token'], 'desktop', lease['session_id'])) as stream:
        task = asyncio.create_task(anext(stream))
        await asyncio.sleep(.01)
        consents.revoke(lease['voice_token'], 'desktop')
        with pytest.raises(ValueError, match='停止'):
            await asyncio.wait_for(task, timeout=2)
    assert closed.is_set()
