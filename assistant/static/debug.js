'use strict';

// Client-observed diagnostics only. No credentials, screenshots or hidden model reasoning.
window.DebugConsole = (() => {
  const $ = (selector) => document.querySelector(selector);
  const node = (tag, className, text) => {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  };
  const logs = [];
  let sequence = 0, payloads = false, run = null, timer = null, logRender = null;
  const secretKeys = /(?:^|_)(?:api_key|apikey|authorization|password|secret|token)$|secret|^x_assistant_token$/i;
  const textKeys = new Set(['message', 'prompt', 'text', 'content']);
  const imageKeys = new Set(['image', 'image_url', 'screenshot', 'png', 'image_data']);

  function safeString(value) {
    return value.replace(/\bBearer\s+[^\s"',;]+/gi, 'Bearer [redacted]')
      .replace(/\bsk-[A-Za-z0-9_-]{8,}/g, '[redacted]')
      .replace(/\b(?:OPENAI_API_KEY|ASSISTANT_MODEL_KEY|GH_TOKEN|X-Assistant-Token|Authorization)\s*[:=]\s*(?:"[^"]*"|'[^']*'|[^\s,;]+)/gi, '[redacted credential]')
      .replace(/data:image\/[^\s"']+/gi, '[image omitted]');
  }
  function redact(value, includeText = payloads, key = '') {
    const normalized = key.toLowerCase().replaceAll('-', '_');
    if (secretKeys.test(normalized)) return '[redacted]';
    if (imageKeys.has(normalized)) return '[image omitted]';
    if (!includeText && normalized === 'arguments') return '[tool arguments omitted]';
    if (typeof value === 'string') {
      if (!includeText && textKeys.has(normalized)) {
        return value.startsWith('[text omitted') ? value : `[text omitted: ${value.length} chars]`;
      }
      return safeString(value.length > 4000 ? value.slice(0, 4000) + '… [truncated]' : value);
    }
    if (Array.isArray(value)) return value.slice(0, 30).map((item) => redact(item, includeText));
    if (value && typeof value === 'object') {
      return Object.fromEntries(Object.entries(value).map(([name, item]) => [name, redact(item, includeText, name)]));
    }
    return value;
  }
  function duration(ms) {
    if (ms === null || ms === undefined) return '—';
    return ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(2)} s`;
  }
  function stamp(iso) { return new Date(iso).toLocaleTimeString('zh-CN', { hour12: false }); }

  function renderLogs() {
    const openEntries = new Set([...$('#event-list').querySelectorAll('details[open]')].map((entry) => entry.dataset.eventId));
    const filter = $('#event-filter').value;
    const entries = logs.filter((entry) => filter === 'all' || entry.category === filter).slice().reverse();
    if (!$('#view-debug').hidden) $('#event-list').replaceChildren();
    if (!$('#view-debug').hidden && !entries.length) $('#event-list').append(node('p', 'empty-state', '暂无匹配事件。发送请求后，可在这里检查实际事件与结果。'));
    for (const entry of $('#view-debug').hidden ? [] : entries) {
      const detail = node('details', `event-entry${entry.category === 'error' ? ' error' : ''}`);
      detail.dataset.eventId = String(entry.id);
      detail.open = openEntries.has(String(entry.id));
      const summary = node('summary');
      const time = node('time', '', stamp(entry.time)); time.dateTime = entry.time;
      summary.append(time, node('span', 'event-category', entry.category), node('span', 'event-summary', entry.summary),
        node('span', 'event-duration', entry.duration_ms === undefined ? '' : duration(entry.duration_ms)));
      detail.append(summary, node('pre', '', JSON.stringify(entry.data, null, 2)));
      $('#event-list').append(detail);
    }
    $('#recent-events').replaceChildren();
    if (!logs.length) $('#recent-events').append(node('p', 'section-note', '尚未记录事件。'));
    for (const entry of logs.slice(-8).reverse()) {
      const row = node('div', `recent-event${entry.category === 'error' ? ' error' : ''}`);
      row.append(node('small', '', `${stamp(entry.time)} / ${entry.category.toUpperCase()}`), node('span', '', entry.summary));
      $('#recent-events').append(row);
    }
    $('#nav-event-count').textContent = String(logs.length);
    $('#inspector-event-count').textContent = String(logs.length);
    $('#footer-events').textContent = `${logs.length} events`;
    $('#export-events').disabled = !logs.length;
  }
  function record(category, summary, data = {}, milliseconds) {
    logs.push({ id: ++sequence, time: new Date().toISOString(), category, summary: safeString(summary),
      data: redact(data), ...(milliseconds === undefined ? {} : { duration_ms: Math.round(milliseconds) }) });
    if (logs.length > 300) logs.shift();
    if (!logRender) logRender = setTimeout(() => { logRender = null; renderLogs(); }, 75);
  }
  function request(path, options) {
    const method = options.method || 'GET';
    let body;
    if (options.body) { try { body = JSON.parse(options.body); } catch { body = '[non-JSON body omitted]'; } }
    record('http', `${method} ${path} · request`, { method, path, ...(body ? { body } : {}) });
    return { method, path, started: performance.now() };
  }
  function response(ticket, status) {
    record(status >= 400 ? 'error' : 'http', `${ticket.method} ${ticket.path} → ${status}`,
      { method: ticket.method, path: ticket.path, status }, performance.now() - ticket.started);
  }

  function renderMetrics() {
    if (!run) return;
    const labels = { running: 'RUNNING', completed: 'DONE', waiting: 'WAITING', failed: 'ERROR', stopped: 'STOPPED', cancelled: 'CANCELLED' };
    $('#metric-state').textContent = labels[run.state] || 'IDLE';
    $('#metric-state').dataset.state = run.state;
    $('#metric-first').textContent = duration(run.first);
    $('#metric-duration').textContent = duration(run.elapsed ?? performance.now() - run.started);
    $('#metric-events').textContent = String(run.events);
    $('#metric-output').textContent = String(run.chars);
  }
  function begin(path) {
    clearInterval(timer);
    run = { state: 'running', started: performance.now(), first: null, elapsed: null, events: 0, chars: 0, action: false };
    $('#route-label').textContent = `POST ${path}`;
    renderMetrics(); timer = setInterval(renderMetrics, 100);
  }
  function stream(event) {
    if (run) {
      run.events++;
      if (event.type === 'text') {
        if (run.first === null && event.text) run.first = performance.now() - run.started;
        run.chars += [...(event.text || '')].length;
      }
      if (event.type === 'action') run.action = true;
      renderMetrics();
    }
    const summaries = { text: `text · +${[...(event.text || '')].length} chars`,
      memory: `memory · ${event.items?.length || 0} retrieved`, action: `action · ${event.action?.title || 'tool'} pending`,
      status: `status · ${safeString(event.message || '')}`, done: 'done · stream completed', error: `error · ${safeString(event.message || '')}` };
    record(event.type === 'error' ? 'error' : 'sse', summaries[event.type] || event.type, event);
    if (event.type === 'memory') retrieval(event.items || []);
  }
  function end(outcome) {
    clearInterval(timer); timer = null;
    if (run) {
      run.state = outcome === 'completed' && run.action ? 'waiting' : outcome;
      run.elapsed = performance.now() - run.started;
      renderMetrics();
    }
  }
  function cancelled() {
    if (run?.state === 'waiting') { run.state = 'cancelled'; renderMetrics(); }
  }
  function reset() {
    clearInterval(timer); timer = null; run = null;
    $('#metric-state').textContent = 'IDLE'; $('#metric-state').dataset.state = 'idle';
    $('#metric-first').textContent = '—'; $('#metric-duration').textContent = '—';
    $('#metric-events').textContent = '0'; $('#metric-output').textContent = '0';
    $('#route-label').textContent = 'POST /api/chat';
    $('#retrieval-details').replaceChildren(node('p', 'section-note', '尚未收到当前会话的记忆检索事件。'));
  }
  function retrieval(items) {
    $('#retrieval-details').replaceChildren();
    if (!items.length) $('#retrieval-details').append(node('p', 'section-note', '本轮没有召回长期记忆。'));
    for (const item of items) {
      const row = node('div', 'retrieval-item');
      const names = { first: '一级记忆', second: '二级记忆', permanent: '永久记忆' };
      row.append(node('span', '', `${names[item.tier] || item.tier} / ${item.id.slice(0, 8)}`), node('p', '', item.content));
      $('#retrieval-details').append(row);
    }
  }
  function runtime(status, session, mode) {
    const instance = status.runtime || {};
    $('#platform-label').textContent = `${instance.platform || 'LOCAL'} / PY ${instance.python || '—'}`;
    $('#footer-runtime').textContent = `${instance.presentation === 'desktop' ? 'APP' : 'SERVICE'} · ${instance.platform || 'local'} · Python ${instance.python || '—'}`;
    $('#current-session').textContent = `session: ${session ? session.slice(0, 12) : '—'}`;
    $('#footer-session').textContent = session ? `session ${session.slice(0, 8)}` : '无活动会话';
    $('#runtime-details').replaceChildren();
    for (const [name, value] of [['模式', mode === 'codex' ? 'codex / CLI' : 'chat / SSE'], ['模型', status.model.name],
      ['API 地址', status.model.endpoint || '—'], ['平台', instance.platform || '—'], ['Python', instance.python || '—'],
      ['运行方式', instance.presentation === 'desktop' ? 'Desktop App' : 'Dev service'],
      ['运行时间', `${instance.uptime_seconds ?? 0} s`], ['配置文件', instance.config_file || '项目目录 / .env'],
      ['沙箱', status.codex.sandbox], ['项目目录', status.codex.workspace || '未配置']]) {
      $('#runtime-details').append(node('dt', '', name), node('dd', '', value));
    }
    $('#runtime-json').textContent = JSON.stringify(redact(status), null, 2);
  }

  function inspectorTab(name, focus = false) {
    document.querySelectorAll('[data-inspector]').forEach((tab) => {
      const active = tab.dataset.inspector === name;
      tab.classList.toggle('active', active); tab.setAttribute('aria-selected', String(active)); tab.tabIndex = active ? 0 : -1;
      if (active && focus) tab.focus();
    });
    document.querySelectorAll('.inspector-content').forEach((panel) => { panel.hidden = panel.id !== `inspect-${name}`; });
  }
  const small = window.matchMedia('(max-width: 1200px)');
  function showInspector(open) {
    open = open && document.body.classList.contains('developer-mode');
    document.body.classList.toggle('inspector-open', small.matches && open);
    document.body.classList.toggle('inspector-closed', !small.matches && !open);
    $('#toggle-inspector').setAttribute('aria-expanded', String(open));
  }
  $('#toggle-inspector').addEventListener('click', () => showInspector($('#toggle-inspector').getAttribute('aria-expanded') !== 'true'));
  $('#close-inspector').addEventListener('click', () => { showInspector(false); $('#toggle-inspector').focus(); });
  small.addEventListener('change', () => showInspector(!small.matches));
  document.querySelectorAll('[data-inspector]').forEach((tab) => {
    tab.addEventListener('click', () => inspectorTab(tab.dataset.inspector));
    tab.addEventListener('keydown', (event) => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault(); const names = ['runtime', 'context', 'events'];
      const index = names.indexOf(tab.dataset.inspector);
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? 2 : (index + (event.key === 'ArrowRight' ? 1 : 2)) % 3;
      inspectorTab(names[next], true);
    });
  });
  function developer(enabled) {
    if (!enabled && payloads) {
      $('#record-payload').checked = false; payloads = false;
      logs.forEach((entry) => { entry.data = redact(entry.data, false); }); renderLogs();
    }
    showInspector(enabled && !small.matches);
  }
  $('#event-filter').addEventListener('change', renderLogs);
  $('#clear-events').addEventListener('click', () => { logs.length = 0; renderLogs(); });
  $('#record-payload').addEventListener('change', (event) => {
    payloads = event.target.checked;
    if (!payloads) { logs.forEach((entry) => { entry.data = redact(entry.data, false); }); renderLogs(); }
  });
  $('#export-events').addEventListener('click', () => {
    const content = JSON.stringify({ application: '夏鱼洛', source: 'client-observed', exported_at: new Date().toISOString(), events: logs }, null, 2);
    const url = URL.createObjectURL(new Blob([content], { type: 'application/json' }));
    const link = document.createElement('a'); link.href = url; link.download = 'xiayuluo-debug-trace.json';
    document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
  $('#metric-first').title = '从发起流式请求到浏览器收到第一段非空文本，包含传输与本地处理耗时。';
  $('#metric-events').title = '实际接收的 SSE 事件数，不是模型 token 数。';
  showInspector(!small.matches); inspectorTab('runtime'); renderLogs();
  return { record, request, response, begin, stream, end, cancelled, reset, runtime, redact, developer, refresh: renderLogs };
})();
