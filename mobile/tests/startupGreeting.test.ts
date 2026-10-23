import assert from 'node:assert/strict';
import { test } from 'node:test';
import { STARTUP_GREETING, StartupGreeting } from '../src/startupGreeting.ts';

const ready = { ready: true, connected: true, online: true, foreground: true,
  visible: true, idle: true, speechReady: true };

test('startup greeting waits for a foreground paired, online, idle chat', () => {
  for (const flag of Object.keys(ready) as (keyof typeof ready)[]) {
    const greeting = new StartupGreeting();
    assert.equal(greeting.take({ ...ready, [flag]: false }), null);
    assert.equal(greeting.take(ready), STARTUP_GREETING);
  }
});

test('one local greeting per launch, never replayed after reconnection or cancelled speech', () => {
  const greeting = new StartupGreeting();
  assert.equal(greeting.take(ready), STARTUP_GREETING);
  assert.equal(greeting.take(ready), null);
  assert.equal(greeting.take({ ...ready, online: false }), null);
  assert.equal(greeting.take({ ...ready, visible: false }), null);
  assert.equal(greeting.take({ ...ready, foreground: false }), null);
  assert.equal(greeting.take(ready), null);
  assert.equal(new StartupGreeting().take(ready), STARTUP_GREETING);
});

test('welcome never claims microphone, activity access or execution is enabled', () => {
  assert.doesNotMatch(STARTUP_GREETING, /已开启|正在监听|正在观察|正在执行|唤醒/);
});
