import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFileSync } from 'node:fs';
import { AndroidUpdateInstaller, type UpdateDependencies } from '../src/updateInstaller.ts';
import { fetchUpdateResource, manifestUrl, type Release } from '../src/updateProtocol.ts';

function release(): Release {
  const asset = { url: 'https://github.com/XiaYuLuo22/xiayuluo/releases/download/v0.2.0/a.apk', size: 10, sha256: 'a'.repeat(64) };
  return { schema: 1, application: 'com.xiayuluo.assistant', version: '0.2.0', android_code: 20,
    notes: 'fixture', assets: { 'android-arm64': asset, 'windows-x64': { ...asset, url: asset.url.replace('.apk', '.zip') } } };
}
function fixture(changes: Partial<UpdateDependencies<string>> = {}) {
  const target = release(), calls: string[] = [];
  const installer = new AndroidUpdateInstaller<string>({
    check: async () => { calls.push('check'); return { available: true, release: target }; },
    download: async (_asset, _signal, progress) => { calls.push('download'); progress(5); progress(10); return 'owned-cache'; },
    verify: async file => { assert.equal(file, 'owned-cache'); calls.push('verify'); return true; },
    launch: async file => { assert.equal(file, 'owned-cache'); calls.push('launch'); },
    discard: async file => { assert.equal(file, 'owned-cache'); calls.push('discard'); }, ...changes,
  });
  return { installer, target, calls };
}

test('verified APK handoff is not installation success; reopen rechecks without redownload', async () => {
  const { installer, target, calls } = fixture();
  const phases: string[] = [];
  const unsubscribe = installer.subscribe(state => phases.push(state.phase));
  await installer.install(target);
  assert.equal(installer.status().phase, 'awaiting-system');
  assert.equal(installer.status().cached, true);
  assert.match(installer.status().message, /是否安装成功以重启后的版本为准/);
  assert.ok(phases.includes('downloading') && phases.includes('verifying'));
  await installer.reopen();
  assert.deepEqual(calls, ['check', 'download', 'verify', 'launch', 'check', 'verify', 'launch']);
  unsubscribe();
});

test('installer permission failure retains verified cache for explicit retry', async () => {
  let attempts = 0;
  const { installer, target, calls } = fixture({ launch: async () => { if (++attempts === 1) throw new Error('permission denied'); } });
  await assert.rejects(installer.install(target), /安装未知应用/);
  assert.equal(installer.status().phase, 'ready');
  assert.equal(installer.status().cached, true);
  await installer.reopen();
  assert.equal(attempts, 2);
  assert.equal(calls.filter(value => value === 'download').length, 1);
  assert.ok(!calls.includes('discard'));
});

test('cancelling during verification prevents launch and discards only new cache', async () => {
  let verified!: (value: boolean) => void, entered!: () => void;
  const waiting = new Promise<void>(resolve => { entered = resolve; });
  const { installer, target, calls } = fixture({ verify: async () => { entered(); return new Promise(resolve => { verified = resolve; }); } });
  const pending = installer.install(target);
  await waiting; installer.cancel(); verified(true);
  await assert.rejects(pending, /取消/);
  assert.equal(installer.status().phase, 'cancelled');
  assert.equal(installer.status().cached, false);
  assert.deepEqual(calls, ['check', 'download', 'discard']);
});

test('hash failure and changed signed release never launch', async () => {
  const corrupted = fixture({ verify: async () => false });
  await assert.rejects(corrupted.installer.install(corrupted.target), /校验失败/);
  assert.deepEqual(corrupted.calls, ['check', 'download', 'discard']);
  const changed = fixture({ check: async () => ({ available: true, release: { ...release(), android_code: 21 } }) });
  await assert.rejects(changed.installer.install(changed.target), /发布版本已变化/);
  assert.deepEqual(changed.calls, []);
});

test('download is single-flight and explicit cancellation keeps current app', async () => {
  let entered!: () => void;
  const waiting = new Promise<void>(resolve => { entered = resolve; });
  const { installer, target, calls } = fixture({ download: async (_asset, signal) => {
    entered(); return new Promise((_resolve, reject) => signal.addEventListener('abort', () => reject(new Error('cancelled'))));
  } });
  const pending = installer.install(target);
  await waiting;
  await assert.rejects(installer.install(target), /已有更新/);
  installer.cancel();
  await assert.rejects(pending, /取消/);
  assert.ok(!calls.includes('launch'));
  assert.equal(installer.status().phase, 'cancelled');
});

test('only fixed manifest and bounded trusted redirects are used without credentials', async () => {
  const expected = 'https://github.com/XiaYuLuo22/xiayuluo/releases/latest/download/update.json';
  assert.equal(manifestUrl({ repository: 'XiaYuLuo22/xiayuluo', manifest_url: expected, public_key: 'a'.repeat(64) }), expected);
  assert.throws(() => manifestUrl({ repository: 'XiaYuLuo22/xiayuluo', manifest_url: 'https://evil.example', public_key: '' }));
  const visits: string[] = [];
  const signal = new AbortController().signal;
  const result = await fetchUpdateResource(expected, signal, async (url, init) => {
    visits.push(url);
    assert.equal(init.credentials, 'omit'); assert.equal(init.redirect, 'manual');
    assert.ok(!JSON.stringify(init.headers).includes('Authorization'));
    return visits.length === 1 ? new Response(null, { status: 302, headers: { location: 'https://release-assets.githubusercontent.com/file' } })
      : new Response('verified');
  });
  assert.equal(await result.text(), 'verified'); assert.equal(visits.length, 2);
  let requests = 0;
  await assert.rejects(fetchUpdateResource(expected, signal, async () => {
    ++requests; return new Response(null, { status: 302, headers: { location: 'http://127.0.0.1/private' } });
  }), /受信任/);
  assert.equal(requests, 1);
  await assert.rejects(fetchUpdateResource(expected, signal, async () => new Response(null, { status: 302, headers: { location: expected } })), /次数过多/);
});

test('UI unsubscribes on unmount, offers explicit cancel and installer permission recovery', () => {
  const source = readFileSync(new URL('../src/UpdateCard.tsx', import.meta.url), 'utf8');
  assert.match(source, /mounted.current = false/); assert.match(source, /unsubscribe\(\)/);
  assert.match(source, /取消检查或下载/); assert.match(source, /再次打开安装器/); assert.match(source, /openInstallPermissionSettings/);
  assert.ok(!source.includes('Linking') && !source.includes('downloadPage'));
});
