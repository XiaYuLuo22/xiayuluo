import assert from 'node:assert/strict';
import { test } from 'node:test';
import { ReplySpeech, parseSpeechStatus, spokenReply, type SpeechNative, type SpeechStatus } from '../src/replySpeech.ts';

function deferred<T>() {
  let resolve!: (value: T) => void, reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
const base: SpeechStatus = { available: true, enabled: true, speaking: false, message: '系统中文声音就绪。',
  provider: 'system', voice_id: 'local-zh', owner: null };
const complete = { mode: 'chat' as const, complete: true, pending: false, aborted: false };

function fake() {
  let status = { ...base }, active: ReturnType<typeof deferred<boolean>> | null = null;
  const calls: string[] = [];
  const native: SpeechNative = {
    speechStatus: async () => JSON.stringify(status),
    configureSpeech: async enabled => {
      calls.push(`configure:${enabled}`); status.enabled = enabled;
      if (!enabled) { active?.resolve(false); active = null; status.speaking = false; status.owner = null; }
      return JSON.stringify(status);
    },
    stopSpeech: async () => {
      calls.push('stop'); active?.resolve(false); active = null; status.speaking = false; status.owner = null;
    },
    speakReply: text => {
      calls.push(text); active = deferred<boolean>(); status.speaking = true; status.owner = 'chat';
      return active.promise;
    },
  };
  const controller = new ReplySpeech(native);
  return { controller, native, calls, finish: () => { status.speaking = false; status.owner = null; active?.resolve(true); active = null; },
    getStatus: () => status, setStatus: (value: SpeechStatus) => { status = value; } };
}

test('native persisted setting is authoritative; nothing is authorized before status or when unsupported', async () => {
  const fixture = fake();
  assert.equal(fixture.controller.snapshot().known, false);
  assert.equal(await fixture.controller.completeTurn(0, 'hello', complete), false);
  assert.equal(fixture.calls.length, 0);
  fixture.setStatus({ ...base, enabled: false });
  await fixture.controller.setActive(true);
  const turn = await fixture.controller.beginTurn();
  assert.equal(fixture.controller.snapshot().status.enabled, false);
  assert.equal(await fixture.controller.completeTurn(turn, 'hello', complete), false);
  const unsupported = new ReplySpeech(undefined);
  await unsupported.setActive(true);
  assert.equal(unsupported.snapshot().known, false);
  assert.equal(await unsupported.completeTurn(await unsupported.beginTurn(), 'hello', complete), false);
});

test('one new completed ordinary reply is spoken once; history has no automatic observer', async () => {
  const fixture = fake(); await fixture.controller.setActive(true);
  const turn = await fixture.controller.beginTurn();
  const speaking = fixture.controller.completeTurn(turn, '你好，今天一起加油。', complete);
  assert.equal(fixture.controller.snapshot().pending, true);
  assert.equal(await fixture.controller.completeTurn(turn, '重复的历史轮询', complete), false);
  fixture.finish(); assert.equal(await speaking, true);
  assert.deepEqual(fixture.calls, ['stop', '你好，今天一起加油。']);
  await fixture.controller.refresh(); await fixture.controller.refresh();
  assert.equal(fixture.calls.length, 2);
});

test('failed, pending-action, cancelled, coding and empty responses are never spoken', async () => {
  const fixture = fake(); await fixture.controller.setActive(true);
  for (const options of [{ ...complete, complete: false }, { ...complete, pending: true },
    { ...complete, aborted: true }, { ...complete, mode: 'codex' as const }]) {
    assert.equal(await fixture.controller.completeTurn(await fixture.controller.beginTurn(), 'do not speak', options), false);
  }
  assert.equal(await fixture.controller.completeTurn(await fixture.controller.beginTurn(), '  ', complete), false);
  assert.ok(fixture.calls.every(call => call === 'stop'));
});

test('new question, session stop, off/on and background invalidate old turn generations', async () => {
  for (const interrupt of ['question', 'session', 'toggle', 'background', 'dispose']) {
    const fixture = fake(); await fixture.controller.setActive(true);
    const turn = await fixture.controller.beginTurn();
    const playing = fixture.controller.completeTurn(turn, 'old reply', complete);
    if (interrupt === 'question') await fixture.controller.beginTurn();
    if (interrupt === 'session') await fixture.controller.stop();
    if (interrupt === 'toggle') { await fixture.controller.configure(false); await fixture.controller.configure(true); }
    if (interrupt === 'background') { await fixture.controller.setActive(false); await fixture.controller.setActive(true); }
    if (interrupt === 'dispose') fixture.controller.dispose();
    assert.equal(await playing, false);
    assert.equal(await fixture.controller.completeTurn(turn, 'late completion', complete), false);
    assert.equal(fixture.calls.filter(call => call === 'old reply').length, 1);
    assert.equal(fixture.calls.includes('late completion'), false);
  }
});

test('switch off before the response completes cannot be undone by later enabling', async () => {
  const fixture = fake(); await fixture.controller.setActive(true);
  const old = await fixture.controller.beginTurn();
  await fixture.controller.configure(false); await fixture.controller.configure(true);
  assert.equal(await fixture.controller.completeTurn(old, 'old deferred network reply', complete), false);
  const fresh = await fixture.controller.beginTurn();
  const speaking = fixture.controller.completeTurn(fresh, 'fresh reply', complete);
  fixture.finish(); assert.equal(await speaking, true);
});

test('late native status and playback completion cannot overwrite a newer switch setting', async () => {
  const fixture = fake(); await fixture.controller.setActive(true);
  const status = deferred<string>();
  fixture.native.speechStatus = () => status.promise;
  const staleRead = fixture.controller.refresh();
  fixture.native.speechStatus = async () => JSON.stringify(fixture.getStatus());
  await fixture.controller.configure(false);
  status.resolve(JSON.stringify(base)); await staleRead;
  assert.equal(fixture.controller.snapshot().status.enabled, false);
  assert.equal(fixture.controller.snapshot().known, true);
});

test('native configuration failures are visible and reconcile actual state, never fake a successful mute', async () => {
  const fixture = fake(); await fixture.controller.setActive(true);
  fixture.native.configureSpeech = async () => { throw new Error('setting rejected'); };
  await fixture.controller.configure(false);
  assert.equal(fixture.controller.snapshot().status.enabled, true);
  assert.equal(fixture.controller.snapshot().known, true);
  assert.match(fixture.controller.snapshot().error, /setting rejected/);
});

test('speech failure leaves the successful text alone and never retries that reply', async () => {
  const fixture = fake(); await fixture.controller.setActive(true);
  fixture.native.speakReply = async () => { throw new Error('no local Chinese voice'); };
  const turn = await fixture.controller.beginTurn();
  assert.equal(await fixture.controller.completeTurn(turn, '已经显示的完整文字', complete), false);
  assert.match(fixture.controller.snapshot().error, /文字已回复，朗读失败/);
  assert.equal(await fixture.controller.completeTurn(turn, '再次重复', complete), false);
});

test('code and links are skipped, prose retained and exceptionally long replies are bounded', () => {
  const text = '# 简介\n\n**你好**，[文档](https://example.test/private?q=1)\n```python\nprint("不朗读代码")\n```\nhttps://example.test/secret';
  const spoken = spokenReply(text);
  assert.match(spoken, /简介/); assert.match(spoken, /你好，文档/);
  assert.match(spoken, /代码请查看文字/); assert.match(spoken, /链接请查看文字/);
  assert.doesNotMatch(spoken, /https:|print\(|example\.test/);
  assert.ok(spokenReply('长'.repeat(9000)).length <= 8000);
});

test('malformed native state fails closed and output remains a local system provider', () => {
  for (const raw of ['bad', '{}', JSON.stringify({ ...base, enabled: 'yes' }),
    JSON.stringify({ ...base, provider: 'remote' }), JSON.stringify({ ...base, voice_id: 123 }),
    JSON.stringify({ ...base, ready: 'yes' })]) {
    assert.throws(() => parseSpeechStatus(raw));
  }
  assert.deepEqual(parseSpeechStatus(JSON.stringify(base)), base);
  for (const ready of [false, true])
    assert.equal(parseSpeechStatus(JSON.stringify({ ...base, ready })).ready, ready);
});

test('effect cleanup and setup can reuse the controller without reviving old playback', async () => {
  const fixture = fake(); await fixture.controller.setActive(true);
  const old = await fixture.controller.beginTurn();
  fixture.controller.dispose(); fixture.controller.attach();
  await fixture.controller.setActive(true);
  assert.equal(await fixture.controller.completeTurn(old, 'stale', complete), false);
  const fresh = await fixture.controller.beginTurn();
  const playing = fixture.controller.completeTurn(fresh, '重新挂载后的新回答', complete);
  fixture.finish(); assert.equal(await playing, true);
});
