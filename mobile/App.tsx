import React, { useEffect, useRef, useState } from 'react';
import { Alert, AppState, FlatList, KeyboardAvoidingView, Modal, Platform, Pressable,
  ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';
import { SafeAreaProvider, SafeAreaView } from 'react-native-safe-area-context';
import { StatusBar } from 'expo-status-bar';
import { UpdateCard } from './src/UpdateCard';
import { Client, loadConnection, pairDevice, saveConnection, type Action, type Connection,
  type Memory, type Message, type Session } from './src/client';

type Tab = 'chat' | 'memory' | 'tasks' | 'devices';
const tabs: [Tab, string, string][] = [['chat', '◌', '聊天'], ['memory', '▦', '记忆'], ['tasks', '◇', '任务'], ['devices', '⌁', '设备']];
const tiers = [['temporary', '临时性'], ['first', '一级'], ['second', '二级'], ['permanent', '永久']];
const errorText = (error: unknown) => error instanceof Error ? error.message : '连接失败，请检查电脑和网络。';

export default function App() {
  return <SafeAreaProvider><StatusBar style="dark" /><Home /></SafeAreaProvider>;
}
function Home() {
  const [tab, setTab] = useState<Tab>('chat');
  const [connection, setConnection] = useState<Connection | null>(null);
  const [ready, setReady] = useState(false);
  const [online, setOnline] = useState(false);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [memories, setMemories] = useState<Memory[]>([]);
  const [tasks, setTasks] = useState<Action[]>([]);
  const [history, setHistory] = useState(false);
  const [query, setQuery] = useState('');
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);
  const [mode, setMode] = useState<'chat' | 'codex'>('chat');
  const [notice, setNotice] = useState('');
  const [tier, setTier] = useState('first');
  const [memoryDraft, setMemoryDraft] = useState('');
  const [url, setUrl] = useState('');
  const [code, setCode] = useState('');
  const [name, setName] = useState('我的手机');
  const controller = useRef<AbortController | null>(null);
  const list = useRef<FlatList<Message>>(null);

  useEffect(() => {
    let alive = true;
    loadConnection().then(value => { if (alive) setConnection(value); })
      .catch(() => { if (alive) setNotice('无法读取安全存储，请在设备页重新配对。'); })
      .finally(() => { if (alive) setReady(true); });
    return () => { alive = false; controller.current?.abort(); };
  }, []);

  // The computer is authoritative. Poll only while foregrounded and not streaming;
  // no offline mutation queue, silent resends, or replicated memory promotion.
  useEffect(() => {
    if (!connection || busy) return;
    let alive = true, pending = false;
    const refresh = async () => {
      if (AppState.currentState !== 'active' || pending) return;
      pending = true;
      try {
        const client = new Client(connection);
        const [sessionList, memoryList, actions, historyData] = await Promise.all([
          client.json<Session[]>('sessions'), client.json<{ items: Memory[] }>('memories'),
          client.json<Action[]>('actions'), selected ? client.json<{ messages: Message[] }>(`sessions/${selected}`) : null,
        ]);
        if (!alive) return;
        setOnline(true); setSessions(sessionList); setMemories(memoryList.items); setTasks(actions);
        if (historyData) setMessages(historyData.messages);
      } catch (error) { if (alive) { setOnline(false); setNotice(errorText(error)); } }
      finally { pending = false; }
    };
    void refresh(); const timer = setInterval(() => void refresh(), 5000);
    const listener = AppState.addEventListener('change', state => { if (state === 'active') void refresh(); });
    return () => { alive = false; clearInterval(timer); listener.remove(); };
  }, [connection, selected, busy]);

  async function connect() {
    if (busy || !ready) return;
    setBusy(true);
    try {
      const value = await pairDevice(url.trim(), code.trim(), name.trim() || '我的手机');
      await saveConnection(value); setConnection(value); setCode(''); setNotice('配对成功，会话和记忆由电脑统一管理。'); setTab('chat');
    } catch (error) { setNotice(errorText(error)); }
    finally { setBusy(false); }
  }
  async function disconnect() {
    if (busy) return;
    try {
      await saveConnection(null); setConnection(null); setOnline(false); setSelected(null);
      setMessages([]); setSessions([]); setMemories([]); setTasks([]); setDraft(''); setMemoryDraft('');
      setNotice('已移除此手机的本地凭据。要彻底撤销访问，请同时在电脑设备页撤销设备。');
    } catch (error) { setNotice(errorText(error)); }
  }
  async function send() {
    if (busy || !draft.trim()) return;
    if (!connection || !online) { setNotice('请先在设备页配对并连接在线电脑。草稿暂留当前页面，不会自动发送。'); return; }
    const prompt = draft.trim(), client = new Client(connection);
    const abort = new AbortController(); controller.current = abort;
    setBusy(true); setNotice(''); let text = '';
    const targetId = -Date.now();
    try {
      const session = selected || (await client.json<Session>('sessions', {})).id;
      setSelected(session); setDraft('');
      setMessages(previous => [...previous, { id: targetId - 1, role: 'user', content: prompt },
        { id: targetId, role: 'assistant', content: '正在回应…' }]);
      await client.stream({ session_id: session, message: prompt, mode }, abort.signal, packet => {
        if (packet.type === 'text' && typeof packet.text === 'string') {
          text += packet.text;
          setMessages(previous => previous.map(item => item.id === targetId ? { ...item, content: text } : item));
        }
        if (packet.type === 'action') setNotice('请求已提交，请到电脑端「待确认任务」核实后执行。有效期 2 分钟。');
      });
    } catch (error) {
      const note = abort.signal.aborted ? '已停止，本轮不自动重发。' : errorText(error);
      setNotice(note); setDraft(current => current || prompt);
      setMessages(previous => previous.map(item => item.id === targetId ? { ...item, content: `${text}\n[未完成] ${note}` } : item));
    } finally { controller.current = null; setBusy(false); }
  }
  async function saveMemory() {
    if (!connection || !online || busy || !memoryDraft.trim() || tier === 'temporary') return;
    const content = memoryDraft.trim(), savedTier = tier;
    Alert.alert(savedTier === 'permanent' ? '永久记住这件事？' : '保存这条记忆？',
      savedTier === 'permanent' ? '会一直保留，直到你手动调整或删除。请勿记录密钥。' : '保存到电脑，同步给已配对的端。', [
        { text: '取消', style: 'cancel' }, { text: '确认保存', onPress: () => {
          setBusy(true);
          new Client(connection).json('memories', { tier: savedTier, content, kind: 'fact' })
            .then(() => { setMemoryDraft(''); setNotice('记忆已保存。'); }).catch(error => setNotice(errorText(error)))
            .finally(() => setBusy(false));
        } },
      ]);
  }

  return <SafeAreaView style={s.safe} edges={['top', 'bottom']}>
    <View style={s.header}>
      <Pressable accessibilityLabel="会话历史" onPress={() => setHistory(true)} disabled={busy}><Text style={s.headerIcon}>☰</Text></Pressable>
      <View style={s.headerBrand}><Text style={s.brand}>夏鱼洛</Text><Text style={s.subtle}>{tabs.find(item => item[0] === tab)?.[2]}</Text></View>
      <Pressable accessibilityLabel="新建对话" disabled={busy} onPress={() => { setSelected(null); setMessages([]); setTab('chat'); }}><Text style={s.headerIcon}>＋</Text></Pressable>
    </View>
    <View style={s.connection}><View style={[s.dot, { backgroundColor: online ? '#408d68' : '#c4a66b' }]} />
      <Text style={s.connectionText}>{connection ? online ? '已连接电脑 · 前台每 5 秒同步' : '电脑未连接 · 不会自动重发' : '本机未配对 · 连接你的电脑继续'}</Text></View>
    {!!notice && <Pressable accessibilityLabel="关闭提示" style={s.notice} onPress={() => setNotice('')}><Text style={s.noticeText}>{notice}　×</Text></Pressable>}
    {tab === 'chat' && <KeyboardAvoidingView style={s.flex} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
      <FlatList ref={list} data={messages} keyExtractor={item => String(item.id)} contentContainerStyle={s.chatList}
        onContentSizeChange={() => list.current?.scrollToEnd({ animated: false })}
        ListEmptyComponent={<View style={s.welcome}><Text style={s.hero}>✦</Text><Text style={s.title}>你好，我是夏鱼洛</Text>
          <Text style={s.description}>随时聊聊，也把想法带回电脑。{ '\n' }同一段对话，换个设备继续。</Text>
          {['聊聊今天发生的事', '帮我整理一个学习计划', '让 Codex 看看我的项目'].map((text, index) =>
            <Pressable key={text} style={s.suggestion} onPress={() => { setDraft(text); setMode(index === 2 ? 'codex' : 'chat'); }}>
              <Text style={s.suggestionText}>{text}</Text><Text style={s.subtle}>›</Text></Pressable>)}</View>}
        renderItem={({ item }) => <View style={[s.message, item.role === 'user' && s.userMessage]}>
          {item.role !== 'user' && <Text style={s.messageName}>✦ 夏鱼洛</Text>}<Text selectable style={s.messageText}>{item.content}</Text>
        </View>} />
      <View style={s.composer}><TextInput style={s.prompt} value={draft} onChangeText={setDraft} multiline maxLength={12000}
        editable={!busy} accessibilityLabel="输入消息" placeholder="和夏鱼洛聊聊，或交给我一件事…" placeholderTextColor="#93a18e" />
        <View style={s.composerBar}><Pressable disabled={busy} onPress={() => setMode(mode === 'chat' ? 'codex' : 'chat')}><Text style={s.mode}>{mode === 'chat' ? '◌ 日常对话' : '⌘ Codex 编程'} ⌄</Text></Pressable>
          <Pressable accessibilityLabel={busy ? '停止回复' : '发送消息'} disabled={busy && !controller.current} style={s.send} onPress={() => busy ? controller.current?.abort() : void send()}><Text style={s.sendText}>{busy ? '■' : '↑'}</Text></Pressable></View>
      </View><Text style={s.caption}>回答可能有误 · 电脑工具执行前需在电脑确认</Text>
    </KeyboardAvoidingView>}
    {tab === 'memory' && <ScrollView contentContainerStyle={s.page}><Text style={s.eyebrow}>MEMORY</Text><Text style={s.title}>把重要的事留下</Text>
      <Text style={s.description}>四级记忆共享电脑上的同一份数据。{ '\n' }历史对话与永久记忆分开管理。</Text>
      <View style={s.tiers}>{tiers.map(([id, label]) => <Pressable key={id} style={[s.tier, tier === id && s.tierSelected]} onPress={() => setTier(id)}><Text style={s.tierText}>{label}</Text></Pressable>)}</View>
      <Text style={s.hint}>{tier === 'temporary' ? '当前会话上下文，闲置 2 小时或电脑服务重启后清空。不复制到手机磁盘。' : tier === 'first' ? '近期片段 · 7 天 · 检索 3 次晋升二级' : tier === 'second' ? '强化记忆 · 30 天 · 每次检索续期' : '长期保留 · 不过期 · 只由你明确确认'}</Text>
      {tier !== 'temporary' && <><TextInput style={s.input} placeholder="一件希望夏鱼洛记住的事…" value={memoryDraft} onChangeText={setMemoryDraft} maxLength={2000} multiline />
        <Button label="确认保存记忆" onPress={() => void saveMemory()} disabled={!online || busy || !memoryDraft.trim()} /></>}
      {memories.filter(item => item.tier === tier).map(item => <View key={item.id} style={s.card}><Text selectable style={s.cardText}>{item.content}</Text><Text style={s.hint}>{item.expires_at ? `保留至 ${new Date(item.expires_at).toLocaleDateString()}` : '永久记忆 · 可在电脑调整或删除'}</Text></View>)}
      {tier !== 'temporary' && !memories.some(item => item.tier === tier) && <Text style={s.empty}>{online ? '这一层还没有记忆' : '连接电脑后查看记忆'}</Text>}
    </ScrollView>}
    {tab === 'tasks' && <ScrollView contentContainerStyle={s.page}><Text style={s.eyebrow}>COMPUTER TASKS</Text><Text style={s.title}>想法交给电脑完成</Text>
      <Text style={s.description}>手机提出任务，电脑核实后执行。{ '\n' }不会让手机直接操控鼠标或修改项目。</Text>
      <Button label="提交 Codex 任务" onPress={() => { setMode('codex'); setTab('chat'); }} />
      {tasks.map(task => <View key={task.id} style={s.card}><Text style={s.cardText}>{task.title}</Text><Text style={s.hint}>等待电脑确认 · 请求 2 分钟后过期</Text></View>)}
      {!tasks.length && <Text style={s.empty}>{online ? '暂时没有待确认任务' : '电脑离线时不执行任务'}</Text>}
    </ScrollView>}
    {tab === 'devices' && <ScrollView keyboardShouldPersistTaps="handled" contentContainerStyle={s.page}>
      <Text style={s.eyebrow}>YOUR DEVICES</Text><Text style={s.title}>随身聊，接着做</Text>
      <Text style={s.description}>手机、电脑、开发者模式，共用同一份对话与四级记忆。仅与你主动配对的电脑连接。</Text>
      <UpdateCard disabled={busy} />
      {connection ? <View style={s.card}><Text style={s.cardText}>{connection.name}</Text><Text selectable style={s.hint}>{connection.url}</Text>
        <Text style={s.hint}>凭据保存在系统安全存储，不同步模型密钥。解除本机配对不会删除电脑数据。</Text>
        <Button label="解除本机配对" disabled={busy} onPress={() => Alert.alert('解除配对？', '清除此手机凭据。电脑中可另行撤销访问权限。', [{ text: '取消' }, { text: '解除', style: 'destructive', onPress: () => void disconnect() }])} /></View> : <View style={s.card}>
        <Text style={s.cardText}>连接你的电脑</Text><Text style={s.hint}>先在电脑「设备与同步」生成 5 分钟有效的一次性配对邀请。同步服务需要 HTTPS，不能直接填写电脑的 localhost。</Text>
        <TextInput style={s.input} value={url} onChangeText={setUrl} placeholder="https://你的同步服务域名" autoCapitalize="none" autoCorrect={false} keyboardType="url" accessibilityLabel="同步服务地址" />
        <TextInput style={s.input} value={name} onChangeText={setName} placeholder="手机名称" maxLength={60} accessibilityLabel="手机名称" />
        <TextInput style={s.input} value={code} onChangeText={setCode} placeholder="粘贴电脑生成的配对邀请" autoCapitalize="none" autoCorrect={false} secureTextEntry accessibilityLabel="一次性配对邀请" />
        <Button label={busy ? '正在配对…' : '安全配对'} onPress={() => void connect()} disabled={busy || !ready || !url.trim() || !code.trim()} />
      </View>}
      <View style={s.card}><Text style={s.cardText}>同步边界</Text><Text style={s.hint}>电脑是当前版本的主机，需要保持运行。手机不存储完整聊天档案，前台每 5 秒刷新；流式回复实时接收。断网草稿仅暂存在当前运行页面，退出后不保证保留。暂无云端账号同步或后台推送。</Text></View>
    </ScrollView>}
    <View style={s.tabBar}>{tabs.map(([id, icon, label]) => <Pressable key={id} style={s.tab} accessibilityRole="tab" accessibilityState={{ selected: tab === id }} onPress={() => setTab(id)}><Text style={[s.tabIcon, tab === id && s.active]}>{icon}</Text><Text style={[s.tabLabel, tab === id && s.active]}>{label}</Text></Pressable>)}</View>
    <Modal visible={history} animationType="slide" onRequestClose={() => setHistory(false)}><SafeAreaView style={s.safe}>
      <View style={s.header}><Text style={s.brand}>最近聊过</Text><Pressable onPress={() => setHistory(false)}><Text style={s.headerIcon}>×</Text></Pressable></View>
      <TextInput style={[s.input, { marginHorizontal: 24 }]} placeholder="搜索会话" value={query} onChangeText={setQuery} />
      <FlatList data={sessions.filter(item => item.title.includes(query))} keyExtractor={item => item.id} contentContainerStyle={s.page}
        ListEmptyComponent={<Text style={s.empty}>连接电脑后，你的会话会出现在这里</Text>}
        renderItem={({ item }) => <Pressable style={s.card} disabled={busy} onPress={() => { setMessages([]); setSelected(item.id); setHistory(false); setTab('chat'); }}><Text style={s.cardText}>{item.title}</Text></Pressable>} />
    </SafeAreaView></Modal>
  </SafeAreaView>;
}
function Button({ label, onPress, disabled = false }: { label: string; onPress: () => void; disabled?: boolean }) {
  return <Pressable style={[s.button, disabled && { opacity: .45 }]} onPress={onPress} disabled={disabled}><Text style={s.buttonText}>{label}</Text></Pressable>;
}
const s = StyleSheet.create({
  safe: { flex: 1, backgroundColor: '#fcfcfa' }, flex: { flex: 1 },
  header: { paddingHorizontal: 24, height: 58, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  headerBrand: { flexDirection: 'row', alignItems: 'center', gap: 10 }, brand: { fontSize: 19, fontWeight: '600', color: '#304c3b' },
  headerIcon: { fontSize: 25, color: '#60856a', padding: 5 }, subtle: { color: '#91a18d', fontSize: 12 },
  connection: { flexDirection: 'row', gap: 7, alignItems: 'center', justifyContent: 'center', paddingBottom: 12 },
  dot: { width: 5, height: 5, borderRadius: 3 }, connectionText: { fontSize: 10, color: '#879680' },
  notice: { backgroundColor: '#f3f5e9', marginHorizontal: 20, padding: 11, borderRadius: 10, marginBottom: 8 }, noticeText: { color: '#738267', fontSize: 12, lineHeight: 20 },
  chatList: { padding: 23, flexGrow: 1 }, welcome: { paddingTop: 28 }, hero: { color: '#48916c', backgroundColor: '#eaf4e9', width: 56, height: 56, textAlign: 'center', lineHeight: 56, borderRadius: 18, fontSize: 37, marginBottom: 24 },
  title: { fontSize: 26, fontWeight: '600', color: '#304c37', marginBottom: 15 }, description: { color: '#7b8b74', fontSize: 13, lineHeight: 24, marginBottom: 25 },
  suggestion: { borderWidth: 1, borderColor: '#e3e9dd', backgroundColor: '#fff', padding: 17, borderRadius: 14, marginBottom: 11, flexDirection: 'row', justifyContent: 'space-between' }, suggestionText: { color: '#607655', fontSize: 13 },
  message: { marginBottom: 23, paddingVertical: 5 }, userMessage: { alignSelf: 'flex-end', maxWidth: '88%', backgroundColor: '#edf3e7', padding: 14, borderRadius: 16 },
  messageName: { color: '#7a9971', fontSize: 11, marginBottom: 10 }, messageText: { color: '#3b5237', fontSize: 14, lineHeight: 25 },
  composer: { marginHorizontal: 18, borderWidth: 1, borderColor: '#dfe7d8', backgroundColor: '#fff', borderRadius: 20, padding: 15 },
  prompt: { minHeight: 50, maxHeight: 140, fontSize: 13, lineHeight: 22, color: '#3b5237', textAlignVertical: 'top' },
  composerBar: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginTop: 7 },
  mode: { color: '#729568', backgroundColor: '#f0f5e9', padding: 7, borderRadius: 8, fontSize: 11 },
  send: { height: 34, width: 34, borderRadius: 17, backgroundColor: '#46895f', alignItems: 'center', justifyContent: 'center' }, sendText: { color: 'white', fontSize: 24 },
  caption: { color: '#91a088', fontSize: 9, textAlign: 'center', padding: 11 },
  page: { padding: 24, paddingBottom: 40 }, eyebrow: { color: '#93a38b', fontSize: 10, letterSpacing: 2, marginBottom: 12 },
  tiers: { flexDirection: 'row', gap: 7, marginBottom: 15 }, tier: { flex: 1, borderWidth: 1, borderColor: '#e0e8d8', borderRadius: 10, paddingVertical: 12 }, tierSelected: { backgroundColor: '#eaf2e3', borderColor: '#a6bf99' }, tierText: { textAlign: 'center', color: '#708963', fontSize: 12 },
  card: { borderWidth: 1, borderColor: '#e2e9db', backgroundColor: '#fff', borderRadius: 15, padding: 18, marginBottom: 14 }, cardText: { color: '#4a6340', fontSize: 14, lineHeight: 24 },
  hint: { color: '#839577', fontSize: 12, lineHeight: 22, marginVertical: 10 }, empty: { color: '#91a087', textAlign: 'center', fontSize: 12, padding: 35 },
  input: { borderWidth: 1, borderColor: '#dde6d4', borderRadius: 10, backgroundColor: '#fafcf7', padding: 13, color: '#506b43', marginVertical: 7, fontSize: 12 },
  button: { backgroundColor: '#46895f', padding: 13, borderRadius: 11, alignItems: 'center', marginVertical: 10 }, buttonText: { color: '#fff', fontSize: 12, fontWeight: '500' },
  tabBar: { flexDirection: 'row', borderTopWidth: 1, borderColor: '#e9ede4', backgroundColor: '#fcfcfa', paddingTop: 10, paddingBottom: 5 },
  tab: { flex: 1, alignItems: 'center', gap: 4 }, tabIcon: { fontSize: 23, color: '#99a690' }, tabLabel: { fontSize: 10, color: '#99a690' }, active: { color: '#43865b' },
});
