"""Opt-in content bridge for MemoryStore, with stable cross-device message IDs.

No cloud event starts Codex, clicks the screen, or becomes an instruction.
Sync must be explicitly configured; construction alone makes no network calls.
"""
import asyncio
from contextlib import contextmanager
import hashlib
import json
from uuid import uuid4

from .cloud_replica import canonical, validate_payload
from .cloud_client import CloudSyncError


def _digest(payload):
    return hashlib.sha256(canonical(payload).encode()).hexdigest()


class MemoryCloudBridge:
    def __init__(self, memory, replica, client):
        self.memory, self.replica, self.client = memory, replica, client
        self.sync_lock = asyncio.Lock()
        self.scope = replica.bridge_identity
        with memory.lock, self._memory_transaction():
            memory.db.execute('CREATE TABLE IF NOT EXISTS cloud_bridge_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
            schema = memory.db.execute("SELECT value FROM cloud_bridge_meta WHERE key='schema'").fetchone()
            if schema is not None and schema[0] != '1':
                raise CloudSyncError('云同步导入日志格式不兼容；原聊天与映射保留。')
            memory.db.execute("INSERT OR IGNORE INTO cloud_bridge_meta VALUES ('schema','1')")
            memory.db.execute('''CREATE TABLE IF NOT EXISTS cloud_bridge_import_journal(
                scope TEXT NOT NULL,entity TEXT NOT NULL,entity_id TEXT NOT NULL,
                local_id TEXT NOT NULL,revision INTEGER NOT NULL,operation TEXT NOT NULL,
                payload_hash TEXT NOT NULL,PRIMARY KEY(scope,entity,entity_id))''')
            existing = memory.db.execute('SELECT 1 FROM cloud_bridge_import_journal LIMIT 1').fetchone()
            own = memory.db.execute('SELECT 1 FROM cloud_bridge_import_journal WHERE scope=? LIMIT 1', (self.scope,)).fetchone()
            if replica.new_bridge_identity and existing and not own:
                raise CloudSyncError('聊天库包含原云同步日志，但稳定映射库缺失；请恢复匹配的同步库，不能当作新设备重建。')
        with replica.lock, replica.db:
            replica.db.executescript('''
                CREATE TABLE IF NOT EXISTS bridge_records(
                    entity TEXT NOT NULL, local_id TEXT NOT NULL, entity_id TEXT NOT NULL,
                    local_hash TEXT NOT NULL, deleted INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(entity,local_id), UNIQUE(entity,entity_id));
                CREATE TABLE IF NOT EXISTS bridge_conflicts(
                    entity TEXT NOT NULL, entity_id TEXT NOT NULL, local_payload TEXT NOT NULL,
                    remote_payload TEXT NOT NULL, PRIMARY KEY(entity,entity_id));
                CREATE TABLE IF NOT EXISTS bridge_import_revisions(
                    entity TEXT NOT NULL,entity_id TEXT NOT NULL,revision INTEGER NOT NULL,
                    PRIMARY KEY(entity,entity_id));
                CREATE TABLE IF NOT EXISTS bridge_export_issues(
                    entity TEXT NOT NULL,local_id TEXT NOT NULL,reason TEXT NOT NULL,
                    payload_bytes INTEGER NOT NULL,PRIMARY KEY(entity,local_id));
            ''')

    @contextmanager
    def _memory_transaction(self):
        # An outer transaction could still roll back after replica metadata was
        # committed. Never pretend a savepoint release makes that durable.
        if self.memory.db.in_transaction:
            raise CloudSyncError('聊天数据库已有事务；本次同步稍后重试，原数据保留。')
        self.memory.db.execute('BEGIN IMMEDIATE')
        try:
            yield
            self.memory.db.commit()
        except BaseException:
            self.memory.db.rollback()
            raise

    def _recover_imports_locked(self):
        """Finish replica bookkeeping only AFTER source content+journal committed."""
        rows = list(self.memory.db.execute('SELECT * FROM cloud_bridge_import_journal WHERE scope=?', (self.scope,)))
        applied_revisions = {(row['entity'], row['entity_id']): row['revision']
                             for row in self.replica.db.execute('SELECT * FROM bridge_import_revisions')}
        with self.replica.transaction():
            for row in rows:
                if applied_revisions.get((row['entity'], row['entity_id']), 0) >= row['revision']:
                    continue
                self.replica.db.execute('INSERT OR REPLACE INTO bridge_records VALUES(?,?,?,?,?)',
                                       (row['entity'], row['local_id'], row['entity_id'], row['payload_hash'], int(row['operation'] == 'delete')))
                self.replica.db.execute('INSERT OR REPLACE INTO bridge_import_revisions VALUES(?,?,?)',
                                       (row['entity'], row['entity_id'], row['revision']))
                self.replica.db.execute('DELETE FROM bridge_conflicts WHERE entity=? AND entity_id=?',
                                       (row['entity'], row['entity_id']))
                if row['entity'] == 'session' and row['operation'] == 'delete' and not self.memory.has_session(row['local_id']):
                    self.memory.buffers.pop(row['local_id'], None)

    def _journal_import(self, item, local_id):
        self.memory.db.execute('INSERT OR REPLACE INTO cloud_bridge_import_journal VALUES(?,?,?,?,?,?,?)',
                               (self.scope, item['entity'], item['entity_id'], local_id,
                                item['revision'], item['operation'], _digest(item['payload'])))

    def _snapshot(self):
        with self.memory.lock:
            return {
                'session': {str(row['id']): {'title': row['title'], 'created_at': row['created_at']}
                            for row in self.memory.db.execute('SELECT * FROM sessions')},
                'message': {str(row['id']): {key: row[key] for key in ('session_id', 'role', 'content', 'created_at')}
                            for row in self.memory.db.execute('SELECT * FROM messages')},
                'memory': {str(row['id']): {key: row[key] for key in
                             ('tier', 'kind', 'content', 'created_at', 'accessed_at', 'expires_at', 'uses', 'source_session')}
                           for row in self.memory.db.execute("SELECT * FROM memories WHERE tier IN ('first','second','permanent')")},
            }

    def _records(self):
        return {(row['entity'], row['local_id']): dict(row)
                for row in self.replica.db.execute('SELECT * FROM bridge_records')}

    def export_local(self):
        """Stage changed text locally. A failed send never removes source rows."""
        staged = 0
        with self.memory.lock, self.replica.lock:
            self._recover_imports_locked()
            snapshot = self._snapshot()
            records = self._records()
            issues = {(row['entity'], row['local_id']) for row in self.replica.db.execute('SELECT entity,local_id FROM bridge_export_issues')}
            invalid_new_sessions = set()
            # Parents are deliberately staged before their messages.
            for entity, rows in snapshot.items():
                for local_id, payload in rows.items():
                    record = records.get((entity, local_id))
                    payload_size = 0
                    try:
                        payload_size = len(canonical(payload).encode())
                        validate_payload(entity, 'upsert', payload)
                        if entity == 'message' and payload['session_id'] in invalid_new_sessions:
                            raise CloudSyncError('parent record is not syncable')
                    except (ValueError, TypeError):
                        reason = 'payload_too_large' if payload_size > 64000 else 'unsupported_record'
                        if entity == 'session' and self.replica.revision('session', local_id) == 0:
                            invalid_new_sessions.add(local_id)
                        with self.replica.transaction():
                            self.replica.db.execute('''INSERT INTO bridge_export_issues VALUES(?,?,?,?)
                                ON CONFLICT(entity,local_id) DO UPDATE SET reason=excluded.reason,payload_bytes=excluded.payload_bytes
                                WHERE reason<>excluded.reason OR payload_bytes<>excluded.payload_bytes''',
                                (entity, local_id, reason, payload_size))
                        continue  # Preserve full local text; sync other valid records.
                    if (entity, local_id) in issues:
                        with self.replica.transaction():
                            self.replica.db.execute('DELETE FROM bridge_export_issues WHERE entity=? AND local_id=?', (entity, local_id))
                    # INTEGER PRIMARY KEY may be reused after deletion. A new
                    # local message must not resurrect its old cloud tombstone.
                    cloud_id = record['entity_id'] if record and not (entity == 'message' and record['deleted']) else (uuid4().hex if entity == 'message' else local_id)
                    fingerprint = _digest(payload)
                    if record and record['local_hash'] == fingerprint and not record['deleted']:
                        continue
                    if self.replica.has_pending(entity, cloud_id):
                        continue  # A later local edit remains in MemoryStore.
                    with self.replica.transaction():
                        self.replica.enqueue_event(entity, cloud_id, payload)
                        self.replica.db.execute('INSERT OR REPLACE INTO bridge_records VALUES(?,?,?,?,0)',
                                               (entity, local_id, cloud_id, fingerprint))
                    staged += 1
            # Persist deletion tombstones, never infer deletion from an empty or
            # failed cloud response. Session cascades are represented explicitly.
            for (entity, local_id), record in records.items():
                if local_id in snapshot[entity] or record['deleted']:
                    continue
                if self.replica.has_pending(entity, record['entity_id']):
                    continue
                with self.replica.transaction():
                    self.replica.enqueue_event(entity, record['entity_id'], operation='delete')
                    self.replica.db.execute('UPDATE bridge_records SET deleted=1,local_hash=? WHERE entity=? AND local_id=?',
                                           (_digest({}), entity, local_id))
                staged += 1
            for entity, local_id in issues:
                if local_id not in snapshot[entity]:
                    with self.replica.transaction():
                        self.replica.db.execute('DELETE FROM bridge_export_issues WHERE entity=? AND local_id=?', (entity, local_id))
        return staged

    def issues(self, *, limit=100):
        """Owner-readable metadata only; no oversized text appears in status."""
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise CloudSyncError('同步问题分页大小无效。')
        with self.replica.lock:
            return [{**dict(row), 'limit_bytes': 64000} for row in self.replica.db.execute(
                'SELECT * FROM bridge_export_issues ORDER BY entity,local_id LIMIT ?', (limit,))]

    def issue_count(self):
        with self.replica.lock:
            return self.replica.db.execute('SELECT COUNT(*) FROM bridge_export_issues').fetchone()[0]

    def _record_conflict(self, entity, entity_id, local, remote):
        self.replica.db.execute('INSERT OR REPLACE INTO bridge_conflicts VALUES(?,?,?,?)',
                               (entity, entity_id, canonical(local), canonical(remote)))

    def import_remote(self):
        """Commit content+recovery journal together, then commit replica mappings.

        The two databases cannot share a crash-atomic WAL transaction. A journal
        in the content database records the exact IDs in that SAME transaction.
        Replaying only replica bookkeeping is then safe after either commit.
        """
        imported = 0
        conflicts, ignored_deletes, cleared_sessions = [], [], set()
        def conflict(entity, entity_id, local, remote):
            conflicts.append((entity, entity_id, local, remote))
        with self.memory.lock, self.replica.lock:
            self._recover_imports_locked()
            snapshot = self._snapshot()
            records = { (row['entity'], row['entity_id']): dict(row)
                        for row in self.replica.db.execute('SELECT * FROM bridge_records') }
            applied = {(row['entity'], row['entity_id']): row['revision']
                       for row in self.replica.db.execute('SELECT * FROM bridge_import_revisions')}
            remote = self.replica.entities(include_deleted=True)
            remote.sort(key=lambda item: {'session': 0, 'message': 1, 'memory': 2}[item['entity']])
            with self._memory_transaction():
                for item in remote:
                    entity, cloud_id, payload = item['entity'], item['entity_id'], item['payload']
                    if applied.get((entity, cloud_id), 0) >= item['revision']:
                        continue  # Unchanged history is not rewritten every poll.
                    record = records.get((entity, cloud_id))
                    local_id = record['local_id'] if record else (None if entity == 'message' else cloud_id)
                    local = snapshot[entity].get(local_id) if local_id is not None else None
                    if record is None and local is not None and local != payload:
                        conflict(entity, cloud_id, local, item)
                        continue
                    if self.replica.has_pending(entity, cloud_id):
                        if local != payload:
                            conflict(entity, cloud_id, local, item)
                        continue
                    if record and ((local is None and not record['deleted'])
                                   or (local is not None and _digest(local) != record['local_hash'])):
                        conflict(entity, cloud_id, local, item)
                        continue
                    if item['operation'] == 'delete':
                        if entity == 'session' and local_id is not None:
                            unsafe_children = False
                            local_records = self._records()
                            for child_id, child in snapshot['message'].items():
                                if child['session_id'] != local_id:
                                    continue
                                child_record = local_records.get(('message', child_id))
                                if (child_record is None or _digest(child) != child_record['local_hash']
                                        or self.replica.has_pending('message', child_record['entity_id'])):
                                    unsafe_children = True
                                    break
                            if unsafe_children:
                                conflict(entity, cloud_id, local, item)
                                continue
                        if local_id is not None:
                            table = {'session': 'sessions', 'message': 'messages', 'memory': 'memories'}[entity]
                            self.memory.db.execute(f'DELETE FROM {table} WHERE id=?', (local_id,))
                            if entity == 'session':
                                cleared_sessions.add(local_id)
                            self._journal_import(item, local_id)
                        else:
                            ignored_deletes.append((entity, cloud_id, item['revision']))
                        continue
                    if entity == 'session':
                        self.memory.db.execute('''INSERT INTO sessions(id,title,created_at) VALUES(?,?,?)
                            ON CONFLICT(id) DO UPDATE SET title=excluded.title,created_at=excluded.created_at''',
                            (local_id, payload['title'], payload['created_at']))
                    elif entity == 'message':
                        if not self.memory.has_session(payload['session_id']):
                            conflict(entity, cloud_id, local, item)
                            continue
                        if local_id is None:
                            cursor = self.memory.db.execute('INSERT INTO messages(session_id,role,content,created_at) VALUES(?,?,?,?)',
                                                           tuple(payload[key] for key in ('session_id', 'role', 'content', 'created_at')))
                            local_id = str(cursor.lastrowid)
                        else:
                            self.memory.db.execute('UPDATE messages SET session_id=?,role=?,content=?,created_at=? WHERE id=?',
                                                   (*[payload[key] for key in ('session_id', 'role', 'content', 'created_at')], local_id))
                    else:
                        keys = ('tier', 'kind', 'content', 'created_at', 'accessed_at', 'expires_at', 'uses', 'source_session')
                        values = [payload[key] for key in keys]
                        self.memory.db.execute('''INSERT INTO memories(id,tier,kind,content,created_at,accessed_at,expires_at,uses,source_session)
                            VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET tier=excluded.tier,kind=excluded.kind,
                            content=excluded.content,created_at=excluded.created_at,accessed_at=excluded.accessed_at,expires_at=excluded.expires_at,
                            uses=excluded.uses,source_session=excluded.source_session''', (local_id, *values))
                    self._journal_import(item, local_id)
                    imported += 1
            # At this point content and its exact ID journal are durable. A crash
            # below leaves a recoverable journal, never a speculative mapping.
            for session_id in cleared_sessions:
                self.memory.buffers.pop(session_id, None)
            with self.replica.transaction():
                self._recover_imports_locked()
                for values in conflicts:
                    self._record_conflict(*values)
                for values in ignored_deletes:
                    self.replica.db.execute('INSERT OR REPLACE INTO bridge_import_revisions VALUES(?,?,?)', values)
        return imported

    def conflicts(self):
        with self.replica.lock:
            result = [{**dict(row), 'local_payload': json.loads(row['local_payload']),
                       'remote_payload': json.loads(row['remote_payload'])}
                      for row in self.replica.db.execute('SELECT * FROM bridge_conflicts')]
            known = {(item['entity'], item['entity_id']) for item in result}
            for event in self.replica.conflicts():
                if (event['entity'], event['entity_id']) not in known:
                    result.append({'entity': event['entity'], 'entity_id': event['entity_id'],
                                   'event_id': event['event_id'], 'local_payload': event['payload'],
                                   'remote_payload': None, 'reason': 'version-conflict'})
            return result

    async def sync_once(self):
        async with self.sync_lock:
            staged = self.export_local()
            result = await self.replica.sync_once(self.client)
            imported = self.import_remote()
            return {**result, 'staged': staged, 'imported': imported,
                    'bridge_conflicts': len(self.conflicts()), 'unsyncable_count': self.issue_count()}
