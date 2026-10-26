import assert from 'node:assert/strict';
import { test } from 'node:test';
import { CloudConflict, CloudHTTPClient, CloudSyncEngine, cloudOrigin,
  type CloudEvent, type CloudPage, type CloudStateStore, type CloudTask, type CloudTransport } from '../src/cloudSync.ts';

class MemoryStore implements CloudStateStore {
  value: string | null = null;
  async load() { return this.value; }
  async save(value: string) { this.value = value; }
}

test('task polling persists actual results without resubmitting and rejects mismatched receipts', async () => {
  const { engine, transport, submitted } = setup();
  const task = await engine.enqueueTask('workspace1', 'session1', 'explicit computer task', 'task1');
  await engine.syncOnce();
  let polls = 0;
  transport.getTask = async () => { ++polls; return { task: { ...task, status: 'succeeded', result: 'actual result' } }; };
  const state = await engine.refreshTasks();
  assert.equal((state.tasks[0].result as { task: { status: string } }).task.status, 'succeeded');
  await engine.refreshTasks();
  assert.equal(polls, 1); assert.equal(submitted.length, 1);
  const second = await engine.enqueueTask('workspace1', 'session1', 'second explicit task', 'task2');
  await engine.syncOnce();
  transport.getTask = async () => ({ task: { ...second, id: 'wrong-task', status: 'running', result: null } });
  await assert.rejects(engine.refreshTasks(), /不匹配/);
  assert.equal((await engine.snapshot()).tasks[1].local_state, 'submitted');
  assert.equal(submitted.length, 2);
});

test('task refresh is bounded and rotates through non-terminal tasks', async () => {
  const { engine, transport } = setup();
  for (let index = 0; index < 25; index++) await engine.enqueueTask('workspace1', 'session1', `task${index}`, `task${index}`);
  await engine.syncOnce();
  const calls: string[] = [];
  transport.getTask = async id => { calls.push(id); return { task: { id, workspace_id: 'workspace1', session_id: 'session1', prompt: id, status: 'running', result: null } }; };
  await engine.refreshTasks(); assert.equal(calls.length, 20);
  await engine.refreshTasks(); assert.equal(calls.length, 40);
  assert.equal(new Set(calls).size, 25);
});
function setup() {
  const store = new MemoryStore(), events: CloudEvent[] = [], submitted: CloudTask[] = [];
  let cursor = 0, next = 1;
  const transport: CloudTransport = {
    async pushEvents(batch) {
      const start = events.length;
      events.push(...batch);
      return { events: batch.map((item, index) => ({ ...item, cursor: start + index + 1, revision: item.base_revision + 1 })), cursor: events.length };
    },
    async pullEvents(after): Promise<CloudPage> {
      const batch = events.slice(after).map((item, index) => ({ ...item, cursor: after + index + 1, revision: item.base_revision + 1 }));
      cursor = Math.max(cursor, after + batch.length);
      return { events: batch, cursor, more: false };
    },
    async submitTask(task) { submitted.push(task); return { task: { ...task, status: 'queued' } }; },
  };
  const engine = new CloudSyncEngine(store, transport, () => `id${next++}`);
  return { store, transport, engine, events, submitted };
}

test('cloud URLs are explicit HTTPS origins without embedded credentials', () => {
  assert.equal(cloudOrigin('https://cloud.example/'), 'https://cloud.example');
  for (const value of ['http://cloud.example', 'https://u:p@cloud.example',
    'https://cloud.example/path', 'https://cloud.example?token=secret']) assert.throws(() => cloudOrigin(value));
});

test('durable queued text survives restart; temporary/raw/private fields are refused', async () => {
  const { engine, store, transport } = setup();
  const first = await engine.enqueue('session', 'session1', { title: '本地未发送', created_at: 'now' });
  const restarted = new CloudSyncEngine(store, transport, () => 'newid');
  assert.deepEqual((await restarted.snapshot()).outbox, [first]);
  await assert.rejects(restarted.enqueue('memory', 'memory1', { tier: 'temporary', content: 'not uploaded' }));
  await assert.rejects(restarted.enqueue('session', 'other', { title: 'x', api_key: 'private' }));
  await assert.rejects(restarted.enqueue('message', 'other', { role: 'user', content: 'x', screenshot: 'png' }));
  assert.deepEqual((await restarted.snapshot()).outbox, [first]);
  const state = await restarted.syncOnce();
  assert.equal(state.cursor, 1); assert.deepEqual(state.outbox, []);
});

