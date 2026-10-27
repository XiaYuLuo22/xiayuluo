import { useEffect, useRef, useState } from 'react';
import { AppState, Platform, Pressable, StyleSheet, Text, TextInput, View } from 'react-native';
import { fetch } from 'expo/fetch';
import * as FS from 'expo-file-system/legacy';
import * as SecureStore from 'expo-secure-store';
import { CloudHTTPClient, CloudSyncEngine, CloudSyncError, type CloudState } from './cloudSync';
import { CloudStorageError, DurableCloudStore, MAX_CLOUD_STATE_BYTES, scopeForCloud,
  validateCloudConnection, type CloudConnection, type CloudFileAdapter } from './cloudStorage';
import { cloudRows, cloudTaskView, verifiedPhoneDevice } from './cloudWorkspace';

const connectionKey = 'xiayuluo.cloud.connection.v1';
const empty: CloudState = { schema: 1, cursor: 0, records: [], outbox: [], tasks: [] };
let nextId = 0;
// IDs are only idempotency identifiers, never credentials or authorization.
const makeId = () => `m${Date.now().toString(36)}-${(++nextId).toString(36)}-${Math.random().toString(36).slice(2, 14)}`;
type Session = { engine: CloudSyncEngine; client: CloudHTTPClient | null; scope: string };
type Workspace = { id: string; name: string };
const sessions = new Map<string, Session>();

function privateFiles(scope: string): CloudFileAdapter {
  if (!FS.documentDirectory || !/^[a-f0-9]{32}$/.test(scope)) throw new CloudStorageError('本机私有离线存储不可用。');
  const directory = `${FS.documentDirectory}xiayuluo-cloud/${scope}/`;
  function path(name: string) {
    if (!/^[A-Za-z0-9_.-]{1,200}$/.test(name) || name.includes('..')) throw new CloudStorageError('离线档案文件名无效。');
    return directory + name;
  }
  async function ensure() { await FS.makeDirectoryAsync(directory, { intermediates: true }); }
  return {
    async list() { await ensure(); return FS.readDirectoryAsync(directory); },
    async read(name) {
      const target = path(name), info = await FS.getInfoAsync(target);
      if (!info.exists) return null;
      if (info.isDirectory || !Number.isSafeInteger(info.size) || info.size < 0 || info.size > MAX_CLOUD_STATE_BYTES) throw new CloudStorageError('离线档案超过上限或不是普通文件，原数据保留。');
      return FS.readAsStringAsync(target);
    },
    async write(name, value) {
      await ensure(); const target = path(name);
      if ((await FS.getInfoAsync(target)).exists) throw new CloudStorageError('离线档案已存在，不覆盖。');
      await FS.writeAsStringAsync(target, value);
    },
    async move(from, to) {
      const target = path(to);
      if ((await FS.getInfoAsync(target)).exists) throw new CloudStorageError('离线档案目标已存在，不覆盖。');
      await FS.moveAsync({ from: path(from), to: target });
    },
    async remove(name) { await FS.deleteAsync(path(name), { idempotent: true }); },
  };
}
function workspaceSession(connection: CloudConnection): Session {
  const scope = scopeForCloud(connection);
  const existing = sessions.get(scope);
  if (existing) { existing.client = new CloudHTTPClient(connection.origin, connection.token, fetch); return existing; }
  if (sessions.size >= 8) throw new CloudStorageError('本次运行已切换多个云设备，请重新打开 App 后再连接；原离线档案保留。');
  const client = new CloudHTTPClient(connection.origin, connection.token, fetch);
  const session: Session = { engine: null!, client, scope };
  const currentClient = () => {
    if (!session.client) throw new CloudSyncError('本机云凭据已移除，队列保留但不会继续发起请求。');
    return session.client;
  };
  // One engine per scope keeps local mutations serialized across remounts and
  // valid token rotation, without moving data to another device's archive.
  session.engine = new CloudSyncEngine(new DurableCloudStore(privateFiles(scope)), {
    pushEvents: events => currentClient().pushEvents(events), pullEvents: cursor => currentClient().pullEvents(cursor),
    submitTask: task => currentClient().submitTask(task), getTask: id => currentClient().getTask(id),
  }, makeId);
  sessions.set(scope, session); return session;
}
function errorText(error: unknown): string {
  return error instanceof CloudStorageError || error instanceof CloudSyncError
    ? error.message : '云连接或本机存储暂不可用，离线记录保留，请检查服务和存储空间。';
}
function Button({ text, action, disabled = false }: { text: string; action(): void; disabled?: boolean }) {
  return <Pressable accessibilityRole="button" disabled={disabled} onPress={action} style={[s.button, disabled && s.disabled]}>
    <Text style={s.buttonText}>{text}</Text></Pressable>;
}

