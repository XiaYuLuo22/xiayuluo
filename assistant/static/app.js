'use strict';

const $ = (selector) => document.querySelector(selector);
const debug = window.DebugConsole;
const state = { token: '', status: null, session: null, mode: 'chat', busy: false, controller: null,
  tier: 'first', action: null, plugin: null, mouseControl: { enabled: false, available: false },
  mouseKnown: false, mouseChanging: false, mouseVersion: 0,
  sessions: [], developer: false, historyStamp: '', syncing: false, syncFailed: false, provider: 'auto' };
const levels = [
  { id: 'temporary', title: '临时性记忆', label: 'TEMPORARY', detail: '当前对话 · 闲置 2 小时', note: '重启后清空' },
  { id: 'first', title: '一级记忆', label: 'LEVEL 01', detail: '近期片段 · 7 天', note: '检索 3 次晋升二级' },
  { id: 'second', title: '二级记忆', label: 'LEVEL 02', detail: '强化记忆 · 30 天', note: '每次检索续期' },
  { id: 'permanent', title: '永久记忆', label: 'PERMANENT', detail: '长期保留 · 不过期', note: '由你明确确认' },
];
const tierName = (tier) => levels.find((level) => level.id === tier)?.title || tier;
const kindName = (kind) => ({ fact: '事实 / 偏好', procedure: '做事方法', episode: '对话片段' }[kind] || kind);
const el = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};
let toastTimer;
function toast(message) {
  $('#toast').textContent = message;
  $('#toast').hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { $('#toast').hidden = true; }, 6000);
}
const guarded = (fn) => async (...args) => { try { await fn(...args); } catch (error) {
  debug.record('error', 'UI operation failed', { message: error.message });
  toast(error.message || '操作失败，请重试。');
} };
async function api(path, options = {}) {
  const ticket = debug.request(path, options);
  let response;
  try {
    response = await fetch(path, { ...options, headers: { 'Content-Type': 'application/json',
      'X-Assistant-Token': state.token, ...(options.headers || {}) } });
  } catch (error) {
    debug.record('error', `${ticket.method} ${path} · ${error.name === 'AbortError' ? 'aborted' : 'connection failed'}`, { name: error.name });
    throw error;
  }
  debug.response(ticket, response.status);
  if (!response.ok) {
    let message = `请求失败（${response.status}）`;
    try { const data = await response.json(); if (typeof data.detail === 'string') message = data.detail; else if (data.detail) message = '参数不符合要求，请检查后重试。'; } catch { /* Non-JSON response. */ }
    throw new Error(message);
  }
  return response;
}
const json = async (path, options) => (await api(path, options)).json();
const post = (path, body = {}) => json(path, { method: 'POST', body: JSON.stringify(body) });
const automaticMode = () => state.status?.automation?.mode === 'auto';
const approveLocally = (text) => automaticMode() || confirm(text);
const usingCodex = () => (state.provider === 'auto' ? state.status?.model.provider : state.provider) === 'codex';
let codexConnecting = false, codexLastAttempt = 0;
async function connectCodex(refresh = false) {
  if (!state.token || codexConnecting || state.busy || document.hidden) return;
  codexConnecting = true; codexLastAttempt = Date.now();
  $('#connect-codex').disabled = true;
  $('#codex-connection').textContent = '正在检测官方 Codex CLI 的 ChatGPT 登录…';
  try {
    await post(refresh ? '/api/codex/connect?refresh=true' : '/api/codex/connect');
    await refreshStatus();
  } catch (error) { $('#codex-connection').textContent = error.message; }
  finally { codexConnecting = false; $('#connect-codex').disabled = false; }
}
$('#connect-codex').addEventListener('click', guarded(() => connectCodex(true)));

// Native speech is separate from microphone/voice-lease authorization. No Web
// speech/network fallback, and no playback triggered by rendering old history.
let speechState = { available: false, enabled: false, speaking: false, generation: 0 };
let speechKnown = false, speechChanging = false, speechVersion = 0;
function renderSpeech() {
  const enabled = speechKnown && speechState.enabled;
  const disabled = speechChanging || !speechKnown || (!enabled && !speechState.available);
  $('#reply-speech').textContent = `朗读：${enabled ? '开' : '关'}`;
  $('#reply-speech').setAttribute('aria-pressed', String(enabled));
  $('#reply-speech').disabled = disabled;
  $('#reply-speech-setting').checked = !!enabled;
  $('#reply-speech-setting').disabled = disabled;
  $('#stop-speaking').hidden = !(speechState.speaking || speechState.phase === 'queued');
  $('#speech-status').textContent = !speechKnown ? '无法核实系统朗读状态；文字聊天仍可使用。'
    : `${speechState.message || (enabled ? '新回复完成后自动朗读。' : '回复朗读已关闭。')}${!speechState.available && speechState.reason ? ` ${speechState.reason}` : ''}${speechState.truncated ? ' 本轮只朗读了部分内容，全文请看聊天。' : ''}`;
  $('#reply-speech').title = $('#speech-status').textContent;
  renderSpeechProfile();
}
function acceptSpeech(info) {
  if (!info || typeof info.enabled !== 'boolean' || !Number.isInteger(info.generation)) throw new Error('朗读服务状态格式无效。');
  if (speechKnown && info.generation < speechState.generation) return;
  speechState = info; speechKnown = true; renderSpeech();
}
async function refreshSpeech() {
  const version = speechVersion;
  try {
    const info = await json('/api/speech');
    if (version === speechVersion) acceptSpeech(info);
  } catch { if (version === speechVersion) { speechKnown = false; renderSpeech(); } }
}
async function stopSpeech(quiet = false) {
  const version = ++speechVersion;
  if (!speechKnown) await refreshSpeech();
  if (speechKnown && !speechState.available && !speechState.speaking) return null;
  try {
    const info = await post('/api/speech/stop');
    if (version !== speechVersion) return null;
    acceptSpeech(info); return speechState.generation;
  }
  catch (error) {
    if (version === speechVersion) { speechKnown = false; renderSpeech(); }
    if (!quiet) toast(`停止朗读未确认：${error.message}`);
    return null;
  }
}
async function toggleSpeech() {
  if (speechChanging || !speechKnown) return;
  const enabled = !speechState.enabled;
  speechChanging = true; const version = ++speechVersion; renderSpeech();
  try {
    const info = await post('/api/speech', { enabled });
    if (version === speechVersion) acceptSpeech(info);
    else await refreshSpeech();
  }
  catch (error) { await refreshSpeech(); toast(`朗读设置失败：${error.message}`); }
  finally { speechChanging = false; renderSpeech(); }
}
async function speakCompletedReply(text, generation) {
  if (!text.trim() || generation === null || !speechKnown || !speechState.enabled ||
      !speechState.available || generation !== speechState.generation) return;
  const version = speechVersion;
  try {
    const info = await post('/api/speech/speak', { text, generation });
    if (version === speechVersion) acceptSpeech(info);
  }
  catch (error) {
    if (version !== speechVersion) return;
    // A speaker failure must not mark a successfully saved answer unfinished.
    toast(`文字回复已完成，朗读未开始：${error.message}`);
    await refreshSpeech();
  }
}
$('#reply-speech').addEventListener('click', guarded(toggleSpeech));
$('#reply-speech-setting').addEventListener('change', guarded(toggleSpeech));
$('#stop-speaking').addEventListener('click', () => void stopSpeech());

let speechVoices = [], speechProfileDirty = false, speechVoicesLoading = false;
const nativeControlOwner = () => localTrainingOwner() && state.status?.runtime?.presentation === 'desktop';
function renderSpeechProfile() {
  const owner = localTrainingOwner();
  $('#speech-personalization').hidden = !owner;
  const ready = owner && nativeControlOwner() && speechKnown && speechState.available;
  const disabled = !ready || speechChanging || speechVoicesLoading;
  for (const id of ['speech-voice', 'speech-rate', 'speech-volume', 'speech-voices-refresh', 'speech-profile-save']) $("#" + id).disabled = disabled;
  $('#speech-profile-preview').disabled = disabled || !speechState.enabled || speechProfileDirty;
  $('#speech-profile-stop').disabled = !owner || !speechKnown || (!speechState.speaking && speechState.phase !== 'queued');
  if (!speechProfileDirty) {
    $('#speech-rate').value = Number.isInteger(speechState.rate) ? speechState.rate : 175;
    $('#speech-volume').value = Number.isFinite(speechState.volume) ? Math.round(speechState.volume * 100) : 100;
    if (typeof speechState.voice_id === 'string') {
      if (![...$('#speech-voice').options].some(option => option.value === speechState.voice_id)) {
        const option = el('option', '', '当前系统声音'); option.value = speechState.voice_id; $('#speech-voice').append(option);
      }
      $('#speech-voice').value = speechState.voice_id;
    }
  }
  if (!ready && !speechVoicesLoading) $('#speech-profile-status').textContent = speechState.reason || '请在电脑原生 App 中使用本地系统语音；浏览器或手机不能配置此音色。';
}
for (const id of ['speech-voice', 'speech-rate', 'speech-volume']) $("#" + id).addEventListener('input', () => {
  speechProfileDirty = true; renderSpeechProfile();
  $('#speech-profile-status').textContent = '语音设置尚未保存；试听使用已保存设置。';
});
$('#speech-voices-refresh').addEventListener('click', guarded(async () => {
  if (!nativeControlOwner() || speechChanging || speechVoicesLoading) return;
  speechVoicesLoading = true; renderSpeechProfile();
  try {
    const result = await json('/api/speech/voices');
    if (!Array.isArray(result.items) || result.items.length > 128 || result.items.some(item =>
      !item || typeof item.id !== 'string' || !item.id || item.id.length > 2000 || typeof item.name !== 'string' || item.name.length > 500)) throw new Error('本地音色列表格式无效。');
    const selected = $('#speech-voice').value;
    speechVoices = result.items;
    $('#speech-voice').replaceChildren();
    for (const voice of speechVoices) { const option = el('option', '', voice.name); option.value = voice.id; $('#speech-voice').append(option); }
    if (!speechVoices.length) { const option = el('option', '', '没有可用的系统声音'); option.value = ''; $('#speech-voice').append(option); }
    if (speechVoices.some(voice => voice.id === selected)) $('#speech-voice').value = selected;
    else if (speechVoices.some(voice => voice.id === speechState.voice_id)) $('#speech-voice').value = speechState.voice_id;
    $('#speech-profile-status').textContent = speechVoices.length ? `找到 ${speechVoices.length} 个系统已安装声音；没有下载或克隆新声音。` : '未找到可用声音，请先在 Windows 安装本地中文语音。';
  } finally { speechVoicesLoading = false; renderSpeechProfile(); }
}));
$('#speech-profile-save').addEventListener('click', guarded(async () => {
  if (!nativeControlOwner() || !speechKnown || !speechState.available || speechChanging || speechVoicesLoading) return;
  const rate = Number($('#speech-rate').value), volume = Number($('#speech-volume').value);
  if (!Number.isInteger(rate) || rate < 80 || rate > 300 || !$('#speech-volume').value.trim() || !Number.isFinite(volume) || volume < 0 || volume > 100) throw new Error('语速需为80–300的整数，音量需在0–100%。');
  const voice = $('#speech-voice').value, version = ++speechVersion;
  speechChanging = true; renderSpeech();
  try {
    const info = await post('/api/speech', { enabled: speechState.enabled, ...(voice ? { voice_id: voice } : {}), rate, volume: volume / 100 });
    if (version === speechVersion) { speechProfileDirty = false; acceptSpeech(info); $('#speech-profile-status').textContent = '本地音色、语速和音量已保存；可点击试听。'; }
    else await refreshSpeech();
  } catch (error) { await refreshSpeech(); throw error; }
  finally { speechChanging = false; renderSpeech(); }
}));
$('#speech-profile-preview').addEventListener('click', guarded(async () => {
  if (!nativeControlOwner() || speechProfileDirty || speechChanging || !speechKnown || !speechState.enabled || !speechState.available) return;
  const version = speechVersion;
  const info = await post('/api/speech/speak', { text: '你好，我是夏鱼洛。这是当前保存的本地声音。', generation: speechState.generation });
  if (version === speechVersion) acceptSpeech(info);
}));
$('#speech-profile-stop').addEventListener('click', () => void stopSpeech());

