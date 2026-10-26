import pytest

from assistant.cloud_client import CloudConflict, CloudSyncError
from assistant.cloud_replica import CloudReplica


def event(number=1, *, entity_id='session1', operation='upsert', title='cloud title', revision=1):
    return {'event_id': f'event{number}', 'entity': 'session', 'entity_id': entity_id,
            'operation': operation, 'base_revision': revision - 1,
            'payload': {'title': title, 'created_at': '2026-01-01T00:00:00Z'} if operation == 'upsert' else {},
            'cursor': number, 'revision': revision}


def page(*events):
    return {'events': list(events), 'cursor': events[-1]['cursor'] if events else 0, 'more': False}


def test_outbox_restarts_preserve_exact_id_and_private_scopes_are_rejected(tmp_path):
    path = tmp_path / 'cloud.sqlite3'
    replica = CloudReplica(path)
    queued = replica.enqueue_event('session', 'session1', {'title': 'local title', 'created_at': '2026-01-01T00:00:00Z'})
    replica.close()
    replica = CloudReplica(path)
    assert replica.pending() == [queued]
    assert replica.enqueue_event('session', 'session1', queued['payload'], event_id=queued['event_id']) == queued
    for entity, payload in [('memory', {'tier': 'temporary', 'content': 'private temp'}),
                            ('message', {'role': 'user', 'content': 'hello', 'screenshot': 'blob'}),
                            ('session', {'title': 'x', 'api_key': 'private key'})]:
        with pytest.raises(ValueError):
            replica.enqueue_event(entity, 'different', payload)
    assert replica.pending() == [queued]
    replica.close()


def test_remote_versions_and_tombstones_are_retained_without_silent_conflict_loss(tmp_path):
    replica = CloudReplica(tmp_path / 'replica.sqlite3')
    replica.apply_page(page(event()))
    pending = replica.enqueue_event('session', 'session1', {'title': 'unsent local', 'created_at': '2026-01-01T00:00:00Z'})
    replica.apply_page(page(event(2, title='other device', revision=2)))
    assert replica.conflicts() == [pending]
    assert not replica.pending()
    assert replica.pending(include_conflicts=True) == [pending]
    assert replica.entities()[0]['payload']['title'] == 'other device'
    replica.apply_page(page(event(3, operation='delete', revision=3)))
    assert replica.entities() == [] and replica.entities(include_deleted=True)[0]['operation'] == 'delete'
    assert replica.conflicts() == [pending]
    replica.close()


def test_invalid_page_rolls_back_entities_and_cursor_atomically(tmp_path):
    replica = CloudReplica(tmp_path / 'replica.sqlite3')
    bad = event(2)
    bad['payload']['training'] = 'not allowed'
    with pytest.raises(ValueError):
        replica.apply_page(page(event(), bad))
    assert replica.cursor == 0 and not replica.entities()
    with pytest.raises(ValueError):
        replica.apply_page({'events': [event()], 'cursor': 10, 'more': False})
    assert replica.cursor == 0 and not replica.entities()
    replica.close()


@pytest.mark.parametrize('timestamp', ['now', '2026-01-01T00:00:00', '2026-99-99T00:00:00Z'])
def test_invalid_remote_times_cannot_poison_local_memory_or_advance_cursor(tmp_path, timestamp):
    replica = CloudReplica(tmp_path / 'replica.sqlite3')
    incoming = event()
    incoming['payload']['created_at'] = timestamp
    with pytest.raises(CloudSyncError, match='时间'):
        replica.apply_page(page(incoming))
    assert replica.cursor == 0 and not replica.entities()
    replica.close()


async def test_offline_send_is_idempotent_and_conflict_preserves_original(tmp_path):
    replica = CloudReplica(tmp_path / 'replica.sqlite3')
    queued = replica.enqueue_event('session', 'session1', {'title': 'local title', 'created_at': '2026-01-01T00:00:00Z'})
    class Client:
        async def push_events(self, events):
            assert events == [queued]
            raise CloudSyncError('offline')
    with pytest.raises(CloudSyncError):
        await replica.sync_once(Client())
    assert replica.pending() == [queued]
    class ConflictClient:
        async def push_events(self, events): raise CloudConflict('conflict')
        async def pull_events(self, after): return page(event())
    await replica.sync_once(ConflictClient())
    assert replica.conflicts() == [queued] and replica.cursor == 1
    replica.close()


async def test_task_submission_reuses_persisted_id_without_execution_replay(tmp_path):
    replica = CloudReplica(tmp_path / 'replica.sqlite3')
    task = replica.enqueue_task('workspace1', 'session1', 'change explicit project', task_id='task1')
    assert replica.enqueue_task('workspace1', 'session1', 'change explicit project', task_id='task1') == task
    with pytest.raises(CloudConflict):
        replica.enqueue_task('workspace1', 'session1', 'different', task_id='task1')
    calls = []
    class Client:
        async def pull_events(self, after): return page()
        async def submit_task(self, payload):
            calls.append(payload)
            if len(calls) == 1: raise CloudSyncError('network after accepted submission')
            return {'task': {**payload, 'status': 'queued'}}
    with pytest.raises(CloudSyncError):
        await replica.sync_once(Client())
    assert replica.tasks(pending_only=True)[0]['id'] == 'task1'
    await replica.sync_once(Client())
    await replica.sync_once(Client())
    assert calls == [task, task]  # Same server-idempotency key; never a new task.
    assert replica.tasks()[0]['local_state'] == 'submitted'
    replica.close()


@pytest.mark.parametrize('receipt', [{}, {'events': [], 'cursor': 0},
    {'events': [event(entity_id='different')], 'cursor': 1}])
async def test_http_success_without_exact_receipt_does_not_acknowledge_outbox(tmp_path, receipt):
    replica = CloudReplica(tmp_path / 'replica.sqlite3')
    original = replica.enqueue_event('session', 'session1', {'title': 'local title', 'created_at': '2026-01-01T00:00:00Z'})
    class Client:
        async def push_events(self, events): return receipt
        async def pull_events(self, after): raise AssertionError('Invalid receipt must not proceed')
    with pytest.raises(CloudSyncError):
        await replica.sync_once(Client())
    assert replica.pending() == [original] and replica.cursor == 0
    replica.close()


async def test_valid_receipt_acknowledges_exact_batch_before_pull(tmp_path):
    replica = CloudReplica(tmp_path / 'replica.sqlite3')
    original = replica.enqueue_event('session', 'session1', {'title': 'local title', 'created_at': '2026-01-01T00:00:00Z'})
    returned = {**original, 'cursor': 1, 'revision': 1}
    class Client:
        async def push_events(self, events): return {'events': [returned], 'cursor': 1}
        async def pull_events(self, after): return page(returned)
    await replica.sync_once(Client())
    assert not replica.pending() and replica.cursor == 1
    replica.close()


def test_outgoing_batches_respect_byte_limit_without_dropping_tail(tmp_path):
    from assistant.cloud_replica import canonical
    replica = CloudReplica(tmp_path / 'replica.sqlite3')
    for index in range(20):
        replica.enqueue_event('message', f'message{index}', {'session_id': 'session1', 'role': 'user',
            'content': 'x' * 60000, 'created_at': '2026-01-01T00:00:00Z'})
    batch = replica.pending()
    assert 0 < len(batch) < 20
    assert len(canonical({'events': batch}).encode()) <= 900000
    assert replica.db.execute('SELECT COUNT(*) FROM replica_outbox').fetchone()[0] == 20
    replica.close()
