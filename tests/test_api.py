import json

import httpx

from assistant.config import Settings
from conftest import events, model_stream


def new_session(client):
    return client.post('/api/sessions').json()['id']


def test_chat_stream_and_memory_reaches_next_turn(client_factory):
    client, records = client_factory()
    session = new_session(client)
    client.post('/api/memories', json={"tier": "permanent", "content": "我喜欢简洁的中文回答"})
    response = client.post('/api/chat', json={"session_id": session, "message": "你好"})
    assert response.status_code == 200
    output = events(response)
    assert [event['text'] for event in output if event['type'] == 'text'] == ['你好', '，一起开始吧。']
    assert output[-1]['type'] == 'done'
    assert output[0]['items'][0]['tier'] == 'permanent'
    assert '我喜欢简洁的中文回答' in records[0]['messages'][0]['content']
    assert records[0]['stream'] is True
    assert not any(tool['function']['name'] == 'desktop_click' for tool in records[0]['tools'])
    history = client.get(f'/api/sessions/{session}').json()['messages']
    assert history[-1]['content'] == '你好，一起开始吧。'
    assert client.get(f'/api/status?session_id={session}').json()['memory']['temporary'] == 2


def test_model_action_has_no_effect_until_confirmed(client_factory):
    stream = model_stream({"tool_calls": [{"index": 0, "function": {"name": "remember", "arguments": '{"tier":"permanent",'}}]},
                          {"tool_calls": [{"index": 0, "function": {"arguments": '"kind":"fact","content":"我喜欢茶"}'}}]}, finish='tool_calls')
    client, _ = client_factory(stream)
    session = new_session(client)
    response = client.post('/api/chat', json={"session_id": session, "message": "永久记住我喜欢茶"})
    action = next(event['action'] for event in events(response) if event['type'] == 'action')
    assert client.get('/api/status').json()['memory']['permanent'] == 0
    approved = client.post(f"/api/actions/{action['id']}/approve")
    assert events(approved)[-1]['type'] == 'done'
    assert client.get('/api/status').json()['memory']['permanent'] == 1
    assert client.post(f"/api/actions/{action['id']}/approve").status_code == 404


def test_rejection_and_expiry_cannot_execute(client_factory):
    client, _ = client_factory()
    session = new_session(client)
    body = {"session_id": session, "tool": "remember", "arguments": {"tier": "second", "content": "喜欢茶"}}
    action = client.post('/api/actions', json=body).json()
    assert client.delete(f"/api/actions/{action['id']}").status_code == 200
    assert client.post(f"/api/actions/{action['id']}/approve").status_code == 404
    expired = client.post('/api/actions', json=body).json()
    client.app.state.actions.items[expired['id']]['created'] -= 121
    assert client.post(f"/api/actions/{expired['id']}/approve").status_code == 404
    assert client.get('/api/status').json()['memory']['second'] == 0


def test_credentials_never_appear_in_status_and_missing_provider_is_actionable(client_factory, tmp_path):
    client, _ = client_factory(settings=Settings(api_key='', data_dir=str(tmp_path)))
    session = new_session(client)
    assert client.post('/api/chat', json={"session_id": session, "message": "你好"}).status_code == 409
    status = client.get('/api/bootstrap')
    assert 'api_key' not in status.text
    assert client.get(f'/api/sessions/{session}').json()['messages'] == []
    assert client.post('/api/memories', json={"tier": "second", "content": "可离线保存"}).status_code == 200


def test_rejects_cross_origin_bad_host_and_missing_token(client_factory):
    client, _ = client_factory()
    assert client.post('/api/sessions', headers={'X-Assistant-Token': ''}).status_code == 403
    assert client.post('/api/sessions', headers={'Origin': 'https://unrelated.example'}).status_code == 403
    assert client.get('/api/bootstrap', headers={'Origin': 'null'}).status_code == 403
    assert client.get('/api/bootstrap', headers={'Host': 'unrelated.example'}).status_code == 400
    assert client.post('/api/sessions', headers={'Origin': 'http://localhost'}).status_code == 200


def test_truncated_stream_is_failure_not_saved_as_success(client_factory):
    client, _ = client_factory(model_stream({'content': '部分内容'}, finish=None))
    session = new_session(client)
    output = events(client.post('/api/chat', json={'session_id': session, 'message': '你好'}))
    assert output[-1]['type'] == 'error'
    assert not any(event['type'] == 'done' for event in output)
    history = client.get(f'/api/sessions/{session}').json()['messages']
    assert '回复未完成' in history[-1]['content']
    assert '部分内容' not in history[-1]['content']


def test_provider_error_is_not_leaked(client_factory):
    client, _ = client_factory(handler=lambda request: httpx.Response(401, text='secret-private-provider-response'))
    session = new_session(client)
    response = client.post('/api/chat', json={'session_id': session, 'message': '你好'})
    assert 'secret-private-provider-response' not in response.text
    assert 'HTTP 401' in response.text


def test_desktop_not_available_has_specific_error(client_factory):
    client, _ = client_factory()
    assert client.post('/api/desktop/screenshot').status_code == 409
    session = new_session(client)
    response = client.post('/api/actions', json={'session_id': session, 'tool': 'desktop_click',
        'arguments': {'x': 10, 'y': 10, 'screenshot_id': 'missing'}})
    assert response.status_code == 422


def test_unknown_tool_and_empty_memory_are_rejected(client_factory):
    client, _ = client_factory()
    session = new_session(client)
    assert client.post('/api/actions', json={'session_id': session, 'tool': 'shell', 'arguments': {}}).status_code == 422
    assert client.post('/api/memories', json={'tier': 'permanent', 'content': '   '}).status_code == 422
    assert client.post('/api/chat', json={'session_id': session, 'message': '   '}).status_code == 422


def test_clear_memory_and_sessions(client_factory):
    client, _ = client_factory()
    session = new_session(client)
    client.post('/api/chat', json={'session_id': session, 'message': '你好'})
    client.post('/api/memories', json={'tier': 'permanent', 'content': '重要事实'})
    assert client.delete('/api/memories').status_code == 200
    assert client.get('/api/sessions').json() == []
    assert client.get('/api/memories').json()['items'] == []


def test_model_cannot_call_unshared_desktop_tool(client_factory):
    stream = model_stream({'tool_calls': [{'index': 0, 'function': {'name': 'desktop_click',
        'arguments': json.dumps({'x': 1, 'y': 1, 'screenshot_id': 'fake'})}}]}, finish='tool_calls')
    client, _ = client_factory(stream)
    session = new_session(client)
    output = events(client.post('/api/chat', json={'session_id': session, 'message': '点击'}))
    assert output[-1]['type'] == 'error'
    assert not client.app.state.actions.items


def test_runtime_metadata_and_endpoint_do_not_expose_credentials(client_factory, tmp_path):
    settings = Settings(api_key='status-fixture-secret',
        api_base='https://fixture-user:fixture-password@api.example/v1?key=fixture-query-secret#fragment',
        data_dir=str(tmp_path))
    client, _ = client_factory(settings=settings)
    response = client.get('/api/status')
    status = response.json()
    assert status['runtime']['name'] == '夏鱼洛'
    assert status['runtime']['python']
    assert status['runtime']['uptime_seconds'] >= 0
    assert status['model']['endpoint'] == 'https://api.example/v1'
    assert 'fixture-' not in response.text
    assert client.get('/openapi.json').json()['info']['title'] == '夏鱼洛 · 本地助手'
