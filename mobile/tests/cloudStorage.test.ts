import assert from 'node:assert/strict';
import { test } from 'node:test';
import { DurableCloudStore, MAX_CLOUD_STATE_BYTES, MAX_CLOUD_DIRECTORY_ITEMS,
  scopeForCloud, validateCloudConnection, type CloudFileAdapter } from '../src/cloudStorage.ts';

class Files implements CloudFileAdapter {
  contents = new Map<string, string>();
  failMove = false;
  failReadback = false;
  async list() { return [...this.contents.keys()]; }
  async read(name: string) {
    if (this.failReadback && name.startsWith('.cloud-state-')) return 'partial';
    return this.contents.get(name) ?? null;
  }
  async write(name: string, value: string) {
    if (this.contents.has(name)) throw new Error('exists');
    this.contents.set(name, value);
  }
  async move(from: string, to: string) {
    if (this.failMove) throw new Error('disk unavailable');
    if (this.contents.has(to)) throw new Error('exists');
    const value = this.contents.get(from); if (value === undefined) throw new Error('missing');
    this.contents.set(to, value); this.contents.delete(from);
  }
  async remove(name: string) { this.contents.delete(name); }
}
function state(cursor = 0) { return JSON.stringify({ schema: 1, cursor, records: [], outbox: [], tasks: [] }); }
function setup() { const files = new Files(); let id = 0; return { files, store: new DurableCloudStore(files, () => `own${++id}`) }; }

test('only bounded device credentials and HTTPS origins become cloud configuration', () => {
  const original = { schema: 1, origin: 'https://sync.example/', deviceId: 'phone1', token: 'device-private-token-123456' };
  const valid = validateCloudConnection(original);
  assert.equal(valid.origin, 'https://sync.example');
  assert.match(scopeForCloud(valid), /^[a-f0-9]{32}$/);
  assert.ok(!scopeForCloud(valid).includes(valid.token));
  assert.equal(scopeForCloud(valid), scopeForCloud(original));
  for (const change of [{ origin: 'http://sync.example' }, { origin: 'https://u:private-password@sync.example' },
    { origin: 'https://sync.example/path' }, { deviceId: '../other' }, { token: 'short' }, { password: 'private-password' }]) {
    assert.throws(() => validateCloudConnection({ ...original, ...change }), error =>
      error instanceof Error && !error.message.includes('private-password') && !error.message.includes(original.token));
  }
});

test('origin and device use separate opaque scopes; rotating credentials retains the same queue', () => {
  const original = { schema: 1 as const, origin: 'https://sync.example', deviceId: 'phone1', token: 'device-private-token-123456' };
  const scopes = [original, { ...original, origin: 'https://other.example' }, { ...original, deviceId: 'phone2' },
    { ...original, token: 'rotated-device-token-987654' }].map(scopeForCloud);
  assert.equal(new Set(scopes).size, 3);
  assert.equal(scopes[0], scopes[3]);
});

test('atomic snapshots survive reopen and retain only two verified generations', async () => {
  const { files, store } = setup();
  assert.equal(await store.load(), null);
  files.contents.set('unknown-user-note.txt', 'preserve');
  await store.save(state(1)); await store.save(state(2)); await store.save(state(3));
  assert.equal(await new DurableCloudStore(files).load(), state(3));
  assert.equal([...files.contents.keys()].filter(name => name.startsWith('state-')).length, 2);
  assert.equal(files.contents.get('unknown-user-note.txt'), 'preserve');
  assert.ok(![...files.contents.keys()].some(name => name.endsWith('.tmp')));
});

test('unchanged foreground polling does not rewrite the private archive', async () => {
  const { files, store } = setup(); await store.save(state(1));
  const original = new Map(files.contents);
  files.write = async () => { assert.fail('Identical polling must not write another snapshot'); };
  await store.save(state(1));
  assert.deepEqual(files.contents, original);
});

test('failed publication or partial write never removes the previous queue', async () => {
  const { files, store } = setup(); await store.save(state(1));
  const original = new Map(files.contents);
  files.failMove = true;
  await assert.rejects(store.save(state(2)), /disk unavailable/);
  assert.deepEqual(files.contents, original);
  assert.equal(await store.load(), state(1));
  files.failMove = false; files.failReadback = true;
  await assert.rejects(store.save(state(2)), /原文件保留/);
  assert.deepEqual(files.contents, original);
});

test('latest checksum corruption fails closed instead of reading old data or clearing', async () => {
  const { files, store } = setup(); await store.save(state(1)); await store.save(state(2));
  const latest = [...files.contents.keys()].filter(name => name.startsWith('state-')).sort().at(-1)!;
  files.contents.set(latest, state(99));
  const before = new Map(files.contents);
  await assert.rejects(store.load(), /原文件保留/);
  await assert.rejects(store.save(state(3)), /原文件保留/);
  assert.deepEqual(files.contents, before);
});

test('unpublished temporaries are ignored but malformed published names are not erased', async () => {
  const { files, store } = setup();
  files.contents.set('.cloud-state-interrupted.tmp', 'partial');
  assert.equal(await store.load(), null);
  await store.save(state(1));
  assert.equal(files.contents.get('.cloud-state-interrupted.tmp'), 'partial');
  files.contents.set('state-damaged.json', 'do not silently discard');
  await assert.rejects(store.load(), /原文件保留/);
});

test('snapshot budget, queue corruption and credential fields fail before writes', async () => {
  const { files, store } = setup(); await store.save(state(1)); const original = new Map(files.contents);
  for (const value of ['not json', ' '.repeat(MAX_CLOUD_STATE_BYTES + 1),
    JSON.stringify({ ...JSON.parse(state()), token: 'private-token' }),
    JSON.stringify({ ...JSON.parse(state()), records: [{ payload: { api_key: 'private-key' } }] }),
    JSON.stringify({ ...JSON.parse(state()), cursor: -1 }),
    JSON.stringify({ ...JSON.parse(state()), tasks: new Array(10001).fill({}) })]) {
    await assert.rejects(store.save(value), /原文件保留/);
    assert.deepEqual(files.contents, original);
  }
  for (let i = files.contents.size; i < MAX_CLOUD_DIRECTORY_ITEMS + 1; i++) files.contents.set(`unknown${i}`, 'x');
  await assert.rejects(store.load(), /原文件保留/);
});

test('per-store serialization keeps concurrent saves as separate generations', async () => {
  const { files, store } = setup();
  await Promise.all([store.save(state(1)), store.save(state(2)), store.save(state(3))]);
  assert.equal(await store.load(), state(3));
  assert.equal([...files.contents.keys()].filter(name => name.startsWith('state-')).length, 2);
});

test('failed final read-back removes only the new publication and retains original state', async () => {
  const { files, store } = setup(); await store.save(state(1));
  const original = new Map(files.contents), read = files.read.bind(files);
  files.read = async name => name.startsWith('state-000000000002-') ? state(999) : read(name);
  await assert.rejects(store.save(state(2)), /原文件保留/);
  assert.deepEqual(files.contents, original);
  assert.equal(await store.load(), state(1));
});

test('ambiguous concurrent generations are not guessed or silently overwritten', async () => {
  const { files, store } = setup(); await store.save(state(1));
  const name = [...files.contents.keys()].find(value => value.startsWith('state-'))!;
  files.contents.set(name.replace('-own1-', '-other-writer-'), files.contents.get(name)!);
  const original = new Map(files.contents);
  await assert.rejects(store.load(), /原文件保留/);
  await assert.rejects(store.save(state(2)), /原文件保留/);
  assert.deepEqual(files.contents, original);
});