let autonomyState = null, visualLoopState = null, localControlsLoading = false, localControlsChanging = false, localControlVersion = 0;
function renderAutonomy(info) {
  autonomyState = info;
  const native = nativeControlOwner(), ready = native && info?.available === true;
  if (!localControlsChanging) $('#autonomy-enabled').checked = !!info?.enabled;
  $('#autonomy-enabled').disabled = localControlsChanging || (!info?.enabled && !ready);
  $('#autonomy-status').textContent = info?.message || '主动决策状态未核实，不会自动开始任务。';
  for (const id of ['autonomy-goal', 'autonomy-criteria', 'autonomy-goal-save']) $("#" + id).disabled = localControlsChanging || !ready || !info?.enabled;
  $('#autonomy-decide').disabled = localControlsChanging || !ready || !info?.enabled || info?.goal?.status !== 'active';
  $('#autonomy-stop').disabled = !localTrainingOwner() || (!native && !info?.enabled && !localControlsChanging);
  $('#autonomy-decision').hidden = !info?.last_decision;
  $('#autonomy-decision').textContent = info?.last_decision ? JSON.stringify(info.last_decision, null, 2).slice(0, 8000) : '';
  renderLocalControlStop();
}
const measured = (value, suffix = '') => Number.isFinite(value) && value >= 0 ? `${value.toFixed(1)}${suffix}` : '—';
function renderVisualLoop(info) {
  visualLoopState = info;
  const ready = nativeControlOwner() && info?.available === true && (!info?.requires_mouse_control || info?.mouse_control_enabled === true);
  const running = info?.running === true;
  $('#visual-loop-status').textContent = `${info?.message || '本地视觉状态未核实，不会开始采样。'}${running ? info?.verified === false ? ' 上次状态为运行；当前未核实，可再次请求停止。' : info.phase === 'starting' ? ' 准备中，请在3秒内切到目标窗口；不执行鼠标。' : ' 正在进行限时影子测试，不执行鼠标。' : ''}${info?.requires_mouse_control && !info?.mouse_control_enabled ? ' 请先在聊天栏开启鼠标控制，再回来开始测试。' : ''}${info?.policy_ready === false ? ' 没有已加载策略，仅采样观察，不进行模型推理。' : ''}`;
  for (const id of ['visual-loop-goal', 'visual-loop-frequency', 'visual-loop-start']) $("#" + id).disabled = localControlsChanging || !ready || running;
  $('#visual-loop-stop').disabled = !localTrainingOwner() || (!nativeControlOwner() && !running && !localControlsChanging);
  $('#visual-loop-capture-fps').textContent = measured(info?.capture_fps, ' 帧/秒');
  $('#visual-loop-inference-fps').textContent = measured(info?.inference_fps, ' 次/秒');
  $('#visual-loop-p95').textContent = `${measured(info?.p95_capture_ms, ' ms')} / ${measured(info?.p95_inference_ms, ' ms')}`;
  $('#visual-loop-dropped').textContent = Number.isSafeInteger(info?.dropped_frames) && info.dropped_frames >= 0 ? String(info.dropped_frames) : '—';
  renderLocalControlStop();
}
function renderLocalControlStop() {
  $('#local-controls-stop').hidden = !localTrainingOwner() || !(autonomyState?.enabled || visualLoopState?.running || localControlsChanging);
}
async function refreshLocalControls() {
  for (const id of ['speech-personalization', 'autonomy-card', 'visual-loop-card']) $("#" + id).hidden = !localTrainingOwner();
  renderSpeechProfile();
  if (!localTrainingOwner() || localControlsLoading || localControlsChanging) return;
  localControlsLoading = true; const version = localControlVersion;
  try {
    await Promise.all([
      json('/api/autonomy').then(info => { if (version === localControlVersion) renderAutonomy(info); }).catch(error => { if (version === localControlVersion) renderAutonomy({ ...autonomyState, available: false, verified: false, message: `无法核实主动决策：${error.message} 不能假定已经停止。` }); }),
      json('/api/visual-loop').then(info => { if (version === localControlVersion) renderVisualLoop(info); }).catch(error => { if (version === localControlVersion) renderVisualLoop({ ...visualLoopState, available: false, verified: false, message: `无法核实本地视觉：${error.message}` }); }),
    ]);
  } finally { localControlsLoading = false; }
}
async function changeLocalControl(path, body, render) {
  if (!nativeControlOwner() || localControlsChanging) return;
  const version = ++localControlVersion;
  localControlsChanging = true; renderAutonomy(autonomyState); renderVisualLoop(visualLoopState);
  try { const info = await post(path, body); if (version === localControlVersion) render(info); }
  finally { if (version === localControlVersion) { localControlsChanging = false; await refreshLocalControls(); } }
}
async function stopLocalControls(scope = 'both', disableAutonomy = false) {
  if (!localTrainingOwner()) return;
  const version = ++localControlVersion;
  localControlsChanging = true; renderAutonomy(autonomyState); renderVisualLoop(visualLoopState);
  const operations = [];
  if (scope !== 'visual') operations.push(post(disableAutonomy ? '/api/autonomy' : '/api/autonomy/stop', disableAutonomy ? { enabled: false } : {}).then(info => { if (version === localControlVersion) renderAutonomy(info); }));
  if (scope !== 'autonomy') operations.push(post('/api/visual-loop/stop', {}).then(info => { if (version === localControlVersion) renderVisualLoop(info); }));
  const results = await Promise.allSettled(operations);
  if (version !== localControlVersion) return;
  localControlsChanging = false; await refreshLocalControls();
  if (results.some(result => result.status === 'rejected')) throw new Error('部分停止请求尚未确认，请检查本机状态并再次停止；不会自动重启。');
}
$('#autonomy-enabled').addEventListener('change', guarded(() => $('#autonomy-enabled').checked
  ? changeLocalControl('/api/autonomy', { enabled: true }, renderAutonomy) : stopLocalControls('autonomy', true)));
$('#autonomy-goal-save').addEventListener('click', guarded(() => {
  const goal = $('#autonomy-goal').value.trim(), criteria = $('#autonomy-criteria').value.trim();
  if (!goal || !criteria) throw new Error('请填写明确目标和完成标准。');
  return changeLocalControl('/api/autonomy/goals', { goal, success_criteria: criteria }, renderAutonomy);
}));
$('#autonomy-decide').addEventListener('click', guarded(() => changeLocalControl('/api/autonomy/decide', {}, renderAutonomy)));
$('#autonomy-stop').addEventListener('click', guarded(() => stopLocalControls('autonomy')));
$('#visual-loop-start').addEventListener('click', guarded(() => {
  const goal = $('#visual-loop-goal').value.trim(), frequency = Number($('#visual-loop-frequency').value);
  if (!goal || goal.length > 128 || !Number.isInteger(frequency) || frequency < 1 || frequency > 30) throw new Error('请填写最多128字的观察目标，目标采样频率需为1–30的整数。');
  return changeLocalControl('/api/visual-loop/start', { goal, frequency_hz: frequency }, renderVisualLoop);
}));
$('#visual-loop-stop').addEventListener('click', guarded(() => stopLocalControls('visual')));
$('#local-controls-stop').addEventListener('click', guarded(() => stopLocalControls()));
$('#local-controls-refresh').addEventListener('click', guarded(refreshLocalControls));
setInterval(() => {
  if (!document.hidden && !$('#view-settings').hidden && (autonomyState?.enabled || visualLoopState?.running)) void refreshLocalControls();
}, 1000);

