"""Fault injection around SQLite commit boundaries, never actual user data."""
from contextlib import contextmanager
import sqlite3

import pytest

from assistant.cloud_bridge import MemoryCloudBridge
from assistant.cloud_client import CloudSyncError
from assistant.cloud_replica import CloudReplica
from assistant.memory import MemoryStore


@pytest.fixture
def setup(tmp_path):
    memory = MemoryStore(tmp_path / 'memory.sqlite3')
    replica = CloudReplica(tmp_path / 'replica.sqlite3')
    bridge = MemoryCloudBridge(memory, replica, None)
    try:
        yield bridge, memory, replica
    finally:
        memory.close(); replica.close()


def remote_history(replica):
    entries = [
        {'event_id': 'session-event', 'entity': 'session', 'entity_id': 'remote-session',
         'payload': {'title': 'remote fixture', 'created_at': '2026-01-01T00:00:00Z'}},
        {'event_id': 'message-event', 'entity': 'message', 'entity_id': 'remote-message',
         'payload': {'session_id': 'remote-session', 'role': 'user', 'content': 'one durable message',
                     'created_at': '2026-01-01T00:00:00Z'}},
    ]
    replica.apply_page({'events': [{**event, 'operation': 'upsert', 'base_revision': 0,
                                   'revision': 1, 'cursor': index + 1}
                                  for index, event in enumerate(entries)], 'cursor': 2, 'more': False})


class CommitFault:
    """Simulate either failed commit or lost acknowledgement after commit."""
    def __init__(self, connection, table, *, after, message_only=False):
        self.connection, self.table, self.after = connection, table, after
        self.message_only, self.changed, self.tripped = message_only, False, False

    def __getattr__(self, name):
        return getattr(self.connection, name)

    def execute(self, sql, parameters=()):
        if (sql.lstrip().lower().startswith('insert') and self.table in sql
                and (not self.message_only or parameters[0] == 'message')):
            self.changed = True
        return self.connection.execute(sql, parameters)

    def commit(self):
        if self.changed and not self.tripped:
            self.tripped = True
            if self.after:
                self.connection.commit()
            raise OSError('simulated commit boundary failure')
        self.connection.commit()


@contextmanager
def fault(owner, table, *, after=False, message_only=False):
    original = owner.db
    proxy = CommitFault(original, table, after=after, message_only=message_only)
    owner.db = proxy
    try:
        yield proxy
    finally:
        owner.db = original


@pytest.mark.parametrize('after', [False, True])
def test_export_outbox_and_stable_message_id_commit_together(setup, after):
    bridge, memory, replica = setup
    session = memory.create_session()
    memory.append(session['id'], 'user', 'local fixture')
    with fault(replica, 'bridge_records', after=after, message_only=True) as failure:
        with pytest.raises(OSError, match='commit boundary'):
            bridge.export_local()
        assert failure.tripped
    bridge.export_local()
    queued = [event for event in replica.pending() if event['entity'] == 'message']
    assert len(queued) == 1
    mapping = replica.db.execute("SELECT entity_id FROM bridge_records WHERE entity='message'").fetchone()
    assert mapping[0] == queued[0]['entity_id']
    assert memory.messages(session['id'])[0]['content'] == 'local fixture'


def test_crash_after_enqueue_before_mapping_does_not_publish_orphan_event(setup, monkeypatch):
    bridge, memory, replica = setup
    session = memory.create_session()
    memory.append(session['id'], 'user', 'local fixture')
    original = replica.enqueue_event
    def crash(*args, **kwargs):
        result = original(*args, **kwargs)
        if args[0] == 'message':
            raise OSError('simulated message mapping failure')
        return result
    monkeypatch.setattr(replica, 'enqueue_event', crash)
    with pytest.raises(OSError, match='mapping failure'):
        bridge.export_local()
    assert not any(event['entity'] == 'message' for event in replica.pending())
    monkeypatch.setattr(replica, 'enqueue_event', original)
    bridge.export_local()
    assert len([event for event in replica.pending() if event['entity'] == 'message']) == 1


@pytest.mark.parametrize('after', [False, True])
def test_memory_commit_boundary_never_turns_import_into_delete_or_duplicate(setup, after):
    bridge, memory, replica = setup
    remote_history(replica)
    with fault(memory, 'cloud_bridge_import_journal', after=after) as failure:
        with pytest.raises(OSError, match='commit boundary'):
            bridge.import_remote()
        assert failure.tripped
    assert memory.has_session('remote-session') is after
    assert bridge.export_local() == 0 and replica.pending() == []
    bridge.import_remote()
    assert len(memory.messages('remote-session')) == 1
    assert bridge.import_remote() == 0
    assert bridge.export_local() == 0 and replica.pending() == []


@pytest.mark.parametrize('after', [False, True])
def test_replica_commit_failure_after_content_recovers_exact_ids(setup, after):
    bridge, memory, replica = setup
    remote_history(replica)
    with fault(replica, 'bridge_records', after=after) as failure:
        with pytest.raises(OSError, match='commit boundary'):
            bridge.import_remote()
        assert failure.tripped
    original = memory.messages('remote-session')
    assert len(original) == 1
    assert bridge.export_local() == 0
    assert bridge.import_remote() == 0
    assert memory.messages('remote-session') == original
    assert not replica.pending()


