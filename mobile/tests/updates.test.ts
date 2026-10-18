import assert from 'node:assert/strict';
import { test } from 'node:test';
import { ed25519 } from '@noble/curves/ed25519.js';
import { bytesToHex, utf8ToBytes } from '@noble/hashes/utils.js';
import { newer, verifyRelease, version } from '../src/updateProtocol.ts';

const key = new Uint8Array(32).fill(7);
const config = { repository: 'XiaYuLuo22/xiayuluo', manifest_url: '', public_key: bytesToHex(ed25519.getPublicKey(key)) };
function data() {
  return { schema: 1, application: 'com.xiayuluo.assistant', version: '0.2.0', android_code: 3, notes: '更新说明',
    assets: Object.fromEntries([['windows-x64', 'a.zip'], ['android-arm64', 'a.apk']].map(([target, name]) => [target,
      { url: `https://github.com/XiaYuLuo22/xiayuluo/releases/download/v0.2.0/${name}`, size: 123, sha256: 'a'.repeat(64) }])) };
}
function sign(value: unknown) { const payload = JSON.stringify(value); return JSON.stringify({ payload, signature: bytesToHex(ed25519.sign(utf8ToBytes(payload), key)) }); }
test('signed releases use strict Ed25519 and monotonic versions', () => {
  assert.equal(verifyRelease(sign(data()), config).version, '0.2.0');
  assert.equal(newer('0.2.0', '0.1.1'), true);
  assert.equal(newer('0.1.1', '0.1.1'), false);
  assert.equal(newer('0.1.0', '0.1.1'), false);
  assert.equal(newer('1.10.0', '1.9.99'), true);
  for (const bad of ['1.0', '01.2.3', '1.2.3-beta']) assert.throws(() => version(bad));
});
test('tampering, wrong key, malformed and oversized metadata fail closed', () => {
  assert.throws(() => verifyRelease(sign(data()).replaceAll('0.2.0', '9.9.9'), config));
  assert.throws(() => verifyRelease(sign(data()), { ...config, public_key: 'a'.repeat(64) }));
  assert.throws(() => verifyRelease('[]', config));
  assert.throws(() => verifyRelease(sign(data()) + ' '.repeat(32768), config));
});
test('signed invalid origins, old asset tags, hashes, sizes and package IDs fail', () => {
  for (const invalid of ['http://github.com/XiaYuLuo22/xiayuluo/releases/download/v0.2.0/a.apk',
    'https://evil.example/a.apk', 'https://github.com/other/repo/releases/download/v0.2.0/a.apk',
    'https://github.com/XiaYuLuo22/xiayuluo/releases/download/v0.1.0/a.apk']) {
    const value = data(); value.assets['android-arm64'].url = invalid;
    assert.throws(() => verifyRelease(sign(value), config));
  }
  for (const changes of [{ size: 0 }, { size: 536870913 }, { size: 1.5 }, { sha256: 'bad' }]) {
    const value = data(); Object.assign(value.assets['android-arm64'], changes);
    assert.throws(() => verifyRelease(sign(value), config));
  }
  assert.throws(() => verifyRelease(sign({ ...data(), application: 'another.app' }), config));
});