let updateBusy = false;
let cloudSyncBusy = false;
let cloudTaskChanging = false;
function renderCloudTasks(info = {}) {
  $('#cloud-tasks-card').hidden = !localTrainingOwner();
  $('#cloud-task-enabled').checked = !!info.enabled;
  $('#cloud-task-enabled').disabled = cloudTaskChanging || (!info.enabled &&
    (!info.configured || info.startup_ready === false || state.status?.runtime.presentation !== 'desktop'));
  $('#cloud-tasks-status').textContent = `${info.message || '云任务默认关闭；配置独立云服务后才能本机授权。'}${info.unconfirmed_count ? ` 有 ${info.unconfirmed_count} 项结果未确认，请查看本机记录，不要直接重发。` : ''}`;
}
function renderCloudSync(info) {
  $('#cloud-sync-status').textContent = `${info.message || '尚未核实'}${info.last_sync ? ` 最近同步：${info.last_sync}` : ''}`;
  $('#cloud-sync-now').disabled = cloudSyncBusy || !info.configured || info.usable === false || info.startup_ready === false || info.phase === 'syncing' || info.phase === 'paused';
  renderCloudTasks(info.tasks);
}
async function refreshCloudSync() {
  $('#cloud-sync-card').hidden = !localTrainingOwner();
  $('#cloud-tasks-card').hidden = !localTrainingOwner();
  if ($('#cloud-sync-card').hidden || cloudSyncBusy) return;
  try { renderCloudSync(await json('/api/cloud-sync')); }
  catch (error) { $('#cloud-sync-status').textContent = `无法核实云同步状态：${error.message}`; $('#cloud-sync-now').disabled = true; }
}
$('#cloud-sync-now').addEventListener('click', guarded(async () => {
  if (cloudSyncBusy || !localTrainingOwner()) return;
  cloudSyncBusy = true; $('#cloud-sync-now').disabled = true;
  try { renderCloudSync(await post('/api/cloud-sync', {})); }
  finally { cloudSyncBusy = false; await refreshCloudSync(); }
}));
$('#cloud-task-enabled').addEventListener('change', guarded(async () => {
  if (cloudTaskChanging || !localTrainingOwner()) return;
  const enabled = $('#cloud-task-enabled').checked;
  cloudTaskChanging = true; $('#cloud-task-enabled').disabled = true;
  try { renderCloudTasks(await post('/api/cloud-tasks', { enabled })); }
  finally { cloudTaskChanging = false; await refreshCloudSync(); }
}));
$('#cloud-task-records-button').addEventListener('click', guarded(async () => {
  if (!localTrainingOwner()) return;
  const result = await json('/api/cloud-tasks/records');
  $('#cloud-task-records').textContent = result.items?.length
    ? result.items.map(item => `${item.id} · ${item.phase}\n${String(item.result || '').slice(0, 1000)}`).join('\n\n')
    : '暂无本机云任务执行记录。';
  $('#cloud-task-records').hidden = false;
}));
let trainingStatus = null, trainingBusy = false, trainingLoading = false;
const localTrainingOwner = () => !!state.token && ['localhost', '127.0.0.1', '[::1]'].includes(location.hostname)
  && !location.pathname.startsWith('/mobile');
function renderTrainingProtection(info) {
  if (!info || typeof info.root !== 'string' || typeof info.ready !== 'boolean') throw new Error('训练数据保护状态格式无效，请更新或重新打开 App。');
  trainingStatus = info;
  $('#training-status').textContent = info.message || (info.ready ? '训练数据目录已就绪。' : '训练数据目录尚未就绪，请先检查本机存储。');
  $('#training-ready').textContent = info.ready ? '就绪' : '需处理，更新前请先检查';
  $('#training-root').textContent = info.root || '尚未设置';
  const backup = info.last_backup;
  $('#training-backup-path').textContent = backup?.path || '本次运行暂无成功备份记录';
  $('#training-backup-size').textContent = backup && Number.isSafeInteger(backup.files) && Number.isSafeInteger(backup.bytes)
    ? `${backup.files.toLocaleString('zh-CN')} 个文件 · ${backup.bytes.toLocaleString('zh-CN')} 字节` : '';
  $('#backup-training').disabled = trainingBusy || !info.ready;
}
async function refreshTrainingProtection() {
  const card = $('#training-protection');
  card.hidden = !localTrainingOwner();
  if (card.hidden || trainingBusy || trainingLoading) return;
  trainingLoading = true; $('#backup-training').disabled = true;
  try { renderTrainingProtection(await json('/api/training/status')); }
  catch (error) {
    trainingStatus = null;
    $('#training-ready').textContent = '尚未核实';
    $('#training-status').textContent = `无法读取训练数据保护状态：${error.message} 原有数据不会因此删除，请稍后点击“刷新状态”。`;
    $('#backup-training').disabled = true;
  } finally { trainingLoading = false; }
}
async function backupTraining() {
  if (!localTrainingOwner() || trainingBusy || trainingLoading || !trainingStatus?.ready) return;
  trainingBusy = true; $('#backup-training').disabled = true;
  $('#backup-training').setAttribute('aria-busy', 'true');
  $('#training-backup-result').textContent = '正在创建本地训练数据备份，请等待完成…';
  try {
    const info = await post('/api/training/backup', {});
    renderTrainingProtection(info);
    if (!info.last_backup?.path) throw new Error('服务未返回成功备份路径，请刷新状态核实；不会自动重试。');
    $('#training-backup-result').textContent = `备份成功：${info.last_backup.path}`;
  } catch (error) {
    $('#training-backup-result').textContent = `备份未完成：${error.message} 请检查目录可写、剩余空间及文件占用后重试。不会删除或覆盖原有训练数据。`;
  } finally {
    trainingBusy = false;
    $('#backup-training').setAttribute('aria-busy', 'false');
    $('#backup-training').disabled = !trainingStatus?.ready;
  }
}
$('#backup-training').addEventListener('click', () => void backupTraining());
let voiceSession = null;
let voiceRunning = false;
async function refreshVoice() {
  try {
    const info = await json('/api/voice');
    voiceRunning = !!info.running;
    $('#voice-indicator').textContent = info.running ? '🎙' : '◌';
    $('#voice-indicator').title = `${info.running ? '点击停止监听 · ' : ''}${info.message || info.reason || '语音未运行'}`;
    $('#voice-indicator').setAttribute('aria-label', `${info.running ? '停止监听' : '语音状态'}：${info.message || info.reason || '未运行'}`);
    $('#voice-status').textContent = `${info.message || ''} ${info.available ? '' : info.reason || ''}`;
    $('#voice-start').disabled = !info.available || info.running;
    $('#voice-stop').disabled = !info.running;
    voiceSession = info.session_id || null;
    $('#voice-session').hidden = !voiceSession;
  } catch { $('#voice-status').textContent = '无法读取语音状态，请重新打开 App。'; }
}
$('#voice-start').addEventListener('click', guarded(async () => {
  if (!approveLocally('开启本地麦克风语音对话，并授权本次语音会话的 Codex 只读问答？\n开启后不用先叫名字，识别到的话会发送给 Codex；旁人或电视的声音也可能被识别，请留意环境并随时停止。最小化后仍运行，可能增加耗电。只发送识别文字、近期上下文和相关记忆，不上传或保存环境录音，不使用付费 API。编程、鼠标和记忆操作不在语音授权内。停止即撤销，退出 App 即停止。')) return;
  await post('/api/voice/start', { confirm: true }); await refreshVoice();
}));
$('#voice-stop').addEventListener('click', guarded(async () => {
  await post('/api/voice/stop'); await refreshVoice();
}));
$('#voice-indicator').addEventListener('click', guarded(async () => {
  if (voiceRunning) { await post('/api/voice/stop'); await refreshVoice(); }
  else await openView('settings');
}));
$('#voice-session').addEventListener('click', guarded(async () => {
  if (voiceSession && !state.busy) { await refreshSessions(); await loadSession(voiceSession); await openView('chat'); }
}));

