"""SQLite transaction log and a non-replaying desktop task queue.

One database belongs to one private owner. Device tokens are stored only as
hashes. Cloud text is inert data; this module has no Codex or desktop imports.
"""
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import stat
from threading import RLock
import time
from uuid import uuid4

from .schema import MAX_DOCUMENT


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def token_hash(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


class Conflict(ValueError):
    def __init__(self, code, **details):
        self.details = {'code': code, **details}
        super().__init__(code)


class Store:
    def __init__(self, database, *, clock=time.time):
        if str(database) != ':memory:':
            path = Path(database)
            if not path.is_absolute():
                raise ValueError('Cloud database must use an absolute persistent path.')
            for parent in (path, *path.parents):
                if os.path.lexists(parent):
                    info = parent.lstat()
                    if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                        raise ValueError('Cloud data paths must not use symbolic links or junctions.')
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            try:
                descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                if not path.is_file():
                    raise ValueError('Cloud database must be a regular file.')
                if os.name == 'posix' and path.stat().st_mode & 0o077:
                    raise ValueError('Existing cloud database permissions must be private to its owner (0600).')
            else:
                os.close(descriptor)
        self.clock = clock
        self.lock = RLock()
        self.db = sqlite3.connect(str(database), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        with self.db:
            self.db.execute('PRAGMA journal_mode=WAL')
            self.db.execute('PRAGMA synchronous=FULL')
            self.db.execute('PRAGMA foreign_keys=ON')
            self.db.executescript('''
                CREATE TABLE IF NOT EXISTS devices(
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL,
                    workspace_id TEXT, token_hash TEXT NOT NULL UNIQUE,
                    revoked INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS events(
                    cursor INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE, device_id TEXT NOT NULL,
                    entity TEXT NOT NULL, entity_id TEXT NOT NULL,
                    operation TEXT NOT NULL, base_revision INTEGER NOT NULL,
                    revision INTEGER NOT NULL, payload TEXT NOT NULL,
                    request_hash TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS entities(
                    entity TEXT NOT NULL, entity_id TEXT NOT NULL,
                    revision INTEGER NOT NULL, operation TEXT NOT NULL,
                    payload TEXT NOT NULL, PRIMARY KEY(entity,entity_id));
                CREATE TABLE IF NOT EXISTS tasks(
                    id TEXT PRIMARY KEY, device_id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL, session_id TEXT NOT NULL,
                    prompt TEXT NOT NULL, request_hash TEXT NOT NULL,
                    status TEXT NOT NULL, result TEXT, worker_id TEXT,
                    lease_hash TEXT, lease_expires_at REAL,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL);
            ''')

    def close(self):
        with self.lock:
            self.db.close()

    def create_device(self, data):
        secret = secrets.token_urlsafe(32)
        device = {'id': uuid4().hex, **data, 'created_at': self.clock(), 'revoked': False}
        with self.lock, self.db:
            self.db.execute('INSERT INTO devices VALUES(?,?,?,?,?,0,?)',
                            (device['id'], device['name'], device['kind'], device['workspace_id'],
                             token_hash(secret), device['created_at']))
        # Both field names are explicit aliases, returned only at enrollment.
        return {**device, 'token': secret, 'device_token': secret}

    def devices(self):
        with self.lock:
            return [dict(row) for row in self.db.execute(
                'SELECT id,name,kind,workspace_id,revoked,created_at FROM devices ORDER BY created_at,id')]

    def authenticate(self, secret):
        if not isinstance(secret, str) or not 20 <= len(secret) <= 512:
            return None
        with self.lock:
            row = self.db.execute('SELECT id,name,kind,workspace_id FROM devices WHERE token_hash=? AND revoked=0',
                                  (token_hash(secret),)).fetchone()
            return dict(row) if row else None

    def revoke(self, device_id):
        with self.lock, self.db:
            changed = self.db.execute('UPDATE devices SET revoked=1 WHERE id=?', (device_id,)).rowcount
            self.db.execute("UPDATE tasks SET status='interrupted',lease_hash=NULL,lease_expires_at=NULL,updated_at=? "
                            "WHERE worker_id=? AND status IN ('leased','running')", (self.clock(), device_id))
            self.db.execute("UPDATE tasks SET status='cancelled',lease_hash=NULL,lease_expires_at=NULL,updated_at=? "
                            "WHERE device_id=? AND status IN ('queued','leased','running')", (self.clock(), device_id))
            return bool(changed)

    def _active_device(self, device_id):
        row = self.db.execute('SELECT id,name,kind,workspace_id FROM devices WHERE id=? AND revoked=0',
                              (device_id,)).fetchone()
        if row is None:
            raise PermissionError('Device was revoked.')
        return dict(row)

    @staticmethod
    def _event(row):
        return {key: row[key] for key in ('cursor', 'event_id', 'entity', 'entity_id', 'operation',
                                          'base_revision', 'revision')} | {'payload': json.loads(row['payload'])}

    def _cursor(self):
        return self.db.execute('SELECT COALESCE(MAX(cursor),0) FROM events').fetchone()[0]

    def push(self, device_id, events):
        accepted = []
        with self.lock, self.db:
            # BEGIN IMMEDIATE also serializes separately opened server processes.
            self.db.execute('BEGIN IMMEDIATE')
            self._active_device(device_id)
            for event in events:
                digest = token_hash(canonical(event))
                existing = self.db.execute('SELECT * FROM events WHERE event_id=?', (event['event_id'],)).fetchone()
                if existing:
                    if existing['device_id'] != device_id or existing['request_hash'] != digest:
                        raise Conflict('event_id_conflict', event_id=event['event_id'])
                    accepted.append(self._event(existing))
                    continue
                previous = self.db.execute('SELECT * FROM entities WHERE entity=? AND entity_id=?',
                                           (event['entity'], event['entity_id'])).fetchone()
                current = previous['revision'] if previous else 0
                if current != event['base_revision']:
                    raise Conflict('revision_conflict', entity=event['entity'], entity_id=event['entity_id'],
                                   current_revision=current)
                # Tombstones are sticky. Recreating a deleted record needs a new
                # identifier; stale offline history cannot resurrect deleted data.
                if previous and previous['operation'] == 'delete' and event['operation'] != 'delete':
                    raise Conflict('deleted_entity', entity=event['entity'], entity_id=event['entity_id'],
                                   current_revision=current)
                payload = canonical(event['payload'])
                if event['entity'] == 'message' and event['operation'] == 'upsert':
                    parent = self.db.execute("SELECT operation FROM entities WHERE entity='session' AND entity_id=?",
                                             (event['payload']['session_id'],)).fetchone()
                    if parent is None or parent['operation'] == 'delete':
                        raise Conflict('session_missing_or_deleted', session_id=event['payload']['session_id'])
                revision = current + 1
                self.db.execute('INSERT OR REPLACE INTO entities VALUES(?,?,?,?,?)',
                                (event['entity'], event['entity_id'], revision, event['operation'], payload))
                cursor = self.db.execute('''INSERT INTO events(event_id,device_id,entity,entity_id,operation,
                    base_revision,revision,payload,request_hash) VALUES(?,?,?,?,?,?,?,?,?)''',
                    (event['event_id'], device_id, event['entity'], event['entity_id'], event['operation'],
                     event['base_revision'], revision, payload, digest)).lastrowid
                accepted.append({'cursor': cursor, **event, 'revision': revision})
            return {'events': accepted, 'cursor': self._cursor()}

    def pull(self, after, limit):
        with self.lock:
            maximum = self._cursor()
            if after > maximum:
                raise Conflict('cursor_ahead', current_cursor=maximum)
            rows = list(self.db.execute('SELECT * FROM events WHERE cursor>? ORDER BY cursor LIMIT ?',
                                       (after, limit + 1)))
            values, size = [], 128
            for row in rows[:limit]:
                value = self._event(row)
                cost = len(canonical(value).encode()) + 2
                if size + cost > MAX_DOCUMENT - 4096:
                    break
                values.append(value)
                size += cost
            return {'events': values, 'cursor': values[-1]['cursor'] if values else after,
                    'more': len(rows) > len(values)}

    def _expire(self):
        # No automatic replay after a claimed task expires. A disconnected worker
        # could already have changed files; retrying it could duplicate side effects.
        self.db.execute("UPDATE tasks SET status='interrupted',lease_hash=NULL,lease_expires_at=NULL,updated_at=? "
                        "WHERE status IN ('leased','running') AND lease_expires_at<=?", (self.clock(), self.clock()))

    @staticmethod
    def _task(row):
        return {key: row[key] for key in ('id', 'workspace_id', 'session_id', 'prompt', 'status', 'result',
                                          'created_at', 'updated_at', 'lease_expires_at')}

    def submit(self, device_id, data):
        digest = token_hash(canonical(data))
        with self.lock, self.db:
            self.db.execute('BEGIN IMMEDIATE')
            self._active_device(device_id)
            previous = self.db.execute('SELECT * FROM tasks WHERE id=?', (data['id'],)).fetchone()
            if previous:
                if previous['device_id'] != device_id or previous['request_hash'] != digest:
                    raise Conflict('task_id_conflict', task_id=data['id'])
                return {'task': self._task(previous)}
            executor = self.db.execute("SELECT 1 FROM devices WHERE kind='desktop' AND revoked=0 AND workspace_id=?",
                                       (data['workspace_id'],)).fetchone()
            if executor is None:
                raise Conflict('workspace_has_no_executor')
            now = self.clock()
            self.db.execute('''INSERT INTO tasks(id,device_id,workspace_id,session_id,prompt,request_hash,status,
                created_at,updated_at) VALUES(?,?,?,?,?,?,'queued',?,?)''',
                (data['id'], device_id, data['workspace_id'], data['session_id'], data['prompt'], digest, now, now))
            return {'task': self._task(self.db.execute('SELECT * FROM tasks WHERE id=?', (data['id'],)).fetchone())}

    def task(self, task_id, device):
        with self.lock, self.db:
            self._active_device(device['id'])
            self._expire()
            row = self.db.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
            if row is None:
                return None
            if row['device_id'] != device['id'] and not (device['kind'] == 'desktop' and row['workspace_id'] == device['workspace_id']):
                return None
            return {'task': self._task(row)}

    def claim(self, device, workspace_id):
        if device['kind'] != 'desktop' or workspace_id != device['workspace_id']:
            raise PermissionError('Device is not the executor of this workspace.')
        with self.lock, self.db:
            self.db.execute('BEGIN IMMEDIATE')
            self._active_device(device['id'])
            self._expire()
            row = self.db.execute("SELECT * FROM tasks WHERE workspace_id=? AND status='queued' ORDER BY created_at,id LIMIT 1",
                                  (workspace_id,)).fetchone()
            if not row:
                return {'task': None}
            secret, expiry = secrets.token_urlsafe(32), self.clock() + 60
            self.db.execute("UPDATE tasks SET status='leased',worker_id=?,lease_hash=?,lease_expires_at=?,updated_at=? WHERE id=?",
                            (device['id'], token_hash(secret), expiry, self.clock(), row['id']))
            updated = self.db.execute('SELECT * FROM tasks WHERE id=?', (row['id'],)).fetchone()
            return {'task': self._task(updated), 'lease_token': secret, 'lease_expires_at': expiry}

    def transition(self, task_id, device, lease_token, action, *, status=None, result=None):
        with self.lock, self.db:
            self.db.execute('BEGIN IMMEDIATE')
            self._active_device(device['id'])
            self._expire()
            row = self.db.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
            if (not row or device['kind'] != 'desktop' or row['worker_id'] != device['id']
                    or row['workspace_id'] != device['workspace_id'] or not row['lease_hash']
                    or not secrets.compare_digest(row['lease_hash'], token_hash(lease_token))):
                raise Conflict('invalid_or_expired_lease')
            if action == 'result' and row['status'] in ('succeeded', 'failed'):
                if row['status'] == status and row['result'] == result:
                    return {'task': self._task(row)}
                raise Conflict('task_already_finished')
            if action == 'start':
                if row['status'] != 'leased':
                    raise Conflict('task_not_leased')
                self.db.execute("UPDATE tasks SET status='running',lease_expires_at=?,updated_at=? WHERE id=?",
                                (self.clock() + 120, self.clock(), task_id))
            elif action == 'heartbeat':
                if row['status'] != 'running':
                    raise Conflict('task_not_running')
                self.db.execute('UPDATE tasks SET lease_expires_at=?,updated_at=? WHERE id=?',
                                (self.clock() + 120, self.clock(), task_id))
            elif action == 'result':
                if row['status'] != 'running':
                    raise Conflict('task_not_running')
                self.db.execute('UPDATE tasks SET status=?,result=?,lease_expires_at=NULL,updated_at=? WHERE id=?',
                                (status, result, self.clock(), task_id))
            else:
                raise ValueError('Unknown transition.')
            return {'task': self._task(self.db.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone())}
