/** Optional cloud transport/replica. No deployment, credentials or silent task execution. */
export type CloudEntity = 'session' | 'message' | 'memory';
export type CloudEvent = { event_id: string; entity: CloudEntity; entity_id: string;
  operation: 'upsert' | 'delete'; base_revision: number; payload: Record<string, unknown> };
export type RemoteCloudEvent = CloudEvent & { cursor: number; revision: number };
export type CloudPage = { events: RemoteCloudEvent[]; cursor: number; more: boolean };
export type CloudTask = { id: string; workspace_id: string; session_id: string; prompt: string };
export type CloudState = { schema: 1; cursor: number; records: RemoteCloudEvent[];
  outbox: (CloudEvent & { conflict?: boolean })[];
  tasks: (CloudTask & { local_state: 'pending' | 'submitted' | 'conflict'; result?: unknown })[] };
/** Implement load/save with private, atomic durable storage. Never store the device token here. */
export interface CloudStateStore { load(): Promise<string | null>; save(value: string): Promise<void> }
export interface CloudTransport {
  pushEvents(events: CloudEvent[]): Promise<unknown>;
  pullEvents(after: number): Promise<CloudPage>;
  submitTask(task: CloudTask): Promise<unknown>;
}
export class CloudSyncError extends Error {}
export class CloudConflict extends CloudSyncError {}