test('offline mutation retains the exact event id; 409 retains both sides', async () => {
  const { engine, transport } = setup();
  const first = await engine.enqueue('session', 'session1', { title: 'unsent', created_at: 'now' });
  transport.pushEvents = async () => { throw new Error('offline'); };
  await assert.rejects(engine.syncOnce());
  assert.equal((await engine.snapshot()).outbox[0].event_id, first.event_id);
  transport.pushEvents = async () => { throw new CloudConflict('version conflict'); };
  transport.pullEvents = async () => ({ cursor: 1, more: false, events: [{ ...first,
    event_id: 'remote', payload: { title: 'remote version', created_at: 'now' }, cursor: 1, revision: 1 }] });
  const state = await engine.syncOnce();
  assert.equal(state.outbox[0].conflict, true);
  assert.equal(state.outbox[0].payload.title, 'unsent');
  assert.equal(state.records[0].payload.title, 'remote version');
});

test('invalid pages never partially advance the durable cursor; tombstones are retained', async () => {
  const { engine, transport } = setup();
  transport.pullEvents = async () => ({ cursor: 2, more: false, events: [{
    event_id: 'event1', entity: 'session', entity_id: 'session1', operation: 'delete', base_revision: 0,
    payload: {}, cursor: 1, revision: 1 }] });
  await assert.rejects(engine.syncOnce());
  assert.equal((await engine.snapshot()).cursor, 0);
  transport.pullEvents = async () => ({ cursor: 1, more: false, events: [{
    event_id: 'event1', entity: 'session', entity_id: 'session1', operation: 'delete', base_revision: 0,
    payload: {}, cursor: 1, revision: 1 }] });
  const state = await engine.syncOnce();
  assert.equal(state.records[0].operation, 'delete');
});

test('task retries reuse the saved id; submitted tasks are never replayed by the client', async () => {
  const { engine, transport, submitted } = setup();
  const task = await engine.enqueueTask('workspace1', 'session1', '明确的电脑任务', 'task1');
  let fail = true;
  transport.submitTask = async item => {
    submitted.push(item);
    if (fail) { fail = false; throw new Error('response lost after server accepted'); }
    return { task: { ...item, status: 'queued' } };
  };
  await assert.rejects(engine.syncOnce());
  assert.equal((await engine.snapshot()).tasks[0].local_state, 'pending');
  await engine.syncOnce(); await engine.syncOnce();
  assert.deepEqual(submitted, [task, task]);
  await assert.rejects(engine.enqueueTask('workspace1', 'session1', 'different task', 'task1'));
});

test('transport forbids redirects, sanitizes errors, and never falls back to another host', async () => {
  const calls: string[] = [];
  const fakeFetch: typeof fetch = async (input, init) => {
    calls.push(String(input));
    assert.equal(init?.redirect, 'error');
    assert.equal((init?.headers as Record<string, string>).Authorization, 'Bearer private-device-token-123456');
    return new Response(JSON.stringify({ detail: 'do not display secrets' }), { status: 302 });
  };
  const client = new CloudHTTPClient('https://cloud.example', 'private-device-token-123456', fakeFetch);
  await assert.rejects(client.pullEvents(0), error => !String(error).includes('do not display secrets'));
  assert.deepEqual(calls, ['https://cloud.example/sync/v1/events?after=0&limit=100']);
});

test('HTTP success without an exact batch receipt never drops queued edits', async () => {
  const { engine, transport } = setup();
  const event = await engine.enqueue('session', 'session1', { title: 'local', created_at: 'now' });
  transport.pushEvents = async () => ({});
  await assert.rejects(engine.syncOnce());
  assert.deepEqual((await engine.snapshot()).outbox, [event]);
});