// Optional activity suggestions are a separate, per-run screenshot grant. They
// never create a conversation, call a tool, or replay through reply TTS/history.
let proactiveState = { available: false, enabled: false, generation: 0, cursor: 0 };
let proactiveKnown = false, proactiveChanging = false, proactiveVersion = 0;
let proactivePolling = false, proactiveCursor = 0, proactivePollError = '';
function clearProactiveNotices() {
  $('#assistant-notices').querySelectorAll('.proactive-notice').forEach(node => node.remove());
  $('#assistant-notices').hidden = !$('#assistant-notices').childElementCount;
}
function transientNotice(kind, text, id) {
  if (typeof text !== 'string' || !text.trim() || text.length > 1200) return;
  const container = $('#assistant-notices');
  const notice = el('article', `assistant-notice ${kind === 'greeting' ? 'startup-greeting' : 'proactive-notice'}`);
  notice.dataset.noticeId = String(id);
  const title = kind === 'greeting' ? '夏鱼洛 · 问候' : kind === 'question' ? '夏鱼洛 · 随口问问' : '夏鱼洛 · 主动建议';
  const heading = el('div', 'assistant-notice-heading');
  const dismiss = el('button', 'text-button', '×');
  dismiss.type = 'button'; dismiss.setAttribute('aria-label', `关闭${title}`);
  dismiss.addEventListener('click', () => { notice.remove(); container.hidden = !container.childElementCount; });
  heading.append(el('span', '', title), dismiss);
  notice.append(heading, el('p', '', text), el('small', '', '仅本次窗口显示，不写入会话或记忆'));
  container.append(notice);
  while (container.childElementCount > 5) (container.querySelector('.proactive-notice') || container.firstElementChild).remove();
  container.hidden = false; scrollChat();
}
function renderProactive() {
  const enabled = proactiveKnown && proactiveState.enabled;
  const unavailable = proactiveKnown && !enabled && !proactiveState.available;
  const button = $('#proactive-control');
  button.textContent = proactiveKnown ? `主动建议：${enabled ? '开' : '关'}` : '主动建议：未核实';
  button.setAttribute('aria-pressed', String(enabled));
  button.setAttribute('aria-busy', String(proactiveChanging));
  button.disabled = proactiveChanging || unavailable;
  $('#proactive-setting').checked = !!enabled;
  $('#proactive-setting').disabled = proactiveChanging || unavailable;
  const interval = Number.isFinite(proactiveState.interval_seconds) ? proactiveState.interval_seconds : 120;
  const probability = Number.isFinite(proactiveState.probability) ? Math.round(proactiveState.probability * 100) : 30;
  const cooldown = Number.isFinite(proactiveState.cooldown_seconds) ? proactiveState.cooldown_seconds : 600;
  const limit = Number.isFinite(proactiveState.max_analyses_per_hour) ? proactiveState.max_analyses_per_hour : 3;
  const suggestions = Number.isFinite(proactiveState.max_notices_per_hour) ? proactiveState.max_notices_per_hour : 3;
  const provider = proactiveState.provider === 'codex' ? 'Codex · ChatGPT 登录' : proactiveState.provider === 'api' ? '所配置的视觉 API' : '当前所选引擎';
  const note = proactiveChanging ? '正在更新本机主动建议授权…'
    : !proactiveKnown ? '主动建议状态未核实，观察可能仍在运行；点击开关可请求关闭。'
    : enabled ? `${proactiveState.message || '已开启前台窗口观察。'} 使用${provider}；每 ${interval} 秒检查活动变化，每小时最多 ${limit} 次分析；${probability}% 概率提出建议，间隔至少 ${cooldown} 秒，每小时最多 ${suggestions} 次建议。${proactivePollError}`
    : proactiveState.available ? '主动建议已关闭，不会为此功能读取屏幕。开启后才会间歇读取当前前台窗口。'
    : proactiveState.reason || '主动建议暂不可用；不会假装正在观察。';
  $('#proactive-note').textContent = note;
  $('#proactive-status').textContent = `${proactiveState.phase ? `状态：${proactiveState.phase} · ` : ''}${note}`;
  button.title = note;
  if (!enabled) $('#activity-context').textContent = '活动感知已关闭；短期活动上下文已清除。';
}
$('#refresh-activity-context').addEventListener('click', guarded(async () => {
  const result = await json('/api/proactive/context');
  $('#activity-context').textContent = result.items?.length
    ? result.items.map(item => `${item.category || '活动'}：${item.activity || ''}（约 ${Math.max(0, Math.round(item.age_seconds || 0))} 秒前）`).join('\n')
    : '暂无有效的短期活动摘要；开启后也需要等待实际观察，不会补造历史。';
}));
function acceptProactive(info) {
  if (!info || typeof info.enabled !== 'boolean' || !Number.isInteger(info.generation)) throw new Error('主动建议状态格式无效。');
  if (proactiveKnown && info.generation < proactiveState.generation) return;
  if (!proactiveKnown || info.generation !== proactiveState.generation || !info.enabled) {
    clearProactiveNotices();
    const cursor = Number.isInteger(info.cursor) && info.cursor >= 0 ? info.cursor : 0;
    proactiveCursor = proactiveKnown && proactiveState.enabled && info.enabled ? Math.min(proactiveCursor, cursor) : cursor;
  }
  proactiveState = info; proactiveKnown = true; proactivePollError = ''; renderProactive();
}
async function refreshProactive() {
  const version = proactiveVersion;
  try {
    const info = await json('/api/proactive');
    if (version === proactiveVersion && !proactiveChanging) acceptProactive(info);
  } catch {
    if (version === proactiveVersion) { proactiveKnown = false; renderProactive(); }
  }
}
async function setProactive(enabled, provider = state.provider) {
  if (proactiveChanging) return;
  if (enabled && (!proactiveKnown || !proactiveState.available)) return;
  proactiveChanging = true; const version = ++proactiveVersion; renderProactive();
  try {
    const info = await post('/api/proactive', { enabled, ...(enabled ? { provider } : {}) });
    if (version === proactiveVersion) acceptProactive(info);
    if (info.enabled !== enabled) throw new Error('服务未接受主动建议设置。');
  } catch (error) {
    proactiveKnown = false;
    proactiveChanging = false;
    await refreshProactive();
    toast(`主动建议设置未确认：${error.message}`);
  } finally {
    proactiveChanging = false; renderProactive();
  }
}
function toggleProactive() {
  return setProactive(proactiveKnown && !proactiveState.enabled);
}
async function pollProactive() {
  if (!state.token || !proactiveKnown || !proactiveState.enabled || proactiveChanging || proactivePolling || document.hidden) return;
  proactivePolling = true;
  const version = proactiveVersion;
  try {
    const info = await json('/api/proactive');
    if (version !== proactiveVersion || proactiveChanging) return;
    acceptProactive(info);
    if (!proactiveState.enabled) return;
    const generation = proactiveState.generation;
    const result = await json(`/api/proactive/events?after=${proactiveCursor}`);
    if (version !== proactiveVersion || proactiveChanging || !proactiveState.enabled || generation !== proactiveState.generation) return;
    if (!Array.isArray(result.events) || !Number.isInteger(result.cursor) || result.cursor < proactiveCursor) throw new Error('提醒列表格式无效。');
    const seen = new Set();
    for (const item of result.events) {
      if (!item || item.generation !== generation || !Number.isInteger(item.id) || item.id <= proactiveCursor || item.id > result.cursor || seen.has(item.id) || !['suggestion', 'question'].includes(item.kind)) continue;
      seen.add(item.id);
      transientNotice(item.kind, item.message, item.id);
    }
    proactiveCursor = result.cursor;
  } catch {
    if (version === proactiveVersion) { proactivePollError = ' 提醒同步失败；观察可能仍运行，可随时关闭开关。'; renderProactive(); }
  } finally { proactivePolling = false; }
}
$('#proactive-control').addEventListener('click', guarded(toggleProactive));
$('#proactive-setting').addEventListener('change', guarded(toggleProactive));
setInterval(() => void pollProactive(), 5000);

let greetingKnown = false, greetingBusy = false;
async function claimGreeting() {
  if (!state.token || greetingKnown || greetingBusy) return;
  greetingBusy = true;
  try {
    const result = await post('/api/greeting');
    if (result.greeting?.id === 'startup') {
      greetingKnown = true;
      transientNotice('greeting', result.greeting.message, 'startup');
    } else if (!result.pending) greetingKnown = true;
    else setTimeout(() => void claimGreeting(), 1000);
  } catch { greetingKnown = true; } // Unsupported/headless hosts never get fabricated greetings.
  finally { greetingBusy = false; }
}
$('#download-update').addEventListener('click', async () => {
  if (updateBusy || state.busy) return toast('请先完成或停止当前任务。');
  const result = await checkForUpdates();
  if (result?.available && result.install_supported) await installSignedUpdate();
});
let lastUpdateCheck = 0;
let updateProgressGeneration = 0;
function watchUpdateProgress() {
  const generation = ++updateProgressGeneration;
  let busy = false;
  const poll = async () => {
    if (busy || generation !== updateProgressGeneration || !updateBusy) return;
    busy = true;
    try {
      const info = await json('/api/updates/progress');
      if (generation !== updateProgressGeneration || !updateBusy) return;
      const received = Number.isSafeInteger(info.received_bytes) ? info.received_bytes : 0;
      const total = Number.isSafeInteger(info.total_bytes) ? info.total_bytes : 0;
      const percent = total > 0 ? ` ${Math.min(100, Math.floor(received * 100 / total))}%` : '';
      if (info.phase !== 'idle' && typeof info.message === 'string' && info.message) {
        $('#update-status').textContent = info.message + (info.phase === 'downloading' ? percent : '');
      }
    } catch { /* Older hosts or brief reloads keep the honest foreground message. */ }
    finally { busy = false; }
  };
  const timer = setInterval(() => void poll(), 1000);
  return () => { if (generation === updateProgressGeneration) ++updateProgressGeneration; clearInterval(timer); };
}
async function checkForUpdates(automatic = false) {
  if (updateBusy || !state.token) return;
  if (automatic && (!state.status?.runtime.update_supported || document.hidden || state.busy)) return;
  lastUpdateCheck = Date.now();
  updateBusy = true; $('#check-update').disabled = true; $('#download-update').disabled = true; $('#install-update').hidden = true;
  $('#update-status').textContent = '正在检查签名更新清单…'; $('#update-notes').hidden = true;
  try {
    const result = await json('/api/updates');
    $('#update-status').textContent = result.available
      ? `发现 ${result.version}，当前 ${result.current}。${result.install_supported ? '可在此下载更新。' : '当前为源码版，请先安装 Windows 发行版。'}`
      : `当前 ${result.current}，暂无更高版本。`;
    $('#update-notes').textContent = result.notes; $('#update-notes').hidden = !result.notes;
    $('#install-update').hidden = !result.available || !result.install_supported;
    $('#update-available').hidden = !result.available;
    $('#update-available').title = result.available ? `发现 ${result.version}，打开更新设置` : '';
    if (result.available && automatic) toast(`发现夏鱼洛 ${result.version}，可在设置中更新。`);
    return result;
  } catch (error) {
    $('#update-status').textContent = error.message;
    $('#update-available').hidden = true;
    return null;
  }
  finally { updateBusy = false; $('#check-update').disabled = false; $('#download-update').disabled = false; }
}
$('#check-update').addEventListener('click', () => void checkForUpdates());
$('#update-available').addEventListener('click', guarded(() => openView('settings')));
document.addEventListener('visibilitychange', () => {
  if (!document.hidden && Date.now() - lastUpdateCheck >= 3600000) void checkForUpdates(true);
});
async function installSignedUpdate() {
  if (updateBusy || state.busy) return toast('请先完成或停止当前任务。');
  if (!approveLocally('下载并校验新版本，然后退出并重启夏鱼洛？配置、聊天和四级记忆保留，安装位置不变。')) return;
  updateBusy = true; $('#install-update').disabled = true; $('#check-update').disabled = true; $('#download-update').disabled = true;
  $('#update-status').textContent = '正在备份训练数据并准备下载，请勿关闭 App…';
  const stopProgress = watchUpdateProgress();
  try { await stopSpeech(true); await post('/api/updates/install', { confirm: true }); stopProgress(); $('#update-status').textContent = '安装包已校验，正在重启安装；新版启动成功后才能确认更新完成。'; }
  catch (error) { $('#update-status').textContent = error.message; }
  finally { stopProgress(); updateBusy = false; $('#install-update').disabled = false; $('#check-update').disabled = false; $('#download-update').disabled = false; }
}
$('#install-update').addEventListener('click', installSignedUpdate);
$('#import-update').addEventListener('click', async () => {
  if (updateBusy || state.busy) return toast('请先完成或停止当前任务。');
  if (!approveLocally('选择下载好的签名更新包，校验后退出并重启夏鱼洛？配置和四级记忆保留。')) return;
  updateBusy = true; $('#import-update').disabled = true; $('#check-update').disabled = true;
  $('#update-status').textContent = '请选择 .xyupdate 签名更新包；校验期间请勿关闭 App…';
  const stopProgress = watchUpdateProgress();
  try {
    const result = await post('/api/updates/import', { confirm: true });
    stopProgress();
    $('#update-status').textContent = result.cancelled ? '已取消，当前版本未改变。' : '签名和文件校验通过，正在重启更新…';
  } catch (error) { $('#update-status').textContent = error.message; }
  finally { stopProgress(); updateBusy = false; $('#import-update').disabled = !state.status?.runtime.update_supported; $('#check-update').disabled = false; }
});

