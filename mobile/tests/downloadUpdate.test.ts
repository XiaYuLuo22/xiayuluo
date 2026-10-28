import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFileSync } from 'node:fs';
import { downloadUpdate } from '../src/downloadUpdate.ts';
import { manifestUrl } from '../src/updateProtocol.ts';

test('GitHub one-click update checks and installs without browser navigation', async () => {
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
  assert.ok(view.includes('accessibilityLabel="GitHub 一键更新"'));
  assert.ok(view.includes('onPress={() => void download()}'));
  assert.ok(view.includes('await downloadUpdate<Release>({'));
  assert.ok(view.includes('await checkUpdate(abort.signal)'));
  assert.ok(view.includes('await installUpdate(target, notify)'));
  assert.ok(view.includes('系统安装确认不能跳过'));
});

test('update source is pinned GitHub Releases and mounting never downloads or installs', () => {
  const config = JSON.parse(readFileSync(new URL('../src/update-config.json', import.meta.url), 'utf8'));
  assert.equal(config.repository, 'XiaYuLuo22/xiayuluo');
  assert.equal(manifestUrl(config), 'https://github.com/XiaYuLuo22/xiayuluo/releases/latest/download/update.json');
  assert.throws(() => manifestUrl({ ...config, manifest_url: 'https://example.com/update.json' }));
  const view = readFileSync(new URL('../src/UpdateCard.tsx', import.meta.url), 'utf8');
  const effect = view.slice(view.indexOf('useEffect(() => {'), view.indexOf('  function begin()'));
  for (const operation of ['checkUpdate(', 'installUpdate(', 'downloadUpdate<', 'download()', 'check()'])
    assert.equal(effect.includes(operation), false, `mount must not invoke ${operation}`);
  const updates = readFileSync(new URL('../src/updates.ts', import.meta.url), 'utf8');
  assert.ok(updates.includes('fetchUpdateResource(manifestUrl(config), abort.signal, fetch)'));
  assert.ok(updates.includes('GitHub Releases 尚未发布可验证版本'));
  assert.ok(view.includes('无需电脑在线'));
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
