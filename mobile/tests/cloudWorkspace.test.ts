import assert from 'node:assert/strict';
import { test } from 'node:test';
import { cloudRows, cloudTaskView, verifiedPhoneDevice } from '../src/cloudWorkspace.ts';
import type { CloudState } from '../src/cloudSync.ts';

test('connection only accepts an explicitly verified matching phone device', () => {
  assert.equal(verifiedPhoneDevice({ device: { id: 'phone1', kind: 'phone' } }), 'phone1');
  assert.equal(verifiedPhoneDevice({ device: { id: 'phone1', kind: 'phone' } }, 'phone1'), 'phone1');
  for (const receipt of [{ device: { id: 'phone1', kind: 'desktop' } }, { device: { id: 'phone1', kind: 'admin' } },
    { device: { id: 'another-device', kind: 'phone' } }, { device: { id: '../escape', kind: 'phone' } }, null, {}]) {
    assert.throws(() => verifiedPhoneDevice(receipt, 'phone1'), /对应手机设备/);
  }
});

test('cloud projections apply tombstones and pending edits without hiding conflict originals', () => {
  const state: CloudState = { schema: 1, cursor: 3, tasks: [], records: [
    { event_id: 'a', entity: 'session', entity_id: 'first', operation: 'upsert', base_revision: 0, revision: 1, cursor: 1,
      payload: { title: 'remote text', created_at: '2026-10-26T00:00:00Z' } },
    { event_id: 'b', entity: 'session', entity_id: 'deleted', operation: 'delete', base_revision: 1, revision: 2, cursor: 2, payload: {} },
  ], outbox: [
    { event_id: 'c', entity: 'session', entity_id: 'first', operation: 'upsert', base_revision: 0, conflict: true,
      payload: { title: 'preserved unsent conflict', created_at: '2026-10-26T00:00:00Z' } },
    { event_id: 'd', entity: 'session', entity_id: 'local', operation: 'upsert', base_revision: 0,
      payload: { title: 'offline text', created_at: '2026-10-26T01:00:00Z' } },
  ] };
  assert.deepEqual(cloudRows(state, 'session').map(item => [item.id, item.payload.title, item.pending]), [
    ['first', 'remote text', false], ['local', 'offline text', true],
  ]);
  assert.equal(state.outbox[0].payload.title, 'preserved unsent conflict');
});

test('task UI never treats queue submission or mismatched receipt as success', () => {
  const task = { id: 'task', workspace_id: 'workspace', session_id: 'session', prompt: 'explicit request', local_state: 'submitted' as const };
  assert.match(cloudTaskView(task).label, /尚未核实/);
  assert.match(cloudTaskView({ ...task, result: { task: { ...task, status: 'queued' } } }).label, /排队/);
  assert.equal(cloudTaskView({ ...task, result: { task: { ...task, status: 'queued' } } }).terminal, false);
  assert.equal(cloudTaskView({ ...task, result: { task: { ...task, id: 'other', status: 'succeeded', result: 'not ours' } } }).result, '');
  assert.equal(cloudTaskView({ ...task, result: { task: { ...task, status: 'succeeded', result: 'verified text' } } }).result, 'verified text');
  assert.match(cloudTaskView({ ...task, result: { task: { ...task, status: 'interrupted' } } }).label, /不自动重放/);
});