function setMode(mode) {
  state.mode = mode;
  $('#mode-toggle').replaceChildren(document.createTextNode(mode === 'codex' ? '⌘ Codex 编程 ' : '◌ 日常对话 '), el('span', '', '⌄'));
  $('#prompt').placeholder = mode === 'codex' ? (automaticMode() ? '描述编程任务，发送后交给本机 Codex…' : '描述编程任务，确认后交给本机 Codex…') : '和夏鱼洛聊聊，或交给我一件事…';
  renderMouseControl();
  if (state.status) debug.runtime(state.status, state.session, mode);
}
async function openView(view) {
  if (!['chat', 'memory', 'plugins', 'settings', 'debug', 'tasks', 'devices'].includes(view)) return;
  if (view === 'debug' && !state.developer) return;
  document.querySelectorAll('.view').forEach((node) => { node.hidden = node.id !== `view-${view}`; });
  document.querySelectorAll('.nav-item').forEach((node) => node.classList.toggle('active', node.dataset.view === view));
  $('#view-title').textContent = { chat: '聊天', memory: '记忆', plugins: '工具箱', settings: '设置', debug: '事件日志', tasks: '待确认任务', devices: '设备与同步' }[view];
  if (view === 'memory') await renderMemories();
  if (view === 'debug') debug.refresh();
  if (view === 'tasks') await renderTasks();
  if (view === 'devices') await renderDevices();
  if (view === 'settings') await Promise.all([refreshTrainingProtection(), refreshCloudSync(), refreshLocalControls()]);
}
function setBusy(busy) {
  state.busy = busy;
  $('#send').hidden = busy;
  $('#stop').hidden = !busy;
  $('#prompt').disabled = busy;
  $('#new-chat').disabled = busy;
  $('#mode-toggle').disabled = busy;
  renderMouseControl();
  document.querySelectorAll('.copy-message').forEach((node) => { node.disabled = busy; });
}
function scrollChat() { const box = $('#chat-scroll'); box.scrollTop = box.scrollHeight; }
function renderAssistant(body, text) {
  // Streaming and restored messages share the same audited rendering path.
  // If bundles are missing, retain the original plain-text behavior.
  if (!window.marked || !window.DOMPurify || text.length > 100000) {
    body.classList.remove('markdown-body'); body.textContent = text; return;
  }
  try {
    const parsed = marked.parse(text, { gfm: true, breaks: true, async: false });
    const fragment = DOMPurify.sanitize(parsed, {
      ALLOWED_TAGS: ['p', 'br', 'strong', 'em', 'del', 'code', 'pre', 'blockquote',
        'ul', 'ol', 'li', 'a', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'hr',
        'table', 'thead', 'tbody', 'tr', 'th', 'td'],
      ALLOWED_ATTR: ['href', 'title', 'class'],
      ALLOW_DATA_ATTR: false, ALLOW_ARIA_ATTR: false, RETURN_DOM_FRAGMENT: true,
    });
    fragment.querySelectorAll('[class]').forEach((node) => {
      if (node.tagName !== 'CODE' || !/^language-[\w-]+$/.test(node.className)) node.removeAttribute('class');
    });
    fragment.querySelectorAll('a').forEach((link) => {
      try {
        const url = new URL(link.getAttribute('href'));
        if (!['http:', 'https:'].includes(url.protocol)) throw new Error('Unsupported link');
        link.target = '_blank'; link.rel = 'noopener noreferrer';
      } catch { link.removeAttribute('href'); }
    });
    // Wide tables scroll within the reply instead of breaking narrow layouts.
    fragment.querySelectorAll('table').forEach((table) => {
      const wrapper = el('div', 'markdown-table'); table.replaceWith(wrapper); wrapper.append(table);
    });
    body.classList.add('markdown-body'); body.replaceChildren(fragment);
  } catch {
    body.classList.remove('markdown-body'); body.textContent = text;
  }
}
function message(role, content = '') {
  $('#welcome').hidden = true;
  const row = el('article', `message ${role}`);
  const avatar = el('span', `avatar ${role === 'user' ? 'user-avatar' : 'assistant-avatar'}`, role === 'user' ? '你' : '✦');
  const holder = el('div', 'message-content');
  const label = el('div', 'message-label', role === 'user' ? '你' : '夏鱼洛');
  const body = el('div', 'message-body', content);
  if (role === 'assistant' && content) renderAssistant(body, content);
  holder.append(label, body);
  if (role === 'assistant') {
    const actions = el('div', 'message-actions');
    const copy = el('button', 'copy-message', '复制'); copy.setAttribute('aria-label', '复制回复');
    copy.addEventListener('click', guarded(async () => {
      if (state.busy) return toast('请等待回复完成后复制。');
      const text = body.innerText;
      let copied = false;
      if (navigator.clipboard?.writeText) {
        try { await navigator.clipboard.writeText(text); copied = true; }
        catch { /* System WebViews may require the legacy local clipboard path. */ }
      }
      if (!copied) {
        const field = el('textarea', 'clipboard-fallback', text); document.body.append(field); field.select();
        try { if (!document.execCommand('copy')) throw new Error('复制失败，请选择文字手动复制。'); }
        finally { field.remove(); }
      }
      toast('回复已复制。');
    }));
    actions.append(copy); holder.append(actions);
  }
  row.append(avatar, holder); $('#messages').append(row); scrollChat();
  return { row, body, holder, label };
}
function renderMouseControl() {
  const control = state.mouseControl;
  const button = $('#mouse-control');
  button.textContent = `鼠标控制：${control.enabled ? '开' : '关'}`;
  button.setAttribute('aria-pressed', String(control.enabled));
  button.setAttribute('aria-busy', String(state.mouseChanging));
  // Closing remains available while a reply is streaming or in programming mode.
  button.disabled = state.mouseChanging || (state.mouseKnown && !control.enabled && (!control.available || state.busy || state.mode !== 'chat'));
  const note = !state.mouseKnown ? '鼠标控制状态未核实；不会为新消息授权操作。点击开关可请求关闭，或在设置中刷新状态。'
    : control.enabled ? '仅明确的鼠标指令才会截图并发送给所选引擎，不是持续录屏。关闭开关会撤销授权并停止本窗口当前请求。'
    : !control.available ? `鼠标控制不可用：${control.reason || '本机桌面尚未就绪。'}`
    : state.mode !== 'chat' ? '鼠标控制请在日常对话中开启；编程消息不会携带鼠标授权。'
    : '鼠标控制已关闭。开启后，仅明确的鼠标指令才会截图并发送给所选引擎，不是持续录屏。';
  $('#mouse-control-note').textContent = state.mouseChanging ? '正在向本机服务更新鼠标控制状态…' : note;
  button.title = note;
}
function acceptMouseControl(control) {
  state.mouseKnown = !!control && typeof control.enabled === 'boolean';
  state.mouseControl = state.mouseKnown ? { ...control, enabled: control.enabled === true, available: control.available === true }
    : { enabled: false, available: false, reason: '当前服务未提供鼠标控制，请更新并重启。' };
  if (state.status) state.status.mouse_control = state.mouseControl;
  renderMouseControl();
}
async function toggleMouseControl() {
  if (state.mouseChanging) return;
  const enabled = state.mouseKnown && !state.mouseControl.enabled;
  if (enabled && (state.busy || state.mode !== 'chat' || !state.mouseControl.available)) return;
  state.mouseChanging = true; state.mouseVersion += 1; renderMouseControl();
  let failure;
  try {
    const control = await post('/api/mouse-control', { enabled });
    acceptMouseControl(control);
    if (control.enabled !== enabled) failure = new Error(control.reason || '服务未接受鼠标控制设置。');
  } catch (error) { failure = error; state.mouseKnown = false; }
  finally { state.mouseChanging = false; state.mouseVersion += 1; renderMouseControl(); }
  if (failure) {
    // A failed request is never treated as local consent or a successful revocation.
    await refreshStatus().catch(() => { state.mouseKnown = false; renderMouseControl(); });
    toast(`鼠标控制设置失败：${failure.message}`);
  }
  if (!enabled && state.mouseKnown && !state.mouseControl.enabled) state.controller?.abort();
}
async function refreshStatus() {
  const mouseVersion = state.mouseVersion;
  state.status = await json(`/api/status${state.session ? `?session_id=${encodeURIComponent(state.session)}` : ''}`);
  const status = state.status;
  if (mouseVersion === state.mouseVersion && !state.mouseChanging) acceptMouseControl(status.mouse_control);
  else status.mouse_control = state.mouseControl;
  $('#import-update').disabled = !status.runtime.update_supported || updateBusy || state.busy;
  const source = state.provider === 'auto' ? status.model.provider : state.provider;
  const ready = source === 'codex' ? status.model.codex_chat_available : status.model.api_configured;
  const connection = status.model.codex_connection;
  $('#codex-connection').textContent = connection.message;
  $('#model-status').textContent = ready ? (source === 'codex' ? connection.logged_in ? `Codex · ChatGPT 登录已识别，${automaticMode() ? '发送后直接调用' : '发送后确认'}` : `Codex · 已找到 CLI，${connection.phase === 'attention' ? '登录需处理' : '正在检测登录'}` : `${status.model.api_name} · API 已配置`) : (source === 'codex' ? '请安装并登录官方 Codex CLI' : 'API 待配置');
  $('#approval-mode').value = status.automation.mode;
  $('#automation-status').textContent = status.automation.warning || (automaticMode() ? '已授权默认同意：发送后直接执行，不逐次弹窗；切换设置不会重放旧请求。' : '当前逐次确认：工具请求等待你在电脑批准。');
  $('#execution-badge').textContent = automaticMode() ? '默认同意 · 自动执行' : '执行前需确认';
  $('#task-policy').textContent = automaticMode() ? '自动模式：新指令直接执行，结果在原会话显示。旧待确认请求不会自动重放，可在此单独处理或取消。' : '手机和当前电脑的请求在这里等待确认，2 分钟过期，不自动执行。';
  $('#model-dot').classList.toggle('off', !ready || (source === 'codex' && !connection.logged_in));
  $('#connection-hint').hidden = ready;
  $('#memory-summary').replaceChildren();
  levels.forEach((level, index) => {
    const row = el('div', 'memory-stat');
    const info = el('div', 'memory-stat-info', level.title); info.append(el('small', '', level.detail));
    row.append(el('span', 'level-icon', index === 3 ? '∞' : `0${index}`), info, el('span', 'memory-stat-count', String(status.memory[level.id])));
    $('#memory-summary').append(row);
  });
  $('#capabilities').replaceChildren();
  const capabilities = [{ title: '流式对话', available: status.model.configured },
    { title: 'Codex', available: status.codex.available }, { title: '聊天鼠标控制', available: status.mouse_control?.available === true }];
  capabilities.forEach((item) => {
    const node = el('div', 'capability'); node.append(el('span', `status-dot${item.available ? '' : ' off'}`),
      el('span', '', item.title), el('span', `capability-status${item.available ? '' : ' off'}`, item.available ? '已配置' : '待连接'));
    $('#capabilities').append(node);
  });
  renderPlugins(); renderSettings();
  debug.runtime(status, state.session, state.mode);
}
async function refreshSessions() {
  const sessions = await json('/api/sessions');
  state.sessions = sessions;
  renderSessions();
  return sessions;
}
function renderSessions() {
  const query = $('#session-search').value.trim().toLocaleLowerCase();
  const sessions = state.sessions.filter((session) => session.title.toLocaleLowerCase().includes(query));
  $('#session-list').replaceChildren(); $('#session-count').textContent = String(state.sessions.length);
  if (!sessions.length) $('#session-list').append(el('p', 'history-empty', query ? '没有找到相关会话。' : '还没有聊过。发送消息，开始第一段对话。'));
  sessions.forEach((session) => {
    const row = el('div', `session-row${session.id === state.session ? ' selected' : ''}`);
    const button = el('button', '', session.title); button.title = session.title;
    button.addEventListener('click', guarded(() => loadSession(session.id)));
    const remove = el('button', 'session-delete', '×'); remove.setAttribute('aria-label', `删除对话：${session.title}`);
    remove.addEventListener('click', guarded(async () => {
      if (state.busy) return toast('请先等待任务完成或停止。');
      if (!approveLocally('删除这段对话及从它产生的记忆？手动另存的记忆会保留。')) return;
      if (state.session === session.id) await stopSpeech(true);
      await json(`/api/sessions/${session.id}`, { method: 'DELETE' });
      if (state.session === session.id) { state.session = null; resetChat(); }
      await refreshSessions(); await refreshStatus();
    }));
    row.append(button, remove); $('#session-list').append(row);
  });
}
function resetChat() {
  state.historyStamp = '';
  $('#messages').replaceChildren(); $('#welcome').hidden = false;
  $('#retrieval-note').hidden = true;
}
async function loadSession(id, { navigate = true } = {}) {
  if (state.busy) return toast('请先等待任务完成或停止。');
  if (state.session !== id) await stopSpeech(true);
  const data = await json(`/api/sessions/${id}`);
  if (state.session !== id) debug.reset();
  state.session = id; resetChat();
  data.messages.forEach((item) => message(item.role, item.content));
  state.historyStamp = historyStamp(data.messages);
  if (navigate) await openView('chat');
  await refreshSessions(); await refreshStatus(); scrollChat();
}
async function ensureSession() {
  if (!state.session) state.session = (await post('/api/sessions')).id;
  return state.session;
}
async function newChat() {
  if (state.busy) return;
  await stopSpeech(true);
  state.session = (await post('/api/sessions')).id;
  debug.reset();
  resetChat(); setMode('chat'); await openView('chat'); await refreshSessions(); await refreshStatus(); $('#prompt').focus();
}

