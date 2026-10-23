import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFileSync } from 'node:fs';
import { downloadUpdate } from '../src/downloadUpdate.ts';

test('download button checks and installs without browser navigation', async () => {
  const calls: string[] = [];
  const release = { version: '0.2.0' };
  assert.equal(await downloadUpdate({
    check: async () => { calls.push('check'); return { available: true, release }; },
    approve: async value => { assert.equal(value, release); calls.push('approve'); return true; },
    install: async value => { assert.equal(value, release); calls.push('verified-install'); },
    noUpdate: () => assert.fail('update exists'),
  }), true);
  assert.deepEqual(calls, ['check', 'approve', 'verified-install']);
  const view = readFileSync(new URL('../src/UpdateCard.tsx', import.meta.url), 'utf8');
  assert.ok(!view.includes('Linking') && !view.includes('downloadPage'));
});

test('not published, current version, or cancelled never starts an install', async () => {
  for (const mode of ['unpublished', 'current', 'cancel']) {
    let informed = false;
    const task = () => downloadUpdate({
      check: async () => { if (mode === 'unpublished') throw new Error('not published'); return { available: mode !== 'current', release: {} }; },
      approve: async () => false,
      install: async () => assert.fail('must not install'),
      noUpdate: () => { informed = true; },
    });
    if (mode === 'unpublished') await assert.rejects(task, /not published/);
    else assert.equal(await task(), false);
    assert.equal(informed, mode === 'current');
  }
});

test('signature/download failures propagate rather than report installation success', async () => {
  await assert.rejects(() => downloadUpdate({
    check: async () => ({ available: true, release: {} }), approve: async () => true,
    install: async () => { throw new Error('invalid signature or hash'); }, noUpdate: () => assert.fail(),
  }), /signature or hash/);
});
