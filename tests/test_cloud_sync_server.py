"""Self-hosted protocol checks only; no real server deployment or private data."""
import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from cloud_sync.server import create_app


ADMIN = 'test-only-admin-' + 'x' * 40
WORKSPACE = 'a' * 32
STAMP = '2026-10-26T00:00:00+00:00'


def event(identifier='event-1', *, entity='session', entity_id='session-1', revision=0,
          operation='upsert', payload=None):
    if payload is None:
        payload = {'title': 'Test session', 'created_at': '2026-10-26T00:00:00+00:00'} if operation == 'upsert' else {}
    return {'event_id': identifier, 'entity': entity, 'entity_id': entity_id,
            'operation': operation, 'base_revision': revision, 'payload': payload}


@pytest.fixture
def cloud(tmp_path):
    clock = [1000.0]
    app = create_app(tmp_path / 'persistent' / 'cloud.sqlite3', ADMIN, clock=lambda: clock[0])
    with TestClient(app, base_url='https://testserver') as client:
        def enroll(kind='phone', workspace=None, name='Private device'):
            body = {'name': name, 'kind': kind}
            if kind == 'desktop':
                body['workspace_id'] = workspace or WORKSPACE
            response = client.post('/sync/v1/devices', json=body, headers={'Authorization': 'Bearer ' + ADMIN})
            assert response.status_code == 200, response.text
            return response.json()
        phone = enroll()
        desktop = enroll('desktop')
        client.headers['Authorization'] = 'Bearer ' + phone['token']
        yield client, app.state.store, phone, desktop, enroll, clock


def test_tls_host_origin_admin_and_device_boundaries(cloud):
    client, store, phone, desktop, _, _ = cloud
    assert client.get('http://testserver/healthz').json() == {'ok': True}
    assert client.get('http://testserver/sync/v1/events').status_code == 426
    assert client.get('https://untrusted.example/sync/v1/events').status_code == 400
    assert client.get('/sync/v1/events', headers={'Origin': 'https://evil.example'}).status_code == 403
    assert client.get('/sync/v1/events', headers={'Authorization': 'Bearer ' + ADMIN}).status_code == 401
    assert client.get('/sync/v1/devices').status_code == 401
    assert client.post('/sync/v1/devices', json={'name': 'bad', 'kind': 'desktop'},
                       headers={'Authorization': 'Bearer ' + ADMIN}).status_code == 422
    devices = client.get('/sync/v1/devices', headers={'Authorization': 'Bearer ' + ADMIN}).json()['devices']
    assert 'token' not in json.dumps(devices)
    row = store.db.execute('SELECT token_hash FROM devices WHERE id=?', (phone['id'],)).fetchone()
    assert row[0] != phone['token'] and len(row[0]) == 64
    assert client.get('/openapi.json').status_code == 404


def test_device_identity_and_registered_workspaces_reveal_no_token_or_online_claim(cloud):
    client, _, phone, desktop, _, _ = cloud
    current = client.get('/sync/v1/device').json()['device']
    assert current['id'] == phone['id'] and current['kind'] == 'phone'
    assert not any(key in current for key in ('token', 'token_hash', 'device_token'))
    workspaces = client.get('/sync/v1/workspaces').json()['workspaces']
    assert workspaces == [{'id': WORKSPACE, 'name': 'Private device', 'status': 'registered', 'online': None}]
    client.delete('/sync/v1/devices/' + desktop['id'], headers={'Authorization': 'Bearer ' + ADMIN})
    assert client.get('/sync/v1/workspaces').json()['workspaces'] == []
    assert client.get('/sync/v1/device', headers={'Authorization': ''}).status_code == 401