const fields: Record<CloudEntity, string[]> = {
  session: ['title', 'created_at'],
  message: ['session_id', 'role', 'content', 'created_at'],
  memory: ['tier', 'kind', 'content', 'created_at', 'accessed_at', 'expires_at', 'uses', 'source_session'],
};
function identifier(value: unknown): asserts value is string {
  if (typeof value !== 'string' || !/^[A-Za-z0-9_-]{1,128}$/.test(value)) throw new CloudSyncError('无效的同步记录编号。');
}
function validEvent(event: CloudEvent, remote = false): void {
  if (!event || typeof event !== 'object' || !(event.entity in fields) ||
      !['upsert', 'delete'].includes(event.operation) || !Number.isSafeInteger(event.base_revision) || event.base_revision < 0) {
    throw new CloudSyncError('无效的云同步事件。');
  }
  identifier(event.event_id); identifier(event.entity_id);
  const payload = event.payload;
  if (!payload || typeof payload !== 'object' || Array.isArray(payload) ||
      Object.keys(payload).some(key => !fields[event.entity].includes(key)) || new TextEncoder().encode(JSON.stringify(payload)).byteLength > 64000) {
    throw new CloudSyncError('同步只接受文字记录，不接受截图、训练数据或凭据字段。');
  }
  if (event.operation === 'delete' && Object.keys(payload).length) throw new CloudSyncError('删除标记不能包含数据。');
  if (event.operation === 'upsert') {
    if (Object.keys(payload).length !== fields[event.entity].length) throw new CloudSyncError('同步记录字段不完整。');
    if (event.entity === 'memory' && !['first', 'second', 'permanent'].includes(String(payload.tier))) {
      throw new CloudSyncError('临时上下文不上传云端。');
    }
    if (event.entity === 'message' && !['user', 'assistant'].includes(String(payload.role))) {
      throw new CloudSyncError('消息角色无效。');
    }
    if (event.entity === 'session' && (typeof payload.title !== 'string' || !payload.title.length || payload.title.length > 200)) throw new CloudSyncError('会话标题无效。');
    if (event.entity !== 'session' && (typeof payload.content !== 'string' || !payload.content.length ||
        payload.content.length > (event.entity === 'memory' ? 2000 : 64000))) throw new CloudSyncError('同步文字内容无效。');
    for (const key of ['created_at', 'accessed_at', 'expires_at']) {
      if (key in payload && !(key === 'expires_at' && payload[key] === null) &&
          (typeof payload[key] !== 'string' || !payload[key].length || payload[key].length > 64)) throw new CloudSyncError('同步时间字段无效。');
    }
    if (event.entity === 'message') identifier(payload.session_id);
    if (event.entity === 'memory') {
      if (!['fact', 'procedure', 'episode'].includes(String(payload.kind))) throw new CloudSyncError('记忆类型无效。');
      if (payload.source_session !== null) identifier(payload.source_session);
    }
    for (const [key, value] of Object.entries(payload)) {
      if (key === 'uses' ? !Number.isSafeInteger(value) || Number(value) < 0 : value !== null && typeof value !== 'string') {
        throw new CloudSyncError('同步文字字段无效。');
      }
    }
  }
  if (remote) {
    const item = event as RemoteCloudEvent;
    if (!Number.isSafeInteger(item.cursor) || item.cursor < 1 || !Number.isSafeInteger(item.revision) || item.revision < 1) {
      throw new CloudSyncError('同步游标或版本无效。');
    }
  }
}
function emptyState(): CloudState { return { schema: 1, cursor: 0, records: [], outbox: [], tasks: [] }; }
function payloadText(payload: Record<string, unknown>): string {
  return JSON.stringify(Object.keys(payload).sort().map(key => [key, payload[key]]));
}
function validReceipt(receipt: unknown, submitted: CloudEvent[]): void {
  const result = receipt as { events?: RemoteCloudEvent[]; cursor?: number };
  if (!result || !Array.isArray(result.events) || result.events.length !== submitted.length ||
      !Number.isSafeInteger(result.cursor) || Number(result.cursor) < 0) {
    throw new CloudSyncError('云端未确认完整批次，原待发送内容保留。');
  }
  const expected = new Map(submitted.map(item => [item.event_id, item])), seen = new Set<string>();
  for (const event of result.events) {
    validEvent(event, true);
    const original = expected.get(event.event_id);
    if (!original || seen.has(event.event_id) || event.revision !== event.base_revision + 1 || event.cursor > Number(result.cursor) ||
        event.entity !== original.entity || event.entity_id !== original.entity_id || event.operation !== original.operation ||
        event.base_revision !== original.base_revision || payloadText(event.payload) !== payloadText(original.payload)) {
      throw new CloudSyncError('云端批次确认内容不符，原待发送内容保留。');
    }
    seen.add(event.event_id);
  }
}
function clone<T>(value: T): T { return JSON.parse(JSON.stringify(value)) as T; }
function checkState(value: unknown): asserts value is CloudState {
  const state = value as CloudState;
  if (!state || state.schema !== 1 || !Number.isSafeInteger(state.cursor) || state.cursor < 0 ||
      !Array.isArray(state.records) || !Array.isArray(state.outbox) || !Array.isArray(state.tasks) ||
      state.records.length > 100000 || state.outbox.length > 10000 || state.tasks.length > 10000) {
    throw new CloudSyncError('本地同步队列格式损坏，不会清空或替换原数据。');
  }
  state.records.forEach(item => validEvent(item, true)); state.outbox.forEach(item => validEvent(item));
  for (const task of state.tasks) {
    identifier(task.id); identifier(task.workspace_id); identifier(task.session_id);
    if (!['pending', 'submitted', 'conflict'].includes(task.local_state) || typeof task.prompt !== 'string' ||
        !task.prompt.trim() || task.prompt.length > 12000) throw new CloudSyncError('本地任务队列格式无效。');
  }
}