async function consumeStream(path, body, target) {
  state.controller = new AbortController(); setBusy(true);
  debug.begin(path);
  target.body.replaceChildren(el('span', 'thinking', '夏鱼洛正在处理'));
  let text = '', finished = false, pendingAction = false, reader;
  let speechGeneration = null;
  let outcome = 'failed';
  try {
    speechGeneration = await stopSpeech(true);
    const response = await api(path, { method: 'POST', body: JSON.stringify(body), signal: state.controller.signal });
    reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = '';
    while (true) {
      const { value, done } = await reader.read();
      buffer += done ? decoder.decode() : decoder.decode(value, { stream: true });
      let boundary;
      while ((boundary = buffer.indexOf('\n\n')) !== -1) {
        const block = buffer.slice(0, boundary); buffer = buffer.slice(boundary + 2);
        const data = block.split('\n').filter((line) => line.startsWith('data:')).map((line) => line.slice(5).trimStart()).join('\n');
        if (!data) continue;
        const event = JSON.parse(data);
        debug.stream(event);
        if (event.type === 'text') { text += event.text; renderAssistant(target.body, text); scrollChat(); }
        if (event.type === 'status') target.label.textContent = `夏鱼洛 · ${event.message}`;
        if (event.type === 'error') throw new Error(event.message);
        if (event.type === 'memory') {
          const count = event.items.length; $('#retrieval-note').hidden = !count;
          $('#retrieval-note').textContent = count ? `▦ 本轮参考了 ${count} 条相关记忆 · 可在四级记忆中管理` : '';
          $('#retrieval-note').title = event.items.map((item) => `${tierName(item.tier)}：${item.content}`).join('\n');
        }
        if (event.type === 'action') { pendingAction = true; addActionCard(target.holder, event.action); }
        if (event.type === 'done') finished = true;
      }
      if (done) break;
    }
    if (!finished) throw new Error('连接提前结束，本轮回复未完成。');
    if (!text) target.body.textContent = '任务已完成。';
    outcome = 'completed';
    if (!pendingAction && !state.controller.signal.aborted) await speakCompletedReply(text, speechGeneration);
  } catch (error) {
    target.row.classList.add('error');
    const note = error.name === 'AbortError' ? '已停止。已执行的操作不会撤回。' : error.message;
    renderAssistant(target.body, text ? `${text}\n\n[未完成] ${note}` : note);
    outcome = error.name === 'AbortError' ? 'stopped' : 'failed';
    debug.record('error', outcome === 'stopped' ? 'stream stopped by user' : 'stream failed', { message: note });
  } finally {
    debug.end(outcome);
    if (reader) await reader.cancel().catch(() => {});
    setBusy(false); state.controller = null;
    await refreshSessions().catch(() => {}); await refreshStatus().catch(() => {}); scrollChat(); $('#prompt').focus();
  }
}
async function sendMessage(event) {
  event.preventDefault(); if (state.busy || state.mouseChanging) return;
  const prompt = $('#prompt').value.trim(); if (!prompt) return;
  const session = await ensureSession();
  $('#prompt').value = '';
  message('user', prompt);
  const target = message('assistant');
  await consumeStream('/api/chat', { session_id: session, message: prompt, mode: state.mode, provider: state.provider === 'auto' ? undefined : state.provider,
    mouse_control: state.mode === 'chat' && state.mouseKnown && state.mouseControl.enabled }, target);
}