def test_atomic_idempotent_incremental_events_and_optimistic_conflict(cloud):
    client, store, phone, desktop, _, _ = cloud
    first = event()
    pushed = client.post('/sync/v1/events', json={'events': [first]})
    assert pushed.status_code == 200
    accepted = pushed.json()['events'][0]
    assert accepted == {'cursor': 1, 'revision': 1, **first}
    assert client.post('/sync/v1/events', json={'events': [first]}).json() == pushed.json()
    conflicting = event('new-event', revision=0, payload={'title': 'stale edit', 'created_at': STAMP})
    conflict = client.post('/sync/v1/events', json={'events': [event('second', entity_id='other-session'), conflicting]})
    assert conflict.status_code == 409 and conflict.json()['detail']['current_revision'] == 1
    # Whole batch rolls back; failed writes never advance the authoritative cursor.
    page = client.get('/sync/v1/events').json()
    assert page == {'events': [accepted], 'cursor': 1, 'more': False}
    assert client.get('/sync/v1/events?after=2').status_code == 409
    corrected = event('new-event', revision=1, payload={'title': 'new edit', 'created_at': STAMP})
    assert client.post('/sync/v1/events', json={'events': [corrected]}).json()['events'][0]['revision'] == 2
    assert client.get('/sync/v1/events?limit=1').json()['more']
    assert client.get('/sync/v1/events?after=1&limit=1').json()['cursor'] == 2
    other_headers = {'Authorization': 'Bearer ' + desktop['token']}
    assert client.post('/sync/v1/events', json={'events': [first]}, headers=other_headers).status_code == 409
    assert client.get('/sync/v1/events', headers=other_headers).json()['cursor'] == 2


def test_tombstones_do_not_resurrect_deleted_sessions_or_accept_orphan_messages(cloud):
    client, *_ = cloud
    assert client.post('/sync/v1/events', json={'events': [event()]}).status_code == 200
    deletion = event('delete-session', revision=1, operation='delete')
    assert client.post('/sync/v1/events', json={'events': [deletion]}).status_code == 200
    assert client.post('/sync/v1/events', json={'events': [event('resurrection', revision=2)]}).status_code == 409
    message = event('message', entity='message', entity_id='message-1', payload={
        'session_id': 'session-1', 'role': 'user', 'content': 'Offline text', 'created_at': STAMP})
    assert client.post('/sync/v1/events', json={'events': [message]}).status_code == 409
    assert client.get('/sync/v1/events?after=1').json()['events'][0]['operation'] == 'delete'


@pytest.mark.parametrize('extra', ['api_key', 'codex_credentials', 'screenshot', 'training_data'])
def test_closed_text_schema_rejects_secret_or_binary_fields(cloud, extra):
    client, *_ = cloud
    value = event()
    value['payload'][extra] = 'not permitted'
    assert client.post('/sync/v1/events', json={'events': [value]}).status_code == 422
    value = event()
    value[extra] = 'not permitted'
    assert client.post('/sync/v1/events', json={'events': [value]}).status_code == 422


@pytest.mark.parametrize('tier', ['temporary', 'working', 'unknown'])
def test_only_persistent_memory_tiers_are_allowed(cloud, tier):
    client, *_ = cloud
    value = event('memory', entity='memory', payload={'tier': tier, 'kind': 'fact', 'content': 'Fact',
        'created_at': STAMP, 'accessed_at': STAMP, 'expires_at': None, 'uses': 0, 'source_session': None})
    assert client.post('/sync/v1/events', json={'events': [value]}).status_code == 422


@pytest.mark.parametrize('role', ['system', 'tool', 'developer'])
def test_cloud_cannot_inject_privileged_history_roles(cloud, role):
    client, *_ = cloud
    value = event('message', entity='message', payload={
        'session_id': 'session-1', 'role': role, 'content': 'untrusted text', 'created_at': STAMP})
    assert client.post('/sync/v1/events', json={'events': [value]}).status_code == 422


def test_all_persistent_memory_fields_roundtrip(cloud):
    client, *_ = cloud
    values = [event('memory-' + tier, entity='memory', entity_id=tier, payload={
        'tier': tier, 'kind': 'episode', 'content': 'Private demonstration fixture', 'created_at': STAMP,
        'accessed_at': STAMP, 'expires_at': None, 'uses': 3, 'source_session': None})
        for tier in ('first', 'second', 'permanent')]
    assert client.post('/sync/v1/events', json={'events': values}).status_code == 200
    page = client.get('/sync/v1/events').json()
    assert [value['payload'] for value in page['events']] == [value['payload'] for value in values]


