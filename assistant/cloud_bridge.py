"""Opt-in content bridge for MemoryStore, with stable cross-device message IDs.

No cloud event starts Codex, clicks the screen, or becomes an instruction.
Sync must be explicitly configured; construction alone makes no network calls.
"""
import asyncio
import hashlib
import json
from uuid import uuid4

from .cloud_replica import canonical


def _digest(payload):
    return hashlib.sha256(canonical(payload).encode()).hexdigest()


class MemoryCloudBridge:
    def __init__(self, memory, replica, client):
        self.memory, self.replica, self.client = memory, replica, client
        self.sync_lock = asyncio.Lock()
        with replica.lock, replica.db:
            replica.db.executescript('''
                CREATE TABLE IF NOT EXISTS bridge_records(
                    entity TEXT NOT NULL, local_id TEXT NOT NULL, entity_id TEXT NOT NULL,
                    local_hash TEXT NOT NULL, deleted INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(entity,local_id), UNIQUE(entity,entity_id));
                CREATE TABLE IF NOT EXISTS bridge_conflicts(
                    entity TEXT NOT NULL, entity_id TEXT NOT NULL, local_payload TEXT NOT NULL,
                    remote_payload TEXT NOT NULL, PRIMARY KEY(entity,entity_id));
            ''')

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
        snapshot = self._snapshot()
        staged = 0
        with self.replica.lock:
            records = self._records()
            # Parents are deliberately staged before their messages.
            for entity, rows in snapshot.items():
                for local_id, payload in rows.items():
                    record = records.get((entity, local_id))
                    cloud_id = record['entity_id'] if record else (uuid4().hex if entity == 'message' else local_id)
                    fingerprint = _digest(payload)
                    if record and record['local_hash'] == fingerprint and not record['deleted']:
                        continue
                    if self.replica.has_pending(entity, cloud_id):
                        continue  # A later local edit remains in MemoryStore.
                    self.replica.enqueue_event(entity, cloud_id, payload)
                    with self.replica.db:
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
                self.replica.enqueue_event(entity, record['entity_id'], operation='delete')
                with self.replica.db:
                    self.replica.db.execute('UPDATE bridge_records SET deleted=1,local_hash=? WHERE entity=? AND local_id=?',
                                           (_digest({}), entity, local_id))
                staged += 1
        return staged

    def _record_conflict(self, entity, entity_id, local, remote):
        self.replica.db.execute('INSERT OR REPLACE INTO bridge_conflicts VALUES(?,?,?,?)',
                               (entity, entity_id, canonical(local), canonical(remote)))

    def import_remote(self):
        """Merge validated remote text without overwriting unsent local changes."""
        imported = 0
        # Global lock ordering is MemoryStore -> replica, matching export's
        # snapshot phase and avoiding nested locks in network code.
        with self.memory.lock, self.replica.lock, self.memory.db, self.replica.db:
            snapshot = self._snapshot()
            records = { (row['entity'], row['entity_id']): dict(row)
                        for row in self.replica.db.execute('SELECT * FROM bridge_records') }
            remote = self.replica.entities(include_deleted=True)
            # Establish remote sessions before importing messages or memories.
            remote.sort(key=lambda item: {'session': 0, 'message': 1, 'memory': 2}[item['entity']])
            for item in remote:
                entity, cloud_id, payload = item['entity'], item['entity_id'], item['payload']
                record = records.get((entity, cloud_id))
                local_id = record['local_id'] if record else (None if entity == 'message' else cloud_id)
                local = snapshot[entity].get(local_id) if local_id is not None else None
                if record is None and local is not None and local != payload:
                    self._record_conflict(entity, cloud_id, local, item)
                    continue
                if self.replica.has_pending(entity, cloud_id):
                    if local != payload:
                        self._record_conflict(entity, cloud_id, local, item)
                    continue
                # Someone may edit the local App while the HTTPS request is in
                # flight. Compare to the exact last staged/imported local bytes.
                if record and ((local is None and not record['deleted'])
                               or (local is not None and _digest(local) != record['local_hash'])):
                    self._record_conflict(entity, cloud_id, local, item)
                    continue
                if item['operation'] == 'delete':
                    if entity == 'session' and local_id is not None:
                        # A session deletion must not cascade away a message the
                        # user added/edited while the network request was in flight.
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
                            self._record_conflict(entity, cloud_id, local, item)
                            continue
                    if local_id is not None:
                        table = {'session': 'sessions', 'message': 'messages', 'memory': 'memories'}[entity]
                        self.memory.db.execute(f'DELETE FROM {table} WHERE id=?', (local_id,))
                        if entity == 'session':
                            self.memory.buffers.pop(local_id, None)
                        self.replica.db.execute('UPDATE bridge_records SET local_hash=?,deleted=1 WHERE entity=? AND local_id=?',
                                               (_digest({}), entity, local_id))
                    continue
                if entity == 'session':
                    self.memory.db.execute('INSERT INTO sessions(id,title,created_at) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET title=excluded.title',
                                           (local_id, payload['title'], payload['created_at']))
                elif entity == 'message':
                    if not self.memory.has_session(payload['session_id']):
                        self._record_conflict(entity, cloud_id, local, item)
                        continue
                    if local_id is None:
                        cursor = self.memory.db.execute('INSERT INTO messages(session_id,role,content,created_at) VALUES(?,?,?,?)',
                                                       tuple(payload[key] for key in ('session_id', 'role', 'content', 'created_at')))
                        local_id = str(cursor.lastrowid)
                    else:
                        self.memory.db.execute('UPDATE messages SET session_id=?,role=?,content=?,created_at=? WHERE id=?',
                                               (*[payload[key] for key in ('session_id', 'role', 'content', 'created_at')], local_id))
                    # Do not feed replayed history into temporary memory or ASR.
                else:
                    keys = ('tier', 'kind', 'content', 'created_at', 'accessed_at', 'expires_at', 'uses', 'source_session')
                    values = [payload[key] for key in keys]
                    self.memory.db.execute('''INSERT INTO memories(id,tier,kind,content,created_at,accessed_at,expires_at,uses,source_session)
                        VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET tier=excluded.tier,kind=excluded.kind,
                        content=excluded.content,accessed_at=excluded.accessed_at,expires_at=excluded.expires_at,
                        uses=excluded.uses,source_session=excluded.source_session''', (local_id, *values))
                self.replica.db.execute('INSERT OR REPLACE INTO bridge_records VALUES(?,?,?,?,0)',
                                       (entity, local_id, cloud_id, _digest(payload)))
                self.replica.db.execute('DELETE FROM bridge_conflicts WHERE entity=? AND entity_id=?', (entity, cloud_id))
                imported += 1
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
                    'bridge_conflicts': len(self.conflicts())}
