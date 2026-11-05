import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { settingsDisclosure } from '../src/settingsDisclosure.ts';

const read = (name: string) => readFileSync(new URL(`../${name}`, import.meta.url), 'utf8');

test('collapsed settings hide layout and accessibility descendants together', () => {
  assert.deepEqual(settingsDisclosure(false), {
    display: 'none', accessibilityElementsHidden: true, importantForAccessibility: 'no-hide-descendants',
  });
  assert.deepEqual(settingsDisclosure(true), {
    display: 'flex', accessibilityElementsHidden: false, importantForAccessibility: 'auto',
  });
  for (let i = 0; i < 10; i++) {
    assert.equal(settingsDisclosure(i % 2 === 0).accessibilityElementsHidden, i % 2 !== 0);
  }
});

test('details default closed, expose expanded state and keep children mounted', () => {
  const source = read('src/SettingsDetails.tsx');
  assert.ok(source.includes('useState(false)'));
  assert.ok(source.includes('accessibilityRole="button"'));
  assert.ok(source.includes('accessibilityState={{ expanded }}'));
  assert.ok(source.includes('onPress={() => setExpanded(current => !current)}'));
  assert.ok(source.includes('{children}'));
  assert.equal(/expanded\s*(?:&&|\?\s*children)/.test(source), false);
  assert.ok(source.includes('display: visibility.display'));
  assert.ok(source.includes('accessibilityElementsHidden={visibility.accessibilityElementsHidden}'));
  assert.ok(source.includes('importantForAccessibility={visibility.importantForAccessibility}'));
  for (const operation of ['useEffect', 'fetch(', 'voice.stop', 'speech.stop', 'AbortController', 'configure(', 'installUpdate('])
    assert.equal(source.includes(operation), false, `fold must not perform ${operation}`);
});

test('ordinary microphone and speech stop controls stay outside collapsed details', () => {
  const voice = read('src/VoiceCard.tsx');
  const opening = voice.indexOf('<SettingsDetails');
  assert.ok(opening > voice.indexOf('status.running ? \'停止监听\''));
  assert.ok(opening > voice.indexOf('status.message'));
  assert.ok(opening > voice.indexOf('查看语音对话'));
  const app = read('App.tsx');
  const speechStart = app.indexOf('回答朗读 · 系统中文音色');
  const speechDetails = app.indexOf('<SettingsDetails title="朗读方式与隐私说明">', speechStart);
  const speechCard = app.slice(speechStart, speechDetails);
  assert.ok(speechCard.includes('停止本次朗读'));
  assert.ok(speechCard.includes('speech.error || speech.status.message'));
  assert.ok(app.includes('accessibilityLabel="立即停止监听"'));
});

test('update progress, cancellation and retry remain outside explanatory details', () => {
  const source = read('src/UpdateCard.tsx');
  const details = source.indexOf('<SettingsDetails title="更新来源、校验与安装说明">');
  const visible = source.slice(0, details);
  assert.ok(details > 0);
  for (const control of ['{message}', 'accessibilityLabel="GitHub 一键更新"',
    '取消检查或下载', '再次打开安装器（重新校验）', '检查安装未知应用权限'])
    assert.ok(visible.includes(control), `must remain visible: ${control}`);
});

test('device settings keep primary connection and voice before advanced cloud controls', () => {
  const source = read('App.tsx');
  const page = source.slice(source.indexOf("{tab === 'devices' &&"), source.indexOf('<View style={s.tabBar}>'));
  assert.ok(page.indexOf('连接你的电脑') < page.indexOf('<VoiceCard'));
  assert.ok(page.indexOf('<VoiceCard') < page.indexOf('<UpdateCard'));
  assert.ok(page.indexOf('<UpdateCard') < page.indexOf('<CloudPanel'));
  assert.ok(page.includes('连接地址与配对管理'));
  assert.ok(page.includes('主动关怀与同步边界'));
  const speech = read('src/SpeechSettings.tsx');
  assert.ok(speech.includes('<SettingsDetails title="个性化声音 · 音色、语速与音量">'));
  assert.ok(speech.indexOf('!!speech.error') > speech.indexOf('</SettingsDetails>'));
  const cloud = read('src/CloudPanel.tsx');
  assert.ok(cloud.includes('<SettingsDetails title="云连接配置与凭据">'));
  assert.ok(cloud.indexOf('电脑直连（原聊天）') < cloud.indexOf('<SettingsDetails'));
  assert.ok(cloud.indexOf('style={s.notice}') < cloud.indexOf('<SettingsDetails'));
});