def submit(client, task_id='task-1'):
    assert client.post('/sync/v1/events', json={'events': [event('task-parent-session')]}).status_code == 200
    data = {'id': task_id, 'workspace_id': WORKSPACE, 'session_id': 'session-1', 'prompt': 'Inspect this workspace.'}
    response = client.post('/sync/v1/tasks', json=data)
    assert response.status_code == 200, response.text
    return data, response.json()


def test_missing_or_deleted_session_cannot_become_a_computer_task(cloud):
    client, _, _, desktop, _, _ = cloud
    body = {'id': 'orphan', 'workspace_id': WORKSPACE, 'session_id': 'session-1', 'prompt': 'explicit task'}
    assert client.post('/sync/v1/tasks', json=body).status_code == 409
    submit(client)
    headers = {'Authorization': 'Bearer ' + desktop['token']}
    claimed = client.post('/sync/v1/tasks/claim', json={'workspace_id': WORKSPACE}, headers=headers).json()
    lease = {'lease_token': claimed['lease_token']}
    assert client.post('/sync/v1/tasks/task-1/start', json=lease, headers=headers).status_code == 200
    deleted = event('delete-parent', revision=1, operation='delete')
    assert client.post('/sync/v1/events', json={'events': [deleted]}).status_code == 200
    assert client.get('/sync/v1/tasks/task-1').json()['task']['status'] == 'cancelled'
    assert client.post('/sync/v1/tasks/task-1/heartbeat', json=lease, headers=headers).status_code == 409
    assert client.post('/sync/v1/tasks', json={**body, 'id': 'cannot-revive'}).status_code == 409
    assert client.post('/sync/v1/tasks/claim', json={'workspace_id': WORKSPACE}, headers=headers).json() == {'task': None}


def test_task_queue_binding_fencing_and_result_visibility(cloud):
    client, store, phone, desktop, enroll, clock = cloud
    data, receipt = submit(client)
    assert receipt['task']['status'] == 'queued'
    assert client.post('/sync/v1/tasks', json=data).json() == receipt
    assert client.post('/sync/v1/tasks', json={**data, 'prompt': 'different'}).status_code == 409
    assert client.post('/sync/v1/tasks/claim', json={'workspace_id': WORKSPACE}).status_code == 403
    second_phone = enroll(name='another phone')
    assert client.get('/sync/v1/tasks/task-1', headers={'Authorization': 'Bearer ' + second_phone['token']}).status_code == 404
    worker_headers = {'Authorization': 'Bearer ' + desktop['token']}
    assert client.post('/sync/v1/tasks/claim', json={'workspace_id': 'wrong'}, headers=worker_headers).status_code == 403
    claimed = client.post('/sync/v1/tasks/claim', json={'workspace_id': WORKSPACE}, headers=worker_headers).json()
    assert claimed['task']['id'] == 'task-1' and claimed['task']['status'] == 'leased'
    assert client.post('/sync/v1/tasks/claim', json={'workspace_id': WORKSPACE}, headers=worker_headers).json() == {'task': None}
    lease = {'lease_token': claimed['lease_token']}
    assert client.post('/sync/v1/tasks/task-1/start', json={'lease_token': 'bad' * 20}, headers=worker_headers).status_code == 409
    assert client.post('/sync/v1/tasks/task-1/result', json={**lease, 'status': 'succeeded', 'result': 'done'}, headers=worker_headers).status_code == 409
    assert client.post('/sync/v1/tasks/task-1/start', json=lease, headers=worker_headers).json()['task']['status'] == 'running'
    clock[0] += 50
    assert client.post('/sync/v1/tasks/task-1/heartbeat', json=lease, headers=worker_headers).json()['task']['lease_expires_at'] == 1170
    result = client.post('/sync/v1/tasks/task-1/result', json={**lease, 'status': 'succeeded', 'result': 'Read-only report'}, headers=worker_headers)
    assert result.status_code == 200 and result.json()['task']['status'] == 'succeeded'
    assert client.get('/sync/v1/tasks/task-1').json()['task']['result'] == 'Read-only report'
    # A lost HTTPS response can be retried without modifying a finished result.
    repeated = client.post('/sync/v1/tasks/task-1/result', json={**lease, 'status': 'succeeded', 'result': 'Read-only report'}, headers=worker_headers)
    assert repeated.json() == result.json()
    assert client.post('/sync/v1/tasks/task-1/result', json={**lease, 'status': 'succeeded', 'result': 'overwrite'}, headers=worker_headers).status_code == 409