export function cloudOrigin(raw: string): string {
  const value = new URL(raw.trim());
  if (value.protocol !== 'https:' || value.username || value.password || value.search || value.hash ||
      !['', '/'].includes(value.pathname)) throw new CloudSyncError('云同步只接受不含凭据的 HTTPS 源站。');
  return value.origin;
}
export class CloudHTTPClient implements CloudTransport {
  readonly origin: string;
  private readonly token: string;
  private readonly fetcher: typeof fetch;
  constructor(origin: string, token: string, fetcher: typeof fetch) {
    this.origin = cloudOrigin(origin);
    if (!/^[\x21-\x7e]{20,512}$/.test(token)) throw new CloudSyncError('云同步设备凭据无效。');
    this.token = token; this.fetcher = fetcher;
  }
  async request(path: string, body?: unknown): Promise<Record<string, unknown>> {
    const abort = new AbortController(); const timer = setTimeout(() => abort.abort(), 20000);
    try {
      const content = body === undefined ? undefined : JSON.stringify(body);
      if (content && new TextEncoder().encode(content).byteLength > 900000) throw new CloudSyncError('同步请求过大，本地数据已保留。');
      const response = await this.fetcher(this.origin + '/sync/v1/' + path, { method: content ? 'POST' : 'GET',
        redirect: 'error', signal: abort.signal, headers: { 'Content-Type': 'application/json',
          Authorization: 'Bearer ' + this.token }, ...(content ? { body: content } : {}) });
      if (response.status === 409) throw new CloudConflict('云端版本冲突，本地修改已保留。');
      if (!response.ok || response.status < 200 || response.status > 201) throw new CloudSyncError(`云同步未完成（HTTP ${response.status}）。`);
      const reader = response.body?.getReader();
      if (!reader) throw new CloudSyncError('设备不支持有界同步响应。');
      let count = 0, text = ''; const decoder = new TextDecoder();
      try {
        while (true) {
          const result = await reader.read();
          if (result.done) { text += decoder.decode(); break; }
          count += result.value.byteLength;
          if (count > 1048576) throw new CloudSyncError('云端响应过大，已停止读取。');
          text += decoder.decode(result.value, { stream: true });
        }
      } finally { await reader.cancel().catch(() => {}); }
      const result: unknown = JSON.parse(text);
      if (!result || typeof result !== 'object' || Array.isArray(result)) throw new CloudSyncError('云端响应格式无效。');
      return result as Record<string, unknown>;
    } catch (error) {
      if (error instanceof CloudSyncError) throw error;
      throw new CloudSyncError('云同步网络或响应不可用，本地待发送内容已保留。');
    } finally { clearTimeout(timer); }
  }
  pushEvents(events: CloudEvent[]) { return this.request('events', { events }); }
  async pullEvents(after: number): Promise<CloudPage> {
    if (!Number.isSafeInteger(after) || after < 0) throw new CloudSyncError('游标无效。');
    return await this.request(`events?after=${after}&limit=100`) as unknown as CloudPage;
  }
  submitTask(task: CloudTask) { return this.request('tasks', task); }
}

