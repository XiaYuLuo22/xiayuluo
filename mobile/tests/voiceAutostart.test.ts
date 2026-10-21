import assert from 'node:assert/strict';
import { test } from 'node:test';
import { VoiceAutostart } from '../src/voiceAutostart.ts';

const ready = { ready: true, connected: true, online: true, foreground: true,
  available: true, running: false, busy: false };

test('launch waits for pairing, readiness and actual foreground without consuming attempt', () => {
  for (const field of ['ready', 'connected', 'online', 'foreground', 'available'] as const) {
    const gate = new VoiceAutostart();
    assert.equal(gate.shouldStart({ ...ready, [field]: false }), false);
    assert.equal(gate.attempted, false);
    assert.equal(gate.shouldStart(ready), true);
  }
});
test('one attempt only: polling, tab changes and foreground returns never restart a stopped service', () => {
  const gate = new VoiceAutostart();
  assert.equal(gate.shouldStart(ready), true);
  for (let i = 0; i < 10; i++) assert.equal(gate.shouldStart(ready), false);
  assert.equal(gate.shouldStart({ ...ready, foreground: false }), false);
  assert.equal(gate.shouldStart({ ...ready, running: true }), false);
  assert.equal(gate.shouldStart(ready), false); // Notification stop, spoken stop, error, permission denial.
});
test('stop wins even before prerequisites or permission prompt finish', () => {
  const gate = new VoiceAutostart();
  gate.stop();
  assert.equal(gate.suspended, true);
  assert.equal(gate.shouldStart(ready), false);
  gate.manualStart();
  assert.equal(gate.suspended, false);
  assert.equal(gate.shouldStart(ready), false);
});
test('existing native service is observed, not duplicated; a new launch may attempt again', () => {
  const gate = new VoiceAutostart();
  assert.equal(gate.shouldStart({ ...ready, running: true }), false);
  assert.equal(gate.shouldStart(ready), false);
  assert.equal(new VoiceAutostart().shouldStart(ready), true);
});
test('single flight does not consume an attempt while another manual start is pending', () => {
  const gate = new VoiceAutostart();
  assert.equal(gate.shouldStart({ ...ready, busy: true }), false);
  assert.equal(gate.attempted, false);
  gate.manualStart();
  assert.equal(gate.shouldStart(ready), false);
});