@pytest.mark.parametrize('started', [False, True])
def test_expired_claims_are_not_replayed_or_acknowledged(cloud, started):
    client, store, phone, desktop, enroll, clock = cloud
    submit(client)
    headers = {'Authorization': 'Bearer ' + desktop['token']}
    claimed = client.post('/sync/v1/tasks/claim', json={'workspace_id': WORKSPACE}, headers=headers).json()
    lease = {'lease_token': claimed['lease_token']}
    if started:
        assert client.post('/sync/v1/tasks/task-1/start', json=lease, headers=headers).status_code == 200
    clock[0] += 130
    assert client.post('/sync/v1/tasks/task-1/start', json=lease, headers=headers).status_code == 409
    assert client.post('/sync/v1/tasks/task-1/result', json={**lease, 'status': 'succeeded', 'result': 'stale'}, headers=headers).status_code == 409
    assert client.post('/sync/v1/tasks/claim', json={'workspace_id': WORKSPACE}, headers=headers).json() == {'task': None}
    assert client.get('/sync/v1/tasks/task-1').json()['task']['status'] == 'interrupted'


def test_revocation_invalidates_tokens_and_inflight_lease(cloud):
    client, store, phone, desktop, _, _ = cloud
    submit(client)
    headers = {'Authorization': 'Bearer ' + desktop['token']}
    claimed = client.post('/sync/v1/tasks/claim', json={'workspace_id': WORKSPACE}, headers=headers).json()
    assert client.delete('/sync/v1/devices/' + desktop['id'], headers={'Authorization': 'Bearer ' + ADMIN}).status_code == 200
    assert client.get('/sync/v1/events', headers=headers).status_code == 401
    assert client.post('/sync/v1/tasks/task-1/start', json={'lease_token': claimed['lease_token']}, headers=headers).status_code == 401
    assert client.get('/sync/v1/tasks/task-1').json()['task']['status'] == 'interrupted'
    assert client.delete('/sync/v1/devices/' + phone['id'], headers={'Authorization': 'Bearer ' + ADMIN}).status_code == 200
    assert client.get('/sync/v1/events').status_code == 401


def test_requests_are_bounded_and_do_not_accept_arbitrary_tools(cloud):
    client, *_ = cloud
    assert client.post('/sync/v1/events', content=b'x' * (1024 * 1024 + 1)).status_code == 413
    assert client.post('/sync/v1/events', json={'events': []}).status_code == 422
    assert client.get('/sync/v1/events?limit=1000').status_code == 422
    assert client.post('/sync/v1/tasks', json={'id': 'task', 'workspace_id': WORKSPACE, 'session_id': 'session',
                                             'prompt': 'text', 'tool': 'desktop_mouse'}).status_code == 422


def test_store_persists_events_and_tasks_without_tokens_in_database(tmp_path):
    database = tmp_path / 'persistent.sqlite3'
    app = create_app(database, ADMIN)
    with TestClient(app, base_url='https://testserver') as client:
        phone = client.post('/sync/v1/devices', json={'name': 'phone', 'kind': 'phone'},
                            headers={'Authorization': 'Bearer ' + ADMIN}).json()
        client.headers['Authorization'] = 'Bearer ' + phone['token']
        assert client.post('/sync/v1/events', json={'events': [event()]}).status_code == 200
    restarted = create_app(database, ADMIN)
    with TestClient(restarted, base_url='https://testserver') as client:
        client.headers['Authorization'] = 'Bearer ' + phone['token']
        assert client.get('/sync/v1/events').json()['cursor'] == 1
    assert phone['token'].encode() not in database.read_bytes()
    assert ADMIN.encode() not in database.read_bytes()