def test_restart_finishes_mapping_from_content_journal(tmp_path):
    memory_path, replica_path = tmp_path / 'memory.sqlite3', tmp_path / 'replica.sqlite3'
    memory, replica = MemoryStore(memory_path), CloudReplica(replica_path)
    bridge = MemoryCloudBridge(memory, replica, None)
    remote_history(replica)
    with fault(replica, 'bridge_records'):
        with pytest.raises(OSError): bridge.import_remote()
    memory.close(); replica.close()
    memory, replica = MemoryStore(memory_path), CloudReplica(replica_path)
    try:
        bridge = MemoryCloudBridge(memory, replica, None)
        assert bridge.export_local() == 0 and bridge.import_remote() == 0
        assert len(memory.messages('remote-session')) == 1 and not replica.pending()
    finally:
        memory.close(); replica.close()


def test_missing_matching_mapping_database_fails_without_resetting_content(setup, tmp_path):
    bridge, memory, replica = setup
    remote_history(replica); bridge.import_remote()
    replacement = CloudReplica(tmp_path / 'new-empty-replica.sqlite3')
    try:
        with pytest.raises(CloudSyncError, match='稳定映射库缺失'):
            MemoryCloudBridge(memory, replacement, None)
        assert len(memory.messages('remote-session')) == 1
        assert not replacement.pending()
    finally:
        replacement.close()


def test_unknown_import_journal_schema_stops_without_touching_content(setup):
    bridge, memory, replica = setup
    session = memory.create_session()
    with memory.db:
        memory.db.execute("UPDATE cloud_bridge_meta SET value='999' WHERE key='schema'")
    with pytest.raises(CloudSyncError, match='格式不兼容'):
        MemoryCloudBridge(memory, replica, None)
    assert memory.has_session(session['id']) and not replica.pending()


def test_unknown_replica_schema_is_not_opened_as_empty_storage(tmp_path):
    path = tmp_path / 'replica.sqlite3'
    replica = CloudReplica(path)
    replica.enqueue_event('session', 'local-session', {'title': 'preserve', 'created_at': '2026-01-01T00:00:00Z'})
    with replica.db:
        replica.db.execute("UPDATE replica_state SET value='999' WHERE key='cloud_replica_schema'")
    replica.close()
    with pytest.raises(CloudSyncError, match='格式不兼容'):
        CloudReplica(path)
    with sqlite3.connect(path) as inspection:
        assert inspection.execute('SELECT COUNT(*) FROM replica_outbox').fetchone()[0] == 1


def test_failed_replica_constructor_closes_only_its_new_connection(tmp_path, monkeypatch):
    opened = []
    connect = sqlite3.connect
    def inspect_connect(*args, **kwargs):
        connection = connect(*args, **kwargs)
        opened.append(connection)
        return connection
    def fail(self, path): raise sqlite3.DatabaseError('fixture initialization failure')
    monkeypatch.setattr('assistant.cloud_replica.sqlite3.connect', inspect_connect)
    monkeypatch.setattr(CloudReplica, '_initialize', fail)
    with pytest.raises(sqlite3.DatabaseError):
        CloudReplica(tmp_path / 'replica.sqlite3')
    assert len(opened) == 1
    with pytest.raises(sqlite3.ProgrammingError, match='closed'):
        opened[0].execute('SELECT 1')


def test_reused_integer_message_id_after_exported_delete_gets_new_cloud_id(setup):
    bridge, memory, replica = setup
    session = memory.create_session()
    memory.append(session['id'], 'user', 'original')
    bridge.export_local()
    old = next(event for event in replica.pending() if event['entity'] == 'message')
    with memory.db:
        memory.db.execute('DELETE FROM messages')
    bridge.export_local()  # Pending old upsert delays the delete.
    originals = replica.pending()
    replica.acknowledge([event['event_id'] for event in originals])
    replica.apply_page({'events': [{**event, 'cursor': i + 1, 'revision': 1} for i, event in enumerate(originals)],
                        'cursor': len(originals), 'more': False})
    bridge.export_local()
    assert any(event['operation'] == 'delete' and event['entity_id'] == old['entity_id'] for event in replica.pending())
    memory.append(session['id'], 'user', 'new message with reused int ID')
    bridge.export_local()
    new = next(event for event in replica.pending() if event['entity'] == 'message' and event['operation'] == 'upsert')
    assert new['entity_id'] != old['entity_id']


def test_cloud_origin_binding_normalizes_but_never_moves_existing_queue(tmp_path):
    replica = CloudReplica(tmp_path / 'replica.sqlite3')
    try:
        replica.bind_origin('https://EXAMPLE.com:443/')
        original = replica.enqueue_event('session', 'session1', {'title': 'private fixture', 'created_at': '2026-01-01T00:00:00Z'})
        replica.bind_origin('https://example.com')
        with pytest.raises(CloudSyncError, match='原同步库不同'):
            replica.bind_origin('https://different.example')
        assert replica.pending() == [original] and replica.cursor == 0
    finally:
        replica.close()


def test_nonempty_unbound_legacy_replica_requires_explicit_migration(tmp_path):
    replica = CloudReplica(tmp_path / 'replica.sqlite3')
    try:
        original = replica.enqueue_event('session', 'session1', {'title': 'private fixture', 'created_at': '2026-01-01T00:00:00Z'})
        with pytest.raises(CloudSyncError, match='未登记云地址'):
            replica.bind_origin('https://example.com')
        assert replica.pending() == [original]
        assert replica.db.execute("SELECT 1 FROM replica_state WHERE key='origin_sha256_v1'").fetchone() is None
    finally:
        replica.close()
