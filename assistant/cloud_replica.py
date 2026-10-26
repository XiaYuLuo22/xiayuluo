"""Durable opt-in replica/outbox; cloud data is content, never executable input.

This store contains no device token, model credentials, screenshots or training
files. Pending edits survive restarts and conflicts are not silently discarded.
"""
import asyncio
import json
import os
from pathlib import Path
import sqlite3
from threading import RLock
from uuid import uuid4

from .cloud_client import CloudConflict, CloudSyncError, _identifier


ENTITIES = ('session', 'message', 'memory')
FIELDS = {
    'session': {'title', 'created_at'},
    'message': {'session_id', 'role', 'content', 'created_at'},
    'memory': {'tier', 'kind', 'content', 'created_at', 'accessed_at', 'expires_at', 'uses', 'source_session'},
}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def validate_payload(entity, operation, payload):
    if entity not in ENTITIES or operation not in ('upsert', 'delete') or not isinstance(payload, dict):
        raise CloudSyncError('云同步记录类型无效。')
    if operation == 'delete':
        if payload:
            raise CloudSyncError('删除标记不能包含附加内容。')
        return
    if set(payload) != FIELDS[entity] or len(canonical(payload).encode()) > 64000:
        raise CloudSyncError('云同步仅接受会话、文字消息和非临时记忆，不接受私钥、截图或训练数据字段。')
    if entity == 'memory' and payload.get('tier') not in ('first', 'second', 'permanent'):
        raise CloudSyncError('临时上下文不能上传云端。')
    if entity == 'message' and payload.get('role') not in ('user', 'assistant'):
        raise CloudSyncError('云同步消息角色无效。')
    if entity == 'session' and (not isinstance(payload.get('title'), str) or not 1 <= len(payload['title']) <= 200):
        raise CloudSyncError('云同步会话标题无效。')
    if entity in ('message', 'memory'):
        if not isinstance(payload.get('content'), str) or not 1 <= len(payload['content']) <= (2000 if entity == 'memory' else 64000):
            raise CloudSyncError('云同步文字内容无效或过长。')
    for name in ('created_at', 'accessed_at', 'expires_at'):
        if name in payload and not (name == 'expires_at' and payload[name] is None):
            if not isinstance(payload[name], str) or not 1 <= len(payload[name]) <= 64:
                raise CloudSyncError('云同步时间字段无效。')
    if entity == 'message':
        _identifier(payload['session_id'])
    if entity == 'memory':
        if payload['kind'] not in ('fact', 'procedure', 'episode'):
            raise CloudSyncError('云同步记忆类型无效。')
        if payload['source_session'] is not None:
            _identifier(payload['source_session'])
    for name, value in payload.items():
        if name == 'uses':
            if type(value) is not int or value < 0:
                raise CloudSyncError('云同步记忆计数无效。')
        elif value is not None and not isinstance(value, str):
            raise CloudSyncError('云同步文字字段无效。')


def validate_event(event, *, remote=False):
    required = {'event_id', 'entity', 'entity_id', 'operation', 'base_revision', 'payload'}
    if not isinstance(event, dict) or not required <= set(event):
        raise CloudSyncError('云同步事件字段不完整。')
    _identifier(event['event_id'])
    _identifier(event['entity_id'])
    if type(event['base_revision']) is not int or event['base_revision'] < 0:
        raise CloudSyncError('云同步记录版本无效。')
    validate_payload(event['entity'], event['operation'], event['payload'])
    if remote and (type(event.get('revision')) is not int or event['revision'] < 1
                   or type(event.get('cursor')) is not int or event['cursor'] < 1):
        raise CloudSyncError('云端事件游标或版本无效。')


def validate_push_receipt(receipt, submitted):
    """A 200 alone cannot acknowledge durable edits; validate the exact batch."""
    if (not isinstance(receipt, dict) or not isinstance(receipt.get('events'), list)
            or type(receipt.get('cursor')) is not int or receipt['cursor'] < 0
            or len(receipt['events']) != len(submitted)):
        raise CloudSyncError('云端未确认完整事件批次；原待发送内容保留。')
    expected = {event['event_id']: event for event in submitted}
    seen = set()
    for event in receipt['events']:
        validate_event(event, remote=True)
        identifier = event['event_id']
        if identifier in seen or identifier not in expected:
            raise CloudSyncError('云端确认事件编号不符；原待发送内容保留。')
        seen.add(identifier)
        if ({key: event[key] for key in expected[identifier]} != expected[identifier]
                or event['revision'] != event['base_revision'] + 1 or event['cursor'] > receipt['cursor']):
            raise CloudSyncError('云端确认内容或版本不符；原待发送内容保留。')