@pytest.mark.parametrize('token', ['', 'short', 'x' * 31, 'x' * 33 + '\n'])
def test_no_default_or_invalid_admin_credentials(tmp_path, token):
    with pytest.raises(ValueError):
        create_app(tmp_path / 'db.sqlite3', token)


def test_explicit_host_and_persistent_path_are_required(tmp_path):
    with pytest.raises(ValueError):
        create_app(tmp_path / 'db.sqlite3', ADMIN, allowed_hosts=['*'])
    with pytest.raises(ValueError):
        create_app('relative.sqlite3', ADMIN)


def test_database_is_private_and_link_paths_are_rejected(tmp_path):
    import os
    database = tmp_path / 'private.sqlite3'
    app = create_app(database, ADMIN)
    app.state.store.close()
    if os.name == 'posix':
        assert database.stat().st_mode & 0o077 == 0
        database.chmod(0o644)
        with pytest.raises(ValueError, match='permissions'):
            create_app(database, ADMIN)
        database.chmod(0o600)
    linked = tmp_path / 'link.sqlite3'
    try:
        linked.symlink_to(database)
    except OSError:
        pytest.skip('Symlinks unavailable in this environment')
    with pytest.raises(ValueError, match='links'):
        create_app(linked, ADMIN)


def test_duplicate_event_ids_and_sensitive_validation_values_are_not_echoed(cloud):
    client, *_ = cloud
    assert client.post('/sync/v1/events', json={'events': [event(), event()]}).status_code == 422
    response = client.post('/sync/v1/events', json={'events': [event()], 'api_key': 'PRIVATE-TEST-VALUE'})
    assert response.status_code == 422 and 'PRIVATE-TEST-VALUE' not in response.text


def test_large_event_pages_are_bounded_without_skipping_any_cursor(cloud):
    client, *_ = cloud
    assert client.post('/sync/v1/events', json={'events': [event()]}).status_code == 200
    for index in range(25):
        data = event('large-' + str(index), entity='message', entity_id='message-' + str(index), payload={
            'session_id': 'session-1', 'role': 'user', 'content': 'x' * 60000, 'created_at': STAMP})
        assert client.post('/sync/v1/events', json={'events': [data]}).status_code == 200
    first = client.get('/sync/v1/events')
    assert len(first.content) < 1024 * 1024 and first.json()['more']
    second = client.get('/sync/v1/events?after=' + str(first.json()['cursor']))
    cursors = [value['cursor'] for value in first.json()['events'] + second.json()['events']]
    assert cursors == list(range(1, 27)) and not second.json()['more']


def test_payload_byte_limit_matches_devices_even_for_multibyte_text(cloud):
    client, *_ = cloud
    large = event('multibyte', entity='message', payload={
        'session_id': 'session-1', 'role': 'user', 'content': '鱼' * 30000, 'created_at': STAMP})
    assert client.post('/sync/v1/events', json={'events': [large]}).status_code == 422


def test_only_one_of_two_server_connections_can_claim_a_task(cloud, tmp_path):
    client, store, phone, desktop, _, _ = cloud
    submit(client)
    from cloud_sync.store import Store
    # Separate server processes use separate SQLite connections; the database
    # transaction, not a Python lock alone, owns the claim.
    database = store.db.execute('PRAGMA database_list').fetchone()['file']
    other = Store(database, clock=store.clock)
    device = store.authenticate(desktop['token'])
    try:
        first = store.claim(device, WORKSPACE)
        assert first['task']['id'] == 'task-1'
        assert other.claim(device, WORKSPACE) == {'task': None}
    finally:
        other.close()


@pytest.mark.parametrize('stamp', ['now', '', '2026-10-26T00:00:00', '9999-99-99T00:00:00Z'])
def test_invalid_or_naive_timestamps_cannot_break_memory_retrieval(cloud, stamp):
    client, *_ = cloud
    value = event('memory', entity='memory', entity_id='memory', payload={
        'tier': 'permanent', 'kind': 'fact', 'content': 'Test fact', 'created_at': STAMP,
        'accessed_at': stamp, 'expires_at': None, 'uses': 0, 'source_session': None})
    assert client.post('/sync/v1/events', json={'events': [value]}).status_code == 422
