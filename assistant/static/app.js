'use strict';

const $ = (selector) => document.querySelector(selector);
const debug = window.DebugConsole;
const state = { token: '', status: null, session: null, mode: 'chat', busy: false, controller: null,
  screenshot: null, attachment: null, position: null, tier: 'first', action: null, plugin: null,
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

let updateBusy = false;
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
  if (!approveLocally('开启本地麦克风持续监听“夏鱼洛”，并授权本次语音会话的 Codex 只读问答？\n最小化后仍运行，可能增加耗电；唤醒后的文字、近期上下文和相关记忆会发送到官方 Codex CLI，进入会话/记忆。不上传或保存环境录音，不使用付费 API。语音通道仅普通问答；编程、鼠标和记忆操作请用其他入口，遵循本机执行策略。停止监听即撤销，退出 App 即停止。')) return;
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
async function loadUpdateDownloads() {
  try {
    const info = await json('/api/updates/downloads');
    const url = new URL(info.windows_url);
    if (url.origin !== 'https://github.com' || url.username || url.password || url.search || url.hash) throw new Error('下载地址配置无效。');
    $('#download-update').href = url.href;
  } catch { /* Keep the pinned GitHub fallback available without a successful update check. */ }
}
$('#download-update').addEventListener('click', () => {
  $('#update-status').textContent = '已请求在系统浏览器打开 GitHub 下载页；私有仓库需登录。请选择成功构建下载，打开页面不代表更新已安装。';
});
$('#check-update').addEventListener('click', async () => {
  if (updateBusy) return;
  updateBusy = true; $('#check-update').disabled = true; $('#install-update').hidden = true;
  $('#update-status').textContent = '正在检查签名更新清单…'; $('#update-notes').hidden = true;
  try {
    const result = await json('/api/updates');
    $('#update-status').textContent = result.available
      ? `发现 ${result.version}，当前 ${result.current}。${result.install_supported ? '可在此下载更新。' : '当前为源码版，请先安装 Windows 发行版。'}`
      : `当前 ${result.current}，暂无更高版本。`;
    $('#update-notes').textContent = result.notes; $('#update-notes').hidden = !result.notes;
    $('#install-update').hidden = !result.available || !result.install_supported;
  } catch (error) { $('#update-status').textContent = error.message; }
  finally { updateBusy = false; $('#check-update').disabled = false; }
});
$('#install-update').addEventListener('click', async () => {
  if (updateBusy || state.busy) return toast('请先完成或停止当前任务。');
  if (!approveLocally('下载并校验新版本，然后退出并重启夏鱼洛？配置、聊天和四级记忆保留，安装位置不变。')) return;
  updateBusy = true; $('#install-update').disabled = true; $('#check-update').disabled = true;
  $('#update-status').textContent = '正在下载并校验，请勿关闭 App；完成后自动重启…';
  try { await post('/api/updates/install', { confirm: true }); $('#update-status').textContent = '校验通过，正在重启安装…'; }
  catch (error) { $('#update-status').textContent = error.message; }
  finally { updateBusy = false; $('#install-update').disabled = false; $('#check-update').disabled = false; }
});

function setMode(mode) {
  state.mode = mode;
  $('#mode-toggle').replaceChildren(document.createTextNode(mode === 'codex' ? '⌘ Codex 编程 ' : '◌ 日常对话 '), el('span', '', '⌄'));
  $('#prompt').placeholder = mode === 'codex' ? (automaticMode() ? '描述编程任务，发送后交给本机 Codex…' : '描述编程任务，确认后交给本机 Codex…') : '和夏鱼洛聊聊，或交给我一件事…';
  if (state.status) debug.runtime(state.status, state.session, mode);
}
async function openView(view) {
  if (!['chat', 'memory', 'desktop', 'plugins', 'settings', 'debug', 'tasks', 'devices'].includes(view)) return;
  if (view === 'debug' && !state.developer) return;
  document.querySelectorAll('.view').forEach((node) => { node.hidden = node.id !== `view-${view}`; });
  document.querySelectorAll('.nav-item').forEach((node) => node.classList.toggle('active', node.dataset.view === view));
  $('#view-title').textContent = { chat: '聊天', memory: '记忆', desktop: '屏幕助手', plugins: '工具箱', settings: '设置', debug: '事件日志', tasks: '待确认任务', devices: '设备与同步' }[view];
  if (view === 'memory') await renderMemories();
  if (view === 'debug') debug.refresh();
  if (view === 'tasks') await renderTasks();
  if (view === 'devices') await renderDevices();
}
function setBusy(busy) {
  state.busy = busy;
  $('#send').hidden = busy;
  $('#stop').hidden = !busy;
  $('#prompt').disabled = busy;
  $('#new-chat').disabled = busy;
  $('#mode-toggle').disabled = busy;
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
async function refreshStatus() {
  state.status = await json(`/api/status${state.session ? `?session_id=${encodeURIComponent(state.session)}` : ''}`);
  const status = state.status;
  const source = state.provider === 'auto' ? status.model.provider : state.provider;
  const ready = source === 'codex' ? status.model.codex_chat_available : status.model.api_configured;
  $('#model-status').textContent = ready ? (source === 'codex' ? `Codex · 已找到 CLI，${automaticMode() ? '发送后直接调用' : '发送后确认'}` : `${status.model.api_name} · API 已配置`) : (source === 'codex' ? '请安装并登录官方 Codex CLI' : 'API 待配置');
  $('#approval-mode').value = status.automation.mode;
  $('#automation-status').textContent = status.automation.warning || (automaticMode() ? '已授权默认同意：发送后直接执行，不逐次弹窗；切换设置不会重放旧请求。' : '当前逐次确认：工具请求等待你在电脑批准。');
  $('#execution-badge').textContent = automaticMode() ? '默认同意 · 自动执行' : '执行前需确认';
  $('#view-desktop .mini-tag').textContent = automaticMode() ? 'AUTO EXECUTION' : 'APPROVAL REQUIRED';
  $('#task-policy').textContent = automaticMode() ? '自动模式：新指令直接执行，结果在原会话显示。旧待确认请求不会自动重放，可在此单独处理或取消。' : '手机和当前电脑的请求在这里等待确认，2 分钟过期，不自动执行。';
  $('#model-dot').classList.toggle('off', !ready);
  $('#connection-hint').hidden = ready;
  $('#desktop-notice').textContent = status.desktop.available ? '本机桌面驱动可用。截图有效期为 2 分钟；点击确认前会校验目标区域。请勿在运行点击时移动窗口。' : status.desktop.reason;
  $('#desktop-notice').classList.toggle('ready', status.desktop.available);
  $('#capture-screen').disabled = !status.desktop.available;
  $('#memory-summary').replaceChildren();
  levels.forEach((level, index) => {
    const row = el('div', 'memory-stat');
    const info = el('div', 'memory-stat-info', level.title); info.append(el('small', '', level.detail));
    row.append(el('span', 'level-icon', index === 3 ? '∞' : `0${index}`), info, el('span', 'memory-stat-count', String(status.memory[level.id])));
    $('#memory-summary').append(row);
  });
  $('#capabilities').replaceChildren();
  const capabilities = [{ title: '流式对话', available: status.model.configured },
    { title: 'Codex', available: status.codex.available }, { title: '屏幕点击', available: status.desktop.available }];
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
  $('#retrieval-note').hidden = true; state.attachment = null; $('#screen-attachment').hidden = true;
}
async function loadSession(id) {
  if (state.busy) return toast('请先等待任务完成或停止。');
  const data = await json(`/api/sessions/${id}`);
  if (state.session !== id) debug.reset();
  state.session = id; resetChat();
  data.messages.forEach((item) => message(item.role, item.content));
  state.historyStamp = historyStamp(data.messages);
  await openView('chat'); await refreshSessions(); await refreshStatus(); scrollChat();
}
async function ensureSession() {
  if (!state.session) state.session = (await post('/api/sessions')).id;
  return state.session;
}
async function newChat() {
  if (state.busy) return;
  state.session = (await post('/api/sessions')).id;
  debug.reset();
  resetChat(); setMode('chat'); await openView('chat'); await refreshSessions(); await refreshStatus(); $('#prompt').focus();
}

async function consumeStream(path, body, target) {
  state.controller = new AbortController(); setBusy(true);
  debug.begin(path);
  target.body.replaceChildren(el('span', 'thinking', '夏鱼洛正在处理'));
  let text = '', finished = false, reader;
  let outcome = 'failed';
  try {
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
        if (event.type === 'action') addActionCard(target.holder, event.action);
        if (event.type === 'done') finished = true;
      }
      if (done) break;
    }
    if (!finished) throw new Error('连接提前结束，本轮回复未完成。');
    if (!text) target.body.textContent = '任务已完成。';
    outcome = 'completed';
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
  event.preventDefault(); if (state.busy) return;
  const prompt = $('#prompt').value.trim(); if (!prompt) return;
  const session = await ensureSession();
  const attachment = state.attachment;
  $('#prompt').value = ''; state.attachment = null; $('#screen-attachment').hidden = true;
  message('user', prompt + (attachment ? '\n[本轮附上主屏幕截图]' : ''));
  const target = message('assistant');
  await consumeStream('/api/chat', { session_id: session, message: prompt, mode: state.mode, provider: state.provider === 'auto' ? undefined : state.provider, screenshot_id: attachment }, target);
}

function actionSummary(action) {
  if (action.tool === 'codex_task') return `项目目录（当前配置）：${state.status.codex.workspace}\n提交工作区：${action.workspace?.name || '未设置'}\n工作区编号：${action.workspace?.id || '未设置'}\n提交沙箱：${action.workspace?.sandbox || state.status.codex.sandbox}\n目录或权限改变后必须重新提交。\n\n任务：${action.arguments.prompt}`;
  if (action.tool === 'desktop_click') return `主屏幕坐标：(${action.arguments.x}, ${action.arguments.y})\n按键：${action.arguments.button === 'right' ? '右键' : '左键'}\n截图 ID：${action.arguments.screenshot_id}`;
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
    : action.tool === 'desktop_click' ? '确认后将在实际桌面执行一次点击。请先核实目标位置和当前屏幕。' : '确认后才会执行插件操作。';
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
async function captureScreen() {
  if (state.busy) return toast('请先结束当前任务。');
  $('#capture-screen').disabled = true;
  try {
    const screenshot = await post('/api/desktop/screenshot'); state.screenshot = screenshot; state.position = null;
    $('#screen-image').src = screenshot.image; $('#screen-container').hidden = false; $('#screen-empty').hidden = true;
    $('#screen-dimensions').textContent = `主屏幕 · ${screenshot.width} × ${screenshot.height}`;
    $('#share-screen').disabled = false; $('#request-click').disabled = true; $('#click-marker').hidden = true;
    $('#selected-position').textContent = '点击截图选择目标位置';
  } finally { $('#capture-screen').disabled = !state.status.desktop.available; }
}
function selectPosition(event) {
  if (!state.screenshot) return;
  const rect = $('#screen-image').getBoundingClientRect(), shot = state.screenshot;
  const x = Math.min(shot.width - 1, Math.max(0, Math.floor((event.clientX - rect.left) / rect.width * shot.width)));
  const y = Math.min(shot.height - 1, Math.max(0, Math.floor((event.clientY - rect.top) / rect.height * shot.height)));
  state.position = { x, y }; $('#selected-position').textContent = `选择坐标 (${x}, ${y}) · 左键单击`;
  $('#click-marker').style.left = `${x / shot.width * 100}%`; $('#click-marker').style.top = `${y / shot.height * 100}%`;
  $('#click-marker').hidden = false; $('#request-click').disabled = false;
}
async function requestClick() {
  if (state.busy || !state.position || !state.screenshot) return;
  const action = await post('/api/actions', { session_id: await ensureSession(), tool: 'desktop_click',
    arguments: { ...state.position, button: 'left', screenshot_id: state.screenshot.id } });
  await showAction(action);
}
async function shareScreen() {
  if (!state.screenshot) return;
  state.attachment = state.screenshot.id; $('#screen-attachment').hidden = false;
  setMode('chat'); await openView('chat'); $('#prompt').focus();
  toast('本轮消息将附上截图，并发送给配置的模型服务。');
}
function renderPlugins() {
  $('#plugin-list').replaceChildren();
  const icons = { codex_task: ['⌘', 'lavender'], desktop_click: ['▱', 'mint'], remember: ['▦', 'yellow'] };
  state.status.plugins.forEach((plugin) => {
    const card = el('article', 'plugin-card'); const [symbol, color] = icons[plugin.name] || ['◇', 'mint'];
    card.append(el('span', `suggestion-icon ${color}`, symbol), el('h2', '', plugin.title), el('p', '', plugin.description));
    card.insertBefore(el('div', 'plugin-id developer-only', plugin.name), card.querySelector('p'));
    const schema = el('details', 'schema-details developer-only');
    schema.append(el('summary', '', 'Parameters / JSON Schema'), el('pre', '', JSON.stringify(plugin.parameters, null, 2)));
    card.append(schema);
    if (!plugin.available) card.append(el('small', '', plugin.reason));
    const footer = el('footer'); footer.append(el('span', '', plugin.available ? `● 已连接 · ${automaticMode() ? '默认同意' : '需确认'}` : '○ 尚未连接'));
    const button = el('button', 'text-button', plugin.name === 'desktop_click' ? '打开桌面 ↗' : plugin.name === 'codex_task' ? '开始任务 ↗' : plugin.name === 'remember' ? '管理记忆 ↗' : '提出请求 ↗');
    button.addEventListener('click', guarded(async () => {
      if (plugin.name === 'desktop_click') return openView('desktop');
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
    { title: '桌面驱动', ready: status.desktop.available, detail: status.desktop.available ? `主屏幕截图与鼠标点击可用，${automaticMode() ? '按授权自动执行' : '需用户确认'}` : status.desktop.reason }].forEach((item) => {
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
    const sessions = await refreshSessions();
    if (state.session === observedSession && state.session && !sessions.some(item => item.id === state.session)) { state.session = null; resetChat(); }
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
}));
$('#session-search').addEventListener('input', renderSessions);

$('#chat-form').addEventListener('submit', guarded(sendMessage));
$('#prompt').addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); $('#chat-form').requestSubmit(); }
});
$('#mode-toggle').addEventListener('click', () => setMode(state.mode === 'chat' ? 'codex' : 'chat'));
$('#stop').addEventListener('click', () => state.controller?.abort());
$('#new-chat').addEventListener('click', guarded(newChat));
document.querySelectorAll('[data-view]').forEach((node) => node.addEventListener('click', guarded(() => openView(node.dataset.view))));
document.querySelectorAll('[data-open]').forEach((node) => node.addEventListener('click', guarded(() => openView(node.dataset.open))));
document.querySelectorAll('[data-suggestion]').forEach((node) => node.addEventListener('click', () => {
  setMode(node.dataset.mode || 'chat'); $('#prompt').value = node.dataset.suggestion; $('#prompt').focus();
}));
document.addEventListener('keydown', (event) => { if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); guarded(newChat)(); } });
$('#refresh-status').addEventListener('click', guarded(refreshStatus));
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
  await json('/api/memories', { method: 'DELETE' }); state.session = null; resetChat();
  await refreshSessions(); await renderMemories(); toast('所有记忆及历史对话已清空。');
}));
$('#capture-screen').addEventListener('click', guarded(captureScreen));
$('#screen-image').addEventListener('click', selectPosition);
$('#request-click').addEventListener('click', guarded(requestClick));
$('#share-screen').addEventListener('click', guarded(shareScreen));
$('#attach-screen').addEventListener('click', guarded(() => openView('desktop')));
$('#remove-attachment').addEventListener('click', () => { state.attachment = null; $('#screen-attachment').hidden = true; });
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
  void loadUpdateDownloads();
  void refreshVoice();
  setInterval(() => void refreshVoice(), 3000);
  await refreshStatus(); const sessions = await refreshSessions(); if (sessions.length) await loadSession(sessions[0].id);
})();