class CloudReplica:
    def __init__(self, path):
        if str(path) != ':memory:':
            selected = Path(path)
            selected.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = RLock()
        self.sync_lock = asyncio.Lock()
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        if str(path) != ':memory:' and os.name != 'nt':
            Path(path).chmod(0o600)
        with self.db:
            self.db.execute('PRAGMA journal_mode=WAL')
            self.db.executescript('''
                CREATE TABLE IF NOT EXISTS replica_state(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                INSERT OR IGNORE INTO replica_state VALUES ('cursor', '0');
                CREATE TABLE IF NOT EXISTS replica_entities(
                    entity TEXT NOT NULL, entity_id TEXT NOT NULL, revision INTEGER NOT NULL,
                    operation TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(entity, entity_id));
                CREATE TABLE IF NOT EXISTS replica_outbox(
                    position INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE,
                    entity TEXT NOT NULL, entity_id TEXT NOT NULL, event TEXT NOT NULL,
                    conflict INTEGER NOT NULL DEFAULT 0, UNIQUE(entity,entity_id));
                CREATE TABLE IF NOT EXISTS replica_tasks(
                    id TEXT PRIMARY KEY, task TEXT NOT NULL, state TEXT NOT NULL, result TEXT);
            ''')

    def close(self):
        with self.lock:
            self.db.close()

    @property
    def cursor(self):
        with self.lock:
            return int(self.db.execute("SELECT value FROM replica_state WHERE key='cursor'").fetchone()[0])

    def revision(self, entity, entity_id):
        with self.lock:
            row = self.db.execute('SELECT revision FROM replica_entities WHERE entity=? AND entity_id=?',
                                  (entity, entity_id)).fetchone()
            return row[0] if row else 0

    def enqueue_event(self, entity, entity_id, payload=None, *, operation='upsert', event_id=None,
                      base_revision=None):
        with self.lock, self.db:
            event = {'event_id': event_id or uuid4().hex, 'entity': entity, 'entity_id': entity_id,
                     'operation': operation, 'base_revision': self.revision(entity, entity_id) if base_revision is None else base_revision,
                     'payload': payload or {}}
            validate_event(event)
            try:
                self.db.execute('INSERT INTO replica_outbox(event_id,entity,entity_id,event) VALUES(?,?,?,?)',
                                (event['event_id'], entity, entity_id, canonical(event)))
            except sqlite3.IntegrityError:
                existing = self.db.execute('SELECT event FROM replica_outbox WHERE event_id=?', (event['event_id'],)).fetchone()
                if existing and json.loads(existing[0]) == event:
                    return event
                raise CloudConflict('该记录已有待同步修改；原修改保留，请先同步或处理冲突。') from None
            return event

    def pending(self, *, limit=100, include_conflicts=False):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise CloudSyncError('云同步批次大小无效。')
        with self.lock:
            rows = self.db.execute('SELECT event FROM replica_outbox WHERE (? OR conflict=0) ORDER BY position LIMIT ?',
                                   (include_conflicts, limit))
            result = []
            for row in rows:
                event = json.loads(row[0])
                if len(canonical({'events': [*result, event]}).encode()) > 900000:
                    break
                result.append(event)
            return result

    def has_pending(self, entity, entity_id):
        with self.lock:
            return self.db.execute('SELECT 1 FROM replica_outbox WHERE entity=? AND entity_id=?',
                                   (entity, entity_id)).fetchone() is not None

    def conflicts(self):
        with self.lock:
            return [json.loads(row[0]) for row in self.db.execute('SELECT event FROM replica_outbox WHERE conflict=1 ORDER BY position')]

    def acknowledge(self, event_ids):
        with self.lock, self.db:
            for event_id in event_ids:
                _identifier(event_id)
                self.db.execute('DELETE FROM replica_outbox WHERE event_id=?', (event_id,))

    def mark_conflicts(self, event_ids):
        with self.lock, self.db:
            for event_id in event_ids:
                self.db.execute('UPDATE replica_outbox SET conflict=1 WHERE event_id=?', (_identifier(event_id),))

    def apply_page(self, page):
        if (not isinstance(page, dict) or not isinstance(page.get('events'), list)
                or type(page.get('cursor')) is not int or type(page.get('more')) is not bool
                or len(page['events']) > 100):
            raise CloudSyncError('云端增量分页无效。')
        with self.lock, self.db:
            previous = self.cursor
            latest = previous
            for event in page['events']:
                validate_event(event, remote=True)
                position = event['cursor']
                if position <= previous:
                    continue
                if position <= latest or position > page['cursor']:
                    raise CloudSyncError('云端事件顺序无效；同步游标未推进。')
                latest = position
                current = self.revision(event['entity'], event['entity_id'])
                if event['revision'] <= current:
                    raise CloudSyncError('云端记录版本倒退；原本地内容保留。')
                self.db.execute('''UPDATE replica_outbox SET conflict=1
                    WHERE entity=? AND entity_id=? AND event_id<>?''',
                    (event['entity'], event['entity_id'], event['event_id']))
                self.db.execute('INSERT OR REPLACE INTO replica_entities VALUES(?,?,?,?,?)',
                    (event['entity'], event['entity_id'], event['revision'], event['operation'], canonical(event['payload'])))
            if page['cursor'] != latest:
                raise CloudSyncError('云端分页游标与内容不一致；同步游标未推进。')
            self.db.execute("UPDATE replica_state SET value=? WHERE key='cursor'", (str(latest),))
        return {'cursor': latest, 'conflicts': len(self.conflicts())}

    def entities(self, entity=None, *, include_deleted=False):
        if entity is not None and entity not in ENTITIES:
            raise CloudSyncError('云同步记录类型无效。')
        with self.lock:
            rows = self.db.execute('SELECT * FROM replica_entities WHERE (? IS NULL OR entity=?) AND (? OR operation<>?)',
                                   (entity, entity, include_deleted, 'delete'))
            return [{**dict(row), 'payload': json.loads(row['payload'])} for row in rows]

    def enqueue_task(self, workspace_id, session_id, prompt, *, task_id=None):
        task = {'id': task_id or uuid4().hex, 'workspace_id': _identifier(workspace_id),
                'session_id': _identifier(session_id), 'prompt': prompt}
        _identifier(task['id'])
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 12000:
            raise CloudSyncError('电脑任务需要非空文字，最多 12000 字符。')
        with self.lock, self.db:
            existing = self.db.execute('SELECT task FROM replica_tasks WHERE id=?', (task['id'],)).fetchone()
            if existing:
                if json.loads(existing[0]) != task:
                    raise CloudConflict('任务编号已存在，不会覆盖或重复执行。')
                return task
            self.db.execute('INSERT INTO replica_tasks VALUES(?,?,?,NULL)', (task['id'], canonical(task), 'pending'))
        return task

    def tasks(self, *, pending_only=False):
        with self.lock:
            rows = self.db.execute('SELECT task,state,result FROM replica_tasks WHERE (?=0 OR state=?)',
                                   (pending_only, 'pending'))
            return [{**json.loads(row[0]), 'local_state': row[1], 'result': json.loads(row[2]) if row[2] else None} for row in rows]

    async def sync_once(self, client):
        async with self.sync_lock:
            return await self._sync_once(client)

    async def _sync_once(self, client):
        pending = self.pending()
        if pending:
            try:
                receipt = await client.push_events(pending)
                validate_push_receipt(receipt, pending)
            except CloudConflict:
                self.mark_conflicts([event['event_id'] for event in pending])
            else:
                # The server contract is an atomic/idempotent batch, never a
                # partial success. A network exception deliberately retains it.
                self.acknowledge([event['event_id'] for event in pending])
        page = await client.pull_events(self.cursor)
        result = self.apply_page(page)
        for task in self.tasks(pending_only=True):
            payload = {key: task[key] for key in ('id', 'workspace_id', 'session_id', 'prompt')}
            try:
                receipt = await client.submit_task(payload)
            except CloudConflict:
                with self.lock, self.db:
                    self.db.execute("UPDATE replica_tasks SET state='conflict' WHERE id=?", (task['id'],))
                continue
            if not isinstance(receipt, dict) or not isinstance(receipt.get('task'), dict) or any(receipt['task'].get(key) != value for key, value in payload.items()):
                raise CloudSyncError('云端未确认相同任务；本地任务保留，稍后仍使用原编号核实。')
            with self.lock, self.db:
                self.db.execute("UPDATE replica_tasks SET state='submitted',result=? WHERE id=?", (canonical(receipt), task['id']))
        with self.lock:
            pending_count = self.db.execute('SELECT COUNT(*) FROM replica_outbox').fetchone()[0]
        return {**result, 'more': page['more'], 'pending': pending_count}