function actionSummary(action) {
  if (action.tool === 'codex_task') return `项目目录（当前配置）：${state.status.codex.workspace}\n提交工作区：${action.workspace?.name || '未设置'}\n工作区编号：${action.workspace?.id || '未设置'}\n提交沙箱：${action.workspace?.sandbox || state.status.codex.sandbox}\n目录或权限改变后必须重新提交。\n\n任务：${action.arguments.prompt}`;
  if (action.tool === 'desktop_click') return `主屏幕坐标：(${action.arguments.x}, ${action.arguments.y})\n按键：${action.arguments.button === 'right' ? '右键' : '左键'}\n截图 ID：${action.arguments.screenshot_id}`;
  if (action.tool === 'desktop_mouse') {
    const names = { click: '左键单击', double_click: '左键双击', right_click: '右键单击', move: '移动鼠标', scroll: '滚动' };
    return `单次鼠标操作：${names[action.arguments.action] || action.arguments.action}\n主屏幕坐标：(${action.arguments.x}, ${action.arguments.y})${action.arguments.action === 'scroll' ? `\n滚动量：${action.arguments.scroll}` : ''}\n截图 ID：${action.arguments.screenshot_id}`;
  }
  if (action.tool === 'remember') return `层级：${tierName(action.arguments.tier)}\n类型：${kindName(action.arguments.kind)}\n\n${action.arguments.content}`;
  return JSON.stringify(action.arguments, null, 2);
}
function addActionCard(holder, action) {
  const card = el('div', 'action-card'); card.append(el('strong', '', `${action.title} · 等待你的确认`), el('p', '', actionSummary(action)));
  const button = el('button', 'primary-button', '查看并确认'); button.addEventListener('click', guarded(() => showAction(action, button)));
  card.append(button); holder.append(card); scrollChat();
}
async function showAction(action, button = null) {
  if (state.busy) return toast('请等待当前请求完成后再确认。');
  if (automaticMode()) {
    if (button) { button.disabled = true; button.textContent = '自动执行中'; }
    await loadSession(action.session_id);
    await consumeStream(`/api/actions/${action.id}/approve`, {}, message('assistant'));
    return;
  }
  state.action = { ...action, button };
  debug.record('tool', `${action.tool} · confirmation opened`, { action });
  $('#action-title').textContent = `确认${action.title}`;
  $('#action-description').textContent = action.tool === 'codex_task'
    ? '确认后将调用本机 Codex。workspace-write 模式可能修改指定项目；CLI 登录与权限需在本机配置。'
    : action.tool === 'codex_chat' ? '确认后发送以下近期会话及参考记忆给官方 Codex CLI。使用 ChatGPT 登录而非 API 密钥；独立临时目录、只读沙箱，禁用命令、插件及浏览器等入口。仍受会员额度限制，不保证逐字输出。'
    : ['desktop_click', 'desktop_mouse'].includes(action.tool) ? '确认后将在实际桌面执行一次鼠标操作；聊天鼠标开关仍须开启。请先核实目标位置和当前屏幕。' : '确认后才会执行插件操作。';
  $('#action-details').textContent = actionSummary(action); $('#action-dialog').showModal();
}
async function declineAction() {
  const action = state.action; state.action = null; $('#action-dialog').close();
  if (action?.button) { action.button.disabled = true; action.button.textContent = '已取消'; }
  if (action) {
    await json(`/api/actions/${action.id}`, { method: 'DELETE' });
    debug.record('tool', `${action.tool} · cancelled`, { action_id: action.id }); debug.cancelled();
  }
}
async function approveAction() {
  if (state.busy || !state.action) return;
  const action = state.action; state.action = null; $('#action-dialog').close();
  if (action.button) { action.button.disabled = true; action.button.textContent = '已确认'; }
  debug.record('tool', `${action.tool} · approved`, { action_id: action.id, arguments: action.arguments });
  await loadSession(action.session_id); const target = message('assistant');
  await consumeStream(`/api/actions/${action.id}/approve`, {}, target);
}

async function renderMemories() {
  const { items } = await json('/api/memories');
  await refreshStatus();
  $('#memory-levels').replaceChildren();
  levels.forEach((level) => {
    const card = el('button', `level-card${state.tier === level.id ? ' selected' : ''}`);
    card.append(el('span', '', level.label), el('strong', '', level.title), el('em', '', String(state.status.memory[level.id])), el('small', '', level.note));
    card.addEventListener('click', guarded(async () => { state.tier = level.id; await renderMemories(); }));
    $('#memory-levels').append(card);
  });
  $('#memory-list-title').textContent = tierName(state.tier); $('#memory-list').replaceChildren();
  if (state.tier === 'temporary') {
    $('#memory-list').append(el('p', 'empty-state', '临时记忆用于当前对话最近 12 条消息，最多 18,000 字符。闲置 2 小时或服务重启后清空；历史聊天仍可查看。'));
    return;
  }
  const selected = items.filter((item) => item.tier === state.tier);
  if (!selected.length) $('#memory-list').append(el('p', 'empty-state', '这一层还没有记忆。开始聊天，或手动添加一条值得记住的事。'));
  selected.forEach((item) => {
    const expired = item.expires_at && Date.parse(item.expires_at) <= Date.now();
    const card = el('article', `memory-card${expired ? ' expired' : ''}`);
    const top = el('div', 'memory-card-top'); top.append(el('span', '', kindName(item.kind)),
      el('span', '', expired ? '已过期 · 不参与检索' : item.expires_at ? `有效至 ${new Date(item.expires_at).toLocaleDateString()}` : '不会自动过期'));
    const footer = el('div', 'memory-card-footer'); footer.append(el('small', '', `检索 ${item.uses} 次 · ${new Date(item.created_at).toLocaleDateString()}`));
    const tools = el('div');
    if (item.tier !== 'permanent') {
      const permanent = el('button', '', '设为永久'); permanent.addEventListener('click', guarded(async () => {
        if (!approveLocally('将这条记忆设为永久？它将一直参与检索，直到你删除或调整层级。')) return;
        await json(`/api/memories/${item.id}`, { method: 'PATCH', body: JSON.stringify({ tier: 'permanent' }) }); await renderMemories();
      })); tools.append(permanent);
    } else {
      const demote = el('button', '', '调整为二级'); demote.addEventListener('click', guarded(async () => {
        await json(`/api/memories/${item.id}`, { method: 'PATCH', body: JSON.stringify({ tier: 'second' }) }); await renderMemories();
      })); tools.append(demote);
    }
    const remove = el('button', 'danger', '删除'); remove.addEventListener('click', guarded(async () => {
      if (!approveLocally('删除这条记忆？')) return;
      await json(`/api/memories/${item.id}`, { method: 'DELETE' }); await renderMemories();
    })); tools.append(remove); footer.append(tools);
    card.append(top, el('p', '', item.content), footer); $('#memory-list').append(card);
  });
}
function renderPlugins() {
  $('#plugin-list').replaceChildren();
  const icons = { codex_task: ['⌘', 'lavender'], desktop_mouse: ['↖', 'mint'], desktop_click: ['↖', 'mint'], remember: ['▦', 'yellow'] };
  state.status.plugins.forEach((plugin) => {
    const card = el('article', 'plugin-card'); const [symbol, color] = icons[plugin.name] || ['◇', 'mint'];
    card.append(el('span', `suggestion-icon ${color}`, symbol), el('h2', '', plugin.title), el('p', '', plugin.description));
    card.insertBefore(el('div', 'plugin-id developer-only', plugin.name), card.querySelector('p'));
    const schema = el('details', 'schema-details developer-only');
    schema.append(el('summary', '', 'Parameters / JSON Schema'), el('pre', '', JSON.stringify(plugin.parameters, null, 2)));
    card.append(schema);
    if (!plugin.available) card.append(el('small', '', plugin.reason));
    const footer = el('footer'); footer.append(el('span', '', plugin.available ? `● 已连接 · ${automaticMode() ? '默认同意' : '需确认'}` : '○ 尚未连接'));
    const mousePlugin = ['desktop_click', 'desktop_mouse'].includes(plugin.name);
    const button = el('button', 'text-button', mousePlugin ? '回聊天控制鼠标 ↗' : plugin.name === 'codex_task' ? '开始任务 ↗' : plugin.name === 'remember' ? '管理记忆 ↗' : '提出请求 ↗');
    button.addEventListener('click', guarded(async () => {
      if (mousePlugin) { setMode('chat'); await openView('chat'); $('#mouse-control').focus(); return toast('在输入栏开启鼠标控制，再描述要点击、移动或滚动的位置。'); }
      if (plugin.name === 'remember') return openView('memory');
      if (plugin.name === 'codex_task') { setMode('codex'); await openView('chat'); return $('#prompt').focus(); }
      state.plugin = plugin; $('#plugin-title').textContent = plugin.title;
      const example = {};
      Object.entries(plugin.parameters.properties || {}).forEach(([key, property]) => {
        example[key] = property.default ?? (property.type === 'integer' || property.type === 'number' ? 0 : property.type === 'boolean' ? false : '');
      });
      $('#plugin-arguments').value = JSON.stringify(example, null, 2); $('#plugin-dialog').showModal();
    })); footer.append(button); card.append(footer); $('#plugin-list').append(card);
  });
}
function renderSettings() {
  const status = state.status; $('#settings-status').replaceChildren();
  const source = state.provider === 'auto' ? status.model.provider : state.provider;
  const sourceReady = source === 'codex' ? status.model.codex_chat_available : status.model.api_configured;
  [{ title: '日常对话', ready: sourceReady, detail: sourceReady ? (source === 'codex' ? `Codex · CLI 已找到，ChatGPT 登录与会员额度需实际调用验证；${automaticMode() ? '发送即调用。' : '逐次确认。'}` : `${status.model.api_name} · API 配置存在，连接需实际调用验证。`) : '尚未连接，请选择 Codex 登录问答，或配置模型 API。' },
    { title: 'Codex 编程（可选）', ready: status.codex.available, detail: status.codex.available ? `${status.codex.workspace} · ${status.codex.sandbox} · ${status.codex.reason}` : status.codex.reason },
    { title: '聊天鼠标控制', ready: status.mouse_control?.available === true, detail: status.mouse_control?.available ? `当前${state.mouseControl.enabled ? '开启' : '关闭'}；通过聊天输入栏开关授权，仅明确鼠标指令触发截图和单次操作。` : status.mouse_control?.reason || status.desktop.reason }].forEach((item) => {
    const row = el('div', 'connection-row'); const info = el('div'); info.append(el('strong', '', item.title), el('small', '', item.detail));
    row.append(el('span', `status-dot${item.ready ? '' : ' off'}`), info); $('#settings-status').append(row);
  });
}