/** Independent optional cloud panel. Direct computer chat/voice stays a separate mode. */
export function CloudPanel({ initialWorkspaceId = '', onActivate }: { initialWorkspaceId?: string; onActivate?(): void }) {
  const [connection, setConnection] = useState<CloudConnection | null>(null);
  const [active, setActive] = useState(false), [ready, setReady] = useState(false), [busy, setBusy] = useState(false);
  const [origin, setOrigin] = useState(''), [deviceId, setDeviceId] = useState(''), [token, setToken] = useState('');
  const [state, setState] = useState<CloudState>(empty), [selected, setSelected] = useState<string | null>(null);
  const [draft, setDraft] = useState(''), [taskDraft, setTaskDraft] = useState(''), [memoryDraft, setMemoryDraft] = useState('');
  const [tier, setTier] = useState<'first' | 'second' | 'permanent'>('second');
  const [workspace, setWorkspace] = useState(initialWorkspaceId), [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [notice, setNotice] = useState('云服务未配置，不会上传数据。'), [lastSync, setLastSync] = useState<string | null>(null);
  const current = useRef<Session | null>(null), generation = useRef(0), mounted = useRef(true), working = useRef(false);
  const pendingTask = useRef<{ scope: string; workspace: string; sessionId: string; prompt: string; id: string } | null>(null);
  const activeRef = useRef(false); activeRef.current = active;
  function inform(value: string) { if (mounted.current) setNotice(value); }
  function finish() { working.current = false; if (mounted.current) setBusy(false); }
  function begin() { if (working.current) return false; working.current = true; setBusy(true); return true; }
  useEffect(() => {
    mounted.current = true; let alive = true;
    if (Platform.OS === 'web') { setReady(true); return () => { mounted.current = false; }; }
    void SecureStore.getItemAsync(connectionKey).then(raw => {
      if (!alive || !raw) return;
      if (new TextEncoder().encode(raw).length > 2048) throw new CloudStorageError('云连接记录无效，不会清空旧离线档案。');
      const saved = validateCloudConnection(JSON.parse(raw));
      setConnection(saved); setOrigin(saved.origin); setDeviceId(saved.deviceId);
      setNotice('云连接已安全保存。选择“独立云工作区”后才会联网同步；原电脑直连不变。');
    }).catch(() => { if (alive) setNotice('无法读取云连接记录，请重新校验连接；旧离线档案未删除。'); })
      .finally(() => { if (alive) setReady(true); });
    return () => { alive = false; mounted.current = false; activeRef.current = false; ++generation.current; };
  }, []);
  async function reload(session: Session) {
    const snapshot = await session.engine.snapshot();
    if (mounted.current && current.current === session) setState(snapshot);
    return snapshot;
  }
  async function sync() {
    const session = current.current;
    if (!activeRef.current || !session || AppState.currentState !== 'active' || !begin()) return;
    const version = generation.current;
    try {
      await session.engine.syncOnce();
      if (!activeRef.current || version !== generation.current) return;
      await session.engine.refreshTasks();
      if (!activeRef.current || version !== generation.current || !session.client) return;
      const list = await session.client.request('workspaces') as { workspaces?: unknown };
      if (!Array.isArray(list.workspaces) || list.workspaces.length > 128) throw new CloudSyncError('工作区列表无效，未改变任务绑定。');
      const values: Workspace[] = list.workspaces.map(item => {
        const value = item as { id?: unknown; name?: unknown; status?: unknown; online?: unknown };
        if (typeof value.id !== 'string' || !/^[A-Za-z0-9_-]{1,128}$/.test(value.id) || typeof value.name !== 'string' ||
            value.name.length > 80 || value.status !== 'registered' || value.online !== null) throw new CloudSyncError('工作区列表未核实。');
        return { id: value.id, name: value.name };
      });
      if (mounted.current && version === generation.current) {
        setWorkspaces(values); setLastSync(new Date().toLocaleTimeString());
        inform('本批文本与任务状态同步完成。已登记工作区不代表电脑在线；消息保存不等于已获模型回复。');
      }
    } catch (error) { if (mounted.current && version === generation.current) inform(`${errorText(error)} 保留上次回执，不自动重发旧任务。`); }
    finally { try { await reload(session); } catch (error) { if (version === generation.current) inform(errorText(error)); } finish(); }
  }
  useEffect(() => {
    if (!active || !connection) return;
    let session: Session;
    try { session = workspaceSession(connection); current.current = session; }
    catch (error) { inform(errorText(error)); return; }
    void reload(session).then(() => { if (activeRef.current && current.current === session) void sync(); }).catch(error => inform(errorText(error)));
    const timer = setInterval(() => { if (activeRef.current) void sync(); }, 15000);
    const listener = AppState.addEventListener('change', value => { if (value === 'active' && activeRef.current) void sync(); });
    return () => { clearInterval(timer); listener.remove(); };
  }, [active, connection]);
  async function configure() {
    if (Platform.OS === 'web') { inform('浏览器仅用于预览，不能保存云设备凭据或连接私人云。'); return; }
    if (!begin()) return;
    try {
      const candidate = validateCloudConnection({ schema: 1, origin, deviceId: deviceId.trim() || 'pending-verification',
        token: token.trim() || (connection && origin.trim().replace(/\/$/, '') === connection.origin ? connection.token : '') });
      const client = new CloudHTTPClient(candidate.origin, candidate.token, fetch);
      // Only this explicit user operation verifies credentials. Never use an admin token.
      const response = await client.request('device');
      let verifiedId: string;
      try { verifiedId = verifiedPhoneDevice(response, deviceId.trim()); }
      catch { throw new CloudStorageError('凭据不是对应手机设备；请使用云端登记的手机专用凭据，不使用管理员或桌面凭据。'); }
      const verified = validateCloudConnection({ ...candidate, deviceId: verifiedId });
      const session = workspaceSession(verified);
      await session.engine.snapshot(); // Do not replace a good connection with a corrupt/mixed local archive.
      await SecureStore.setItemAsync(connectionKey, JSON.stringify(verified), { keychainAccessible: SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY });
      ++generation.current; current.current = session;
      if (mounted.current) {
        setConnection(verified); setOrigin(verified.origin); setDeviceId(verified.deviceId); setToken(''); setLastSync(null);
        setSelected(null); setState(await session.engine.snapshot());
        inform('手机凭据已核实并保存，独立云工作区将在前台同步；旧服务离线档案不会被删除或混入。');
      }
    } catch (error) { inform(errorText(error)); }
    finally { finish(); }
  }
  async function forget() {
    if (!begin()) return;
    try {
      await SecureStore.deleteItemAsync(connectionKey);
      if (current.current) current.current.client = null;
      activeRef.current = false; ++generation.current; current.current = null;
      if (mounted.current) { setActive(false); setConnection(null); setToken(''); setState(empty); setSelected(null); setLastSync(null); setWorkspaces([]); }
      inform('已移除本机云凭据，离线档案与未发送记录保留。要撤销云访问，请由云服务管理员撤销设备。');
    } catch { inform('无法移除安全凭据，原配置保留。'); }
    finally { finish(); }
  }
  async function sessionId(title: string) {
    if (selected && cloudRows(await current.current!.engine.snapshot(), 'session').some(item => item.id === selected)) return selected;
    const id = makeId();
    await current.current!.engine.enqueue('session', id, { title: title.slice(0, 28) || '新的云对话', created_at: new Date().toISOString() });
    if (mounted.current) setSelected(id); return id;
  }
  async function localSave(kind: 'message' | 'task' | 'memory' | 'session') {
    const session = current.current;
    if (!session || !activeRef.current || !begin()) return;
    try {
      if (kind === 'session') { setSelected(null); const id = makeId(); await session.engine.enqueue('session', id,
        { title: '新的云对话', created_at: new Date().toISOString() }); if (mounted.current) setSelected(id); }
      else if (kind === 'message') {
        const content = draft.trim(); if (!content) return;
        const id = await sessionId(content);
        await session.engine.enqueue('message', makeId(), { session_id: id, role: 'user', content, created_at: new Date().toISOString() });
        if (mounted.current) setDraft('');
      } else if (kind === 'task') {
        if (!/^[A-Za-z0-9_-]{1,128}$/.test(workspace.trim()) || !taskDraft.trim()) throw new CloudStorageError('请先选择或核对电脑工作区，并填写明确任务。');
        const prompt = taskDraft.trim(), boundWorkspace = workspace.trim();
        const snapshot = await session.engine.snapshot(), existing = cloudRows(snapshot, 'session');
        const chosen = selected && existing.some(item => item.id === selected) ? selected : null;
        let staged = pendingTask.current;
        if (!staged || staged.scope !== session.scope || staged.workspace !== boundWorkspace || staged.prompt !== prompt ||
            (chosen && chosen !== staged.sessionId) || snapshot.records.some(item => item.entity === 'session' &&
              item.entity_id === staged!.sessionId && item.operation === 'delete')) {
          staged = { scope: session.scope, workspace: boundWorkspace, prompt, sessionId: chosen || makeId(), id: makeId() };
          pendingTask.current = staged;
        }
        if (!existing.some(item => item.id === staged.sessionId)) await session.engine.enqueue('session', staged.sessionId,
          { title: prompt.slice(0, 28), created_at: new Date().toISOString() });
        // Keep this ID across an uncertain local write/retry. Only an explicitly
        // changed prompt/workspace/session represents a new computer operation.
        await session.engine.enqueueTask(boundWorkspace, staged.sessionId, prompt, staged.id);
        if (mounted.current) { setTaskDraft(''); setSelected(staged.sessionId); }
        pendingTask.current = null;
      } else {
        if (!memoryDraft.trim()) return;
        const now = new Date(), expires = tier === 'permanent' ? null : new Date(now.getTime() + (tier === 'first' ? 7 : 30) * 86400000).toISOString();
        await session.engine.enqueue('memory', makeId(), { tier, kind: 'fact', content: memoryDraft.trim(), created_at: now.toISOString(),
          accessed_at: now.toISOString(), expires_at: expires, uses: 0, source_session: selected });
        if (mounted.current) setMemoryDraft('');
      }
      await reload(session); inform('已保存到本机离线队列。联网后同步；排队或保存文字不表示已经执行。');
    } catch (error) { inform(errorText(error)); }
    finally { try { await reload(session); } catch (error) { inform(errorText(error)); } finish(); }
  }
  const sessionRows = cloudRows(state, 'session').reverse(), messages = cloudRows(state, 'message').filter(item => item.payload.session_id === selected);
  const conflicts = state.outbox.filter(item => item.conflict);
  return <View style={s.card}>
    <Text style={s.heading}>连接方式与独立云工作区</Text>
    <Text style={s.hint}>原聊天/语音仍使用电脑直连。下方云面板独立保存文本与任务，不读取屏幕或控制鼠标；云语音暂不可用，选择云时暂停已有直连语音，不自动重开。</Text>
    <View style={s.row}><Button text="电脑直连（原聊天）" action={() => { setActive(false); activeRef.current = false; ++generation.current; inform('云面板已暂停，原聊天页仍使用已配对电脑；直连语音可手动重新开启。'); }} />
      <Button text="独立云工作区" disabled={!ready || busy || Platform.OS === 'web'} action={() => { onActivate?.(); setActive(true); }} /></View>
    <Text style={s.hint}>云服务尚需自行部署，公开 GitHub 不是此服务。只有显式连接且进入云工作区才同步，前台约15秒一轮，后台不保证运行。</Text>
    <Text accessibilityLiveRegion="polite" style={s.notice}>{notice}{lastSync ? ` 上次成功同步：${lastSync}` : ''}</Text>
    {active && <>
      <Text style={s.label}>云连接配置（手机设备专用凭据）</Text>
      <TextInput style={s.input} value={origin} onChangeText={setOrigin} placeholder="https://独立云服务域名" autoCapitalize="none" autoCorrect={false} maxLength={512} accessibilityLabel="独立云服务地址" />
      <TextInput style={s.input} value={deviceId} onChangeText={setDeviceId} placeholder="手机设备编号，可留空由服务核实" autoCapitalize="none" autoCorrect={false} maxLength={128} accessibilityLabel="云手机设备编号" />
      <TextInput style={s.input} value={token} onChangeText={setToken} placeholder={connection ? '已保存凭据，不显示；更换时粘贴新凭据' : '手机设备专用 token，不是管理员密钥'} secureTextEntry autoCapitalize="none" autoCorrect={false} maxLength={512} accessibilityLabel="云手机设备凭据" />
      <Button text={busy ? '正在处理…' : '校验并安全保存云连接'} disabled={busy || !ready || !origin.trim()} action={() => void configure()} />
      {connection && <><Text selectable style={s.hint}>已保存：{connection.origin} · {connection.deviceId}。凭据只在系统安全存储；不同服务/设备的离线档案隔离，换账号不会删除旧库。</Text>
        <Button text="暂停并移除本机云凭据（保留离线档案）" disabled={busy} action={() => void forget()} />
        <Button text="立即同步并刷新任务状态" disabled={busy} action={() => void sync()} />
        <Text style={s.hint}>本机待发送 {state.outbox.length} 条 · 任务 {state.tasks.length} 个 · 冲突 {conflicts.length} 条。单份档案最多2MiB；超过上限停止保存，不会清空旧档案。</Text>
        {!!conflicts.length && <View><Text style={s.label}>冲突保留，未自动覆盖</Text>{conflicts.slice(0, 5).map(item => <Text selectable key={item.event_id} style={s.hint}>{item.entity}：{JSON.stringify(item.payload).slice(0, 600)}</Text>)}</View>}
        <Text style={s.label}>云会话（含离线待发送）</Text>
        <Button text="新建云会话" disabled={busy} action={() => void localSave('session')} />
        {sessionRows.slice(0, 20).map(item => <Button key={item.id} text={`${selected === item.id ? '● ' : ''}${String(item.payload.title)}${item.pending ? '（待同步）' : ''}`} disabled={busy} action={() => setSelected(item.id)} />)}
        {messages.slice(-100).map(item => <View key={item.id} style={s.message}><Text style={s.hint}>{item.payload.role === 'assistant' ? '夏鱼洛' : '你'}{item.pending ? ' · 待同步' : ''}</Text><Text selectable style={s.text}>{String(item.payload.content).slice(0, 6000)}{String(item.payload.content).length > 6000 ? '\n（界面仅显示前6000字，原文保留）' : ''}</Text></View>)}
        <TextInput style={s.input} multiline value={draft} onChangeText={setDraft} maxLength={12000} placeholder="保存一条文字；不会自动触发模型或电脑操作" accessibilityLabel="云对话文字" />
        <Button text="保存文字到离线队列（不自动执行）" disabled={busy || !draft.trim()} action={() => void localSave('message')} />
        <Text style={s.hint}>点击保存后才写入离线档案；尚未保存的输入只在当前页面暂存。</Text>
        <Text style={s.label}>明确交给电脑的任务</Text>
        <Text style={s.hint}>普通云消息不会自动回复或执行。此按钮才提交电脑任务；电脑须开启云任务开关、在线且工作区匹配。已登记不等于在线，失败/中断任务不自动重放。</Text>
        {workspaces.map(item => <Button key={item.id} text={`${workspace === item.id ? '● ' : ''}${item.name}（已登记，在线状态未知）`} disabled={busy} action={() => setWorkspace(item.id)} />)}
        <TextInput style={s.input} value={workspace} onChangeText={setWorkspace} maxLength={128} placeholder="电脑工作区编号（可由上方选择或电脑复制）" autoCapitalize="none" autoCorrect={false} accessibilityLabel="云任务工作区编号" />
        <TextInput style={s.input} multiline value={taskDraft} onChangeText={setTaskDraft} maxLength={12000} placeholder="明确描述希望电脑执行的编程任务" accessibilityLabel="云电脑任务" />
        <Button text="交给电脑执行（离线时仅入队）" disabled={busy || !taskDraft.trim() || !workspace.trim()} action={() => void localSave('task')} />
        {state.tasks.slice(-20).reverse().map(task => { const view = cloudTaskView(task); return <View key={task.id} style={s.message}>
          <Text style={s.label}>{view.label}</Text><Text selectable style={s.text}>{task.prompt.slice(0, 1000)}</Text>
          {!!view.result && <Text selectable style={s.text}>{view.result.slice(0, 6000)}{view.result.length > 6000 ? '\n（界面仅显示前6000字，原结果保留）' : ''}</Text>}
        </View>; })}
        <Text style={s.label}>非临时云记忆</Text>
        {cloudRows(state, 'memory').slice(-20).map(item => <Text selectable key={item.id} style={s.hint}>{String(item.payload.tier)}：{String(item.payload.content)}{item.pending ? '（待同步）' : ''}</Text>)}
        <View style={s.row}>{(['first', 'second', 'permanent'] as const).map(value => <Button key={value} text={`${tier === value ? '● ' : ''}${value === 'first' ? '一级' : value === 'second' ? '二级' : '永久'}`} action={() => setTier(value)} disabled={busy} />)}</View>
        <TextInput style={s.input} multiline value={memoryDraft} onChangeText={setMemoryDraft} maxLength={2000} placeholder="只保存你明确提供的偏好或事实；不填写密码、密钥" accessibilityLabel="云记忆内容" />
        <Button text={tier === 'permanent' ? '保存永久记忆（不自动到期）' : '保存所选层级记忆'} disabled={busy || !memoryDraft.trim()} action={() => void localSave('memory')} />
      </>}
    </>}
  </View>;
}

const s = StyleSheet.create({
  card: { borderWidth: 1, borderColor: '#e2e9db', borderRadius: 15, padding: 18, marginBottom: 14 },
  heading: { color: '#4a6340', fontSize: 16 }, label: { color: '#4a6340', fontSize: 13, marginTop: 15, marginBottom: 6 },
  hint: { color: '#738267', fontSize: 12, lineHeight: 20, marginVertical: 6 }, text: { color: '#506b43', lineHeight: 22, fontSize: 13 },
  notice: { color: '#506b43', fontSize: 12, lineHeight: 20, marginVertical: 10 },
  input: { borderWidth: 1, borderColor: '#dde6d4', borderRadius: 10, backgroundColor: '#fafcf7', padding: 12, color: '#506b43', marginVertical: 6, fontSize: 12 },
  button: { backgroundColor: '#46895f', padding: 11, borderRadius: 10, marginVertical: 5, alignItems: 'center' },
  buttonText: { color: '#fff', fontSize: 12 }, disabled: { opacity: .5 }, row: { flexDirection: 'row', flexWrap: 'wrap', gap: 6 },
  message: { padding: 10, borderRadius: 10, backgroundColor: '#f0f4ec', marginVertical: 5 },
});