export class CloudSyncEngine {
  private serial: Promise<unknown> = Promise.resolve();
  private readonly store: CloudStateStore;
  private readonly transport: CloudTransport;
  private readonly makeId: () => string;
  constructor(store: CloudStateStore, transport: CloudTransport, makeId: () => string) {
    this.store = store; this.transport = transport; this.makeId = makeId;
  }
  private async load(): Promise<CloudState> {
    const text = await this.store.load();
    if (text === null) return emptyState();
    let result: unknown;
    try { result = JSON.parse(text); } catch { throw new CloudSyncError('本地同步队列损坏，原内容保留。'); }
    checkState(result); return result;
  }
  private exclusive<T>(operation: () => Promise<T>): Promise<T> {
    const result = this.serial.then(operation, operation);
    this.serial = result.catch(() => {}); return result;
  }
  snapshot(): Promise<CloudState> { return this.exclusive(async () => clone(await this.load())); }
  enqueue(entity: CloudEntity, entityId: string, payload: Record<string, unknown> = {}, operation: 'upsert' | 'delete' = 'upsert'): Promise<CloudEvent> {
    return this.exclusive(async () => {
      const state = await this.load();
      if (state.outbox.some(item => item.entity === entity && item.entity_id === entityId)) throw new CloudConflict('该记录已有待发送修改，请先同步或处理冲突。');
      const current = state.records.find(item => item.entity === entity && item.entity_id === entityId);
      const event: CloudEvent = { event_id: this.makeId(), entity, entity_id: entityId, operation,
        base_revision: current?.revision || 0, payload: clone(payload) };
      validEvent(event); state.outbox.push(event); await this.store.save(JSON.stringify(state)); return clone(event);
    });
  }
  enqueueTask(workspaceId: string, sessionId: string, prompt: string, taskId = this.makeId()): Promise<CloudTask> {
    return this.exclusive(async () => {
      identifier(workspaceId); identifier(sessionId); identifier(taskId);
      if (!prompt.trim() || prompt.length > 12000) throw new CloudSyncError('电脑任务需要非空文字，最多12000字符。');
      const state = await this.load(), task = { id: taskId, workspace_id: workspaceId, session_id: sessionId, prompt };
      const previous = state.tasks.find(item => item.id === taskId);
      if (previous) {
        if (previous.prompt !== prompt || previous.workspace_id !== workspaceId || previous.session_id !== sessionId) throw new CloudConflict('任务编号已存在，不会覆盖或重复提交。');
        return task;
      }
      state.tasks.push({ ...task, local_state: 'pending' }); await this.store.save(JSON.stringify(state)); return task;
    });
  }
  syncOnce(): Promise<CloudState> {
    return this.exclusive(async () => {
      const state = await this.load(), pending: (CloudEvent & { conflict?: boolean })[] = [];
      for (const item of state.outbox.filter(value => !value.conflict).slice(0, 100)) {
        if (new TextEncoder().encode(JSON.stringify({ events: [...pending, item] })).byteLength > 900000) break;
        pending.push(item);
      }
      if (pending.length) {
        try {
          const submitted = pending.map(({ conflict: _, ...event }) => event);
          validReceipt(await this.transport.pushEvents(submitted), submitted);
        }
        catch (error) {
          if (!(error instanceof CloudConflict)) throw error;
          pending.forEach(item => { item.conflict = true; });
          await this.store.save(JSON.stringify(state));
        }
        if (!pending.some(item => item.conflict)) {
          const accepted = new Set(pending.map(item => item.event_id));
          state.outbox = state.outbox.filter(item => !accepted.has(item.event_id));
          await this.store.save(JSON.stringify(state));
        }
      }
      const page = await this.transport.pullEvents(state.cursor);
      if (!page || !Array.isArray(page.events) || page.events.length > 100 || typeof page.more !== 'boolean' ||
          !Number.isSafeInteger(page.cursor) || page.cursor < state.cursor) throw new CloudSyncError('云端分页格式无效。');
      // Work on a copy so malformed pages cannot partly advance the durable cursor.
      const next = clone(state); let latest = state.cursor;
      for (const item of page.events) {
        validEvent(item, true);
        if (item.cursor <= state.cursor) continue;
        if (item.cursor <= latest || item.cursor > page.cursor) throw new CloudSyncError('云端事件顺序无效。');
        latest = item.cursor;
        const current = next.records.find(value => value.entity === item.entity && value.entity_id === item.entity_id);
        if (current && item.revision <= current.revision) throw new CloudSyncError('云端记录版本倒退。');
        next.outbox.filter(value => value.entity === item.entity && value.entity_id === item.entity_id && value.event_id !== item.event_id)
          .forEach(value => { value.conflict = true; });
        next.records = next.records.filter(value => value.entity !== item.entity || value.entity_id !== item.entity_id);
        next.records.push(clone(item));
      }
      if (latest !== page.cursor) throw new CloudSyncError('云端游标与事件内容不一致。');
      next.cursor = latest; await this.store.save(JSON.stringify(next));
      for (const task of next.tasks.filter(item => item.local_state === 'pending')) {
        const { local_state: _, result: __, ...payload } = task;
        try {
          const receipt = await this.transport.submitTask(payload) as { task?: CloudTask };
          if (!receipt || !receipt.task || Object.entries(payload).some(([key, value]) => receipt.task![key as keyof CloudTask] !== value)) {
            throw new CloudSyncError('云端未确认相同任务，原任务保留并使用原编号重试。');
          }
          task.result = receipt; task.local_state = 'submitted';
        }
        catch (error) { if (!(error instanceof CloudConflict)) throw error; task.local_state = 'conflict'; }
        await this.store.save(JSON.stringify(next));
      }
      return clone(next);
    });
  }
}