function historyStamp(messages) { return `${messages.length}:${messages.at(-1)?.id || ''}`; }
async function renderTasks() {
  const tasks = await json('/api/actions'); $('#pending-tasks').replaceChildren();
  if (!tasks.length) $('#pending-tasks').append(el('p', 'empty-state', '暂时没有待确认任务。手机提交的 Codex 请求会出现在这里。'));
  tasks.forEach((task) => {
    const card = el('article', 'memory-card'); card.append(el('h2', '', task.title), el('p', '', actionSummary(task)));
    const button = el('button', 'primary-button', '查看并确认');
    button.addEventListener('click', guarded(() => showAction(task, button)));
    card.append(button); $('#pending-tasks').append(card);
  });
}
async function renderDevices() {
  const [devices, workspace] = await Promise.all([json('/api/devices'), json('/api/workspace')]);
  $('#shared-workspace-name').textContent = `项目：${workspace.name}`;
  $('#shared-workspace-id').textContent = `工作区编号：${workspace.id || '请先设置 CODEX_WORKSPACE'}`;
  $('#shared-workspace-policy').textContent = `${workspace.sandbox === 'workspace-write' ? '可修改指定项目' : '只读检查，不允许修改项目'} · ${workspace.approvals === 'automatic' ? '默认同意，指令直接执行' : '电脑逐次确认'} · ${workspace.available ? 'CLI 已找到，登录与额度需实际执行验证' : '目录或 Codex CLI 尚未就绪'}`;
  $('#invite-device').disabled = !devices.enabled;
  $('#mobile-setup-note').textContent = devices.enabled ? '手机同步已启用。需要先配置 HTTPS 反向代理，仅发布 /mobile/ 路径；配置步骤见 MOBILE.md。' : '默认未开启。请在 .env 设置 MOBILE_ENABLED=true 并重启，再按 MOBILE.md 配置 HTTPS 连接。不要将本地 /api 接口发布到网络。';
  $('#device-list').replaceChildren();
  if (!devices.items.length) $('#device-list').append(el('p', 'empty-state', '还没有配对的手机。'));
  devices.items.forEach((device) => {
    const card = el('article', 'memory-card');
    card.append(el('h2', '', device.name), el('p', '', device.revoked ? '已撤销访问' : `凭据有效至 ${new Date(device.expires_at).toLocaleDateString()}`));
    if (!device.revoked) {
      const button = el('button', 'text-button danger', '撤销这台设备');
      button.addEventListener('click', guarded(async () => {
        if (!approveLocally('撤销后，这台手机不能继续发起请求；已开始的任务不会自动撤回。')) return;
        await json(`/api/devices/${device.id}`, { method: 'DELETE' }); await renderDevices();
      })); card.append(button);
    }
    $('#device-list').append(card);
  });
}
async function syncViews() {
  if (!state.token || state.busy || state.syncing || document.hidden) return;
  state.syncing = true;
  const observedSession = state.session;
  try {
    await refreshStatus();
    if (usingCodex() && Date.now() - codexLastAttempt > 30000) void connectCodex();
    const sessions = await refreshSessions();
    if (state.session === observedSession && state.session && !sessions.some(item => item.id === state.session)) { await stopSpeech(true); state.session = null; resetChat(); }
    if (!$('#view-chat').hidden && state.session) {
      const session = state.session;
      const data = await json(`/api/sessions/${session}`);
      if (state.busy || state.session !== session) return;
      const stamp = historyStamp(data.messages);
      if (stamp !== state.historyStamp) {
        $('#messages').replaceChildren(); $('#welcome').hidden = data.messages.length > 0;
        data.messages.forEach(item => message(item.role, item.content)); state.historyStamp = stamp;
      }
    }
    if (!$('#view-tasks').hidden) await renderTasks();
    if (!$('#view-devices').hidden) await renderDevices();
    if (!$('#view-memory').hidden) await renderMemories();
    state.syncFailed = false;
  } catch { if (!state.syncFailed) toast('同步暂时中断，请检查本机服务。不会自动重发消息或执行任务。'); state.syncFailed = true; }
  finally { state.syncing = false; }
}
$('#refresh-tasks').addEventListener('click', guarded(renderTasks));
$('#refresh-devices').addEventListener('click', guarded(renderDevices));
$('#invite-device').addEventListener('click', guarded(async () => {
  const invitation = await post('/api/devices/invitations');
  $('#pair-code').value = invitation.code; $('#pair-invitation').hidden = false;
  $('#pair-code').focus(); $('#pair-code').select();
  setTimeout(() => { if ($('#pair-code').value === invitation.code) { $('#pair-code').value = ''; $('#pair-invitation').hidden = true; } }, invitation.expires_in * 1000);
}));
setInterval(syncViews, 5000);

function setDeveloperMode(enabled, persist = false) {
  state.developer = enabled;
  document.body.classList.toggle('developer-mode', enabled);
  $('#developer-mode').checked = enabled;
  debug.developer(enabled);
  if (!enabled && !$('#view-debug').hidden) guarded(() => openView('chat'))();
  if (persist) {
    try { localStorage.setItem('xiayuluo.developer-mode', String(enabled)); }
    catch { /* App preferences remain usable if local storage is unavailable. */ }
  }
}
try { setDeveloperMode(localStorage.getItem('xiayuluo.developer-mode') === 'true'); }
catch { setDeveloperMode(false); }
$('#developer-mode').addEventListener('change', (event) => setDeveloperMode(event.target.checked, true));
try {
  const selected = localStorage.getItem('xiayuluo.chat-provider');
  if (['auto', 'api', 'codex'].includes(selected)) state.provider = selected;
} catch { /* Defaults work with private storage disabled. */ }
$('#chat-provider').value = state.provider;
$('#approval-mode').addEventListener('change', guarded(async event => {
  const mode = event.target.value;
  if (state.busy) { $('#approval-mode').value = state.status.automation.mode; return toast('请先完成或停止当前任务。'); }
  try { await post('/api/automation', { mode }); }
  finally { await refreshStatus(); }
}));
$('#chat-provider').addEventListener('change', guarded(async event => {
  if (state.busy) { $('#chat-provider').value = state.provider; return toast('请先完成当前回复。'); }
  state.provider = event.target.value;
  try { localStorage.setItem('xiayuluo.chat-provider', state.provider); } catch { /* Window preference only. */ }
  await refreshStatus();
  if (usingCodex()) void connectCodex();
}));
$('#chat-provider').addEventListener('change', guarded(async () => {
  if (!state.busy && proactiveKnown && proactiveState.enabled) await setProactive(true, state.provider);
}));
$('#session-search').addEventListener('input', renderSessions);

$('#chat-form').addEventListener('submit', guarded(sendMessage));
$('#prompt').addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); $('#chat-form').requestSubmit(); }
});
$('#mode-toggle').addEventListener('click', () => setMode(state.mode === 'chat' ? 'codex' : 'chat'));
$('#stop').addEventListener('click', () => { state.controller?.abort(); void stopSpeech(true); });
$('#new-chat').addEventListener('click', guarded(newChat));
document.querySelectorAll('[data-view]').forEach((node) => node.addEventListener('click', guarded(() => openView(node.dataset.view))));
document.querySelectorAll('[data-open]').forEach((node) => node.addEventListener('click', guarded(() => openView(node.dataset.open))));
document.querySelectorAll('[data-suggestion]').forEach((node) => node.addEventListener('click', () => {
  setMode(node.dataset.mode || 'chat'); $('#prompt').value = node.dataset.suggestion; $('#prompt').focus();
}));
document.addEventListener('keydown', (event) => { if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); guarded(newChat)(); } });
$('#refresh-status').addEventListener('click', guarded(refreshStatus));
$('#refresh-status').addEventListener('click', () => void refreshProactive());
$('#refresh-status').addEventListener('click', () => void refreshTrainingProtection());
$('#refresh-status').addEventListener('click', () => void refreshCloudSync());
$('#refresh-status').addEventListener('click', () => void refreshLocalControls());
$('#add-memory').addEventListener('click', () => $('#memory-dialog').showModal());
document.querySelectorAll('[data-close]').forEach((node) => node.addEventListener('click', () => $(`#${node.dataset.close}`).close()));
$('#memory-form').addEventListener('submit', guarded(async (event) => {
  event.preventDefault(); const content = $('#memory-content').value.trim(); if (!content) return;
  await post('/api/memories', { tier: $('#memory-tier').value, kind: $('#memory-kind').value, content });
  state.tier = $('#memory-tier').value; $('#memory-content').value = ''; $('#memory-dialog').close(); await renderMemories(); toast('记忆已保存。');
}));
$('#clear-memory').addEventListener('click', guarded(async () => {
  if (state.busy) return toast('请先停止正在执行的任务。');
  if (!approveLocally('清空所有层级的记忆及全部历史对话？此操作无法撤销。')) return;
  await stopSpeech(true);
  await json('/api/memories', { method: 'DELETE' }); state.session = null; resetChat();
  await refreshSessions(); await renderMemories(); toast('所有记忆及历史对话已清空。');
}));
$('#mouse-control').addEventListener('click', guarded(toggleMouseControl));
$('#decline-action').addEventListener('click', guarded(declineAction));
$('#close-action').addEventListener('click', guarded(declineAction));
$('#action-dialog').addEventListener('cancel', (event) => { event.preventDefault(); guarded(declineAction)(); });
$('#approve-action').addEventListener('click', guarded(approveAction));
$('#plugin-form').addEventListener('submit', guarded(async (event) => {
  event.preventDefault(); if (!state.plugin) return;
  let args; try { args = JSON.parse($('#plugin-arguments').value); } catch { throw new Error('请输入有效的 JSON 参数。'); }
  const action = await post('/api/actions', { session_id: await ensureSession(), tool: state.plugin.name, arguments: args });
  $('#plugin-dialog').close(); await showAction(action);
}));
guarded(async () => {
  const bootstrap = await (await fetch('/api/bootstrap')).json(); state.token = bootstrap.token;
  void refreshProactive();
  void claimGreeting();
  void refreshVoice();
  setInterval(() => void refreshVoice(), 3000);
  void refreshSpeech();
  setInterval(() => void refreshSpeech(), 2000);
  await refreshStatus(); const sessions = await refreshSessions(); if (sessions.length) await loadSession(sessions[0].id, { navigate: false });
  if (usingCodex()) void connectCodex();
  void checkForUpdates(true);
  setInterval(() => void checkForUpdates(true), 3600000);
})();
