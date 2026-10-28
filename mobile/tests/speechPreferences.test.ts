import assert from 'node:assert/strict';
import { test } from 'node:test';
import { ReplySpeech, parseSpeechStatus, parseSpeechVoices, validSpeechPreferences,
  type SpeechNative, type SpeechStatus } from '../src/replySpeech.ts';

const voice = { id: 'installed-zh', name: '系统中文', languages: ['zh-CN'], local: true,
  network_required: false, installed: true };
const initial: SpeechStatus = { available: true, enabled: true, speaking: false, message: '本机声音就绪',
  provider: 'system', voice_id: voice.id, owner: null, rate: 180, volume: 1, personalization_available: true };
const completed = { mode: 'chat' as const, complete: true, pending: false, aborted: false };

function fixture() {
  let status = { ...initial };
  const calls: string[] = [];
  const native: SpeechNative = {
    speechStatus: async () => JSON.stringify(status),
    configureSpeech: async enabled => { status.enabled = enabled; return JSON.stringify(status); },
    stopSpeech: async () => { calls.push('stop'); status.speaking = false; },
    speakReply: async text => { calls.push('speak:' + text); return true; },
    speechVoices: async () => { calls.push('voices'); return JSON.stringify([voice]); },
    configureSpeechPreferences: async (id, rate, volume) => {
      calls.push('preferences'); status = { ...status, voice_id: id || voice.id, rate, volume };
      return JSON.stringify(status);
    },
  };
  return { native, calls, controller: new ReplySpeech(native), status: () => status };
}

test('voice settings accept only bounded numeric values and installed offline Chinese options', () => {
  assert.ok(validSpeechPreferences(null, 180, 1));
  assert.ok(validSpeechPreferences(voice.id, 80, 0));
  for (const [id, rate, volume] of [[voice.id, '180', 1], [voice.id, true, 1], [voice.id, 180.5, 1],
    [voice.id, 301, 1], [voice.id, 180, NaN], [voice.id, 180, Infinity], ['', 180, 1], ['bad\nvoice', 180, 1]]) {
    assert.equal(validSpeechPreferences(id, rate, volume), false);
  }
  assert.deepEqual(parseSpeechVoices(JSON.stringify([voice])), [voice]);
  for (const bad of [{ ...voice, network_required: true }, { ...voice, installed: false },
    { ...voice, local: false }, { ...voice, languages: ['en-US'] }, { ...voice, id: '' }]) {
    assert.throws(() => parseSpeechVoices(JSON.stringify([bad])));
  }
  assert.throws(() => parseSpeechVoices(JSON.stringify([voice, voice])));
  assert.throws(() => parseSpeechVoices('x'.repeat(512001)));
});

test('legacy native modules remain supported for ordinary speech but cannot fake personalization', async () => {
  const { native, controller, calls } = fixture();
  delete native.speechVoices; delete native.configureSpeechPreferences;
  assert.equal(controller.personalizationSupported, false);
  await controller.setActive(true);
  await controller.loadVoices();
  await controller.configurePreferences(null, 180, .5);
  assert.match(controller.snapshot().error, /当前 APK 不支持/);
  await controller.configure(false);
  assert.equal(controller.snapshot().status.enabled, false);
  assert.deepEqual(calls, []);
});

test('voices load only on explicit request, are copied, and never start audio or microphone', async () => {
  const { controller, calls } = fixture();
  await controller.refresh();
  assert.deepEqual(calls, []);
  await controller.loadVoices();
  const view = controller.snapshot();
  assert.equal(view.voicesKnown, true);
  view.voices[0].languages.push('en');
  view.voices.length = 0;
  assert.deepEqual(controller.snapshot().voices, [voice]);
  assert.deepEqual(calls, ['voices']);
});

test('saving preferences cancels old turn generations without replay or changing microphone consent', async () => {
  const { controller, calls, status } = fixture();
  await controller.setActive(true);
  await controller.loadVoices();
  const old = await controller.beginTurn();
  await controller.configurePreferences(voice.id, 150, .4);
  assert.equal(controller.snapshot().status.rate, 150);
  assert.equal(controller.snapshot().status.volume, .4);
  assert.equal(status().enabled, true);
  assert.equal(await controller.completeTurn(old, '旧回复不可复活', completed), false);
  assert.deepEqual(calls, ['voices', 'stop', 'stop', 'preferences']);
});

test('bad, unknown or unverified preferences fail before native dispatch', async () => {
  const { controller, calls } = fixture();
  await controller.refresh();
  await controller.configurePreferences(voice.id, 180, 1);
  assert.deepEqual(calls, []);
  await controller.loadVoices();
  for (const args of [[voice.id, 79, 1], ['not-installed', 180, 1], [voice.id, 180, 2]] as const)
    await controller.configurePreferences(...args);
  assert.deepEqual(calls, ['voices']);
  assert.match(controller.snapshot().error, /请刷新并选择/);
});

test('native save failures preserve actual state and never claim successful settings', async () => {
  const { controller, native, calls } = fixture();
  await controller.refresh(); await controller.loadVoices();
  native.configureSpeechPreferences = async () => { throw new Error('本机保存失败'); };
  await controller.configurePreferences(voice.id, 150, .2);
  assert.equal(controller.snapshot().status.rate, 180);
  assert.equal(controller.snapshot().status.volume, 1);
  assert.equal(controller.snapshot().known, true);
  assert.match(controller.snapshot().error, /声音设置未确认/);
  assert.equal(calls.some(call => call.startsWith('speak:')), false);
});

test('native response must confirm the requested settings and cannot choose a network provider', async () => {
  const { controller, native } = fixture();
  await controller.refresh(); await controller.loadVoices();
  native.configureSpeechPreferences = async () => JSON.stringify(initial);
  await controller.configurePreferences(voice.id, 150, .2);
  assert.match(controller.snapshot().error, /系统尚未确认/);
  assert.throws(() => parseSpeechStatus(JSON.stringify({ ...initial, rate: '150' })));
  assert.throws(() => parseSpeechStatus(JSON.stringify({ ...initial, volume: true })));
  assert.throws(() => parseSpeechStatus(JSON.stringify({ ...initial, personalization_available: 'yes' })));
});

test('stop invalidates delayed settings responses and never permits a stale reply', async () => {
  const { controller, native, calls } = fixture();
  await controller.setActive(true); await controller.loadVoices();
  const old = await controller.beginTurn();
  let resolve!: (value: string) => void;
  native.configureSpeechPreferences = () => new Promise<string>(yes => { resolve = yes; });
  const pending = controller.configurePreferences(voice.id, 150, .2);
  await new Promise(yes => setImmediate(yes));
  await controller.stop();
  resolve(JSON.stringify({ ...initial, rate: 150, volume: .2 }));
  await pending;
  assert.equal(await controller.completeTurn(old, 'stale', completed), false);
  assert.equal(controller.snapshot().status.rate, 180);
  assert.equal(calls.some(call => call.startsWith('speak:')), false);
});
