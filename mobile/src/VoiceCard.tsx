import React, { useEffect, useState } from 'react';
import { Alert, AppState, NativeModules, PermissionsAndroid, Platform, Pressable, Text, View, type Permission } from 'react-native';
import type { Connection } from './client';

type VoiceStatus = { available: boolean; running: boolean; phase: string; message: string; session_id?: string | null; battery_exempt?: boolean };
type VoiceModule = { status(): Promise<string>; start(url: string, token: string): Promise<void>; stop(): Promise<void>; openPowerSettings(): Promise<void> };
const native = NativeModules.XiayuluoVoice as VoiceModule | undefined;
export async function stopVoice() { if (Platform.OS === 'android' && native) await native.stop(); }

export function VoiceCard({ connection, disabled, automatic, onSession }: { connection: Connection | null; disabled: boolean; automatic: boolean; onSession(id: string): void }) {
  const [status, setStatus] = useState<VoiceStatus>({ available: false, running: false, phase: 'off', message: '正在读取语音状态…' });
  const [busy, setBusy] = useState(false);
  async function refresh() {
    if (Platform.OS !== 'android' || !native) { setStatus({ available: false, running: false, phase: 'off', message: '需要包含原生语音模块的安卓安装包；Expo Go / iOS / 网页不支持。' }); return; }
    try { setStatus(JSON.parse(await native.status()) as VoiceStatus); }
    catch { setStatus({ available: false, running: false, phase: 'error', message: '无法读取原生语音状态，请重新打开 App。' }); }
  }
  useEffect(() => {
    void refresh(); const timer = setInterval(() => { if (AppState.currentState === 'active') void refresh(); }, 2000);
    const listener = AppState.addEventListener('change', state => { if (state === 'active') void refresh(); });
    return () => { clearInterval(timer); listener.remove(); }; // Native service deliberately outlives React/background suspension.
  }, []);
  async function enable() {
    if (!connection || !native || AppState.currentState !== 'active' || busy || disabled) return;
    setBusy(true);
    try {
      const permissions: Permission[] = [PermissionsAndroid.PERMISSIONS.RECORD_AUDIO];
      if (Number(Platform.Version) >= 33) permissions.push(PermissionsAndroid.PERMISSIONS.POST_NOTIFICATIONS);
      const result = await PermissionsAndroid.requestMultiple(permissions);
      if (permissions.some(name => result[name] !== PermissionsAndroid.RESULTS.GRANTED)) throw new Error('需要允许麦克风和通知。请在系统应用设置中开启，再返回 App 重试。');
      await native.start(connection.url, connection.token); await refresh();
    } catch (error) { Alert.alert('语音未开启', error instanceof Error ? error.message : '请检查权限和离线模型。'); }
    finally { setBusy(false); }
  }
  return <View style={{ borderWidth: 1, borderColor: '#e2e9db', borderRadius: 15, padding: 18, marginBottom: 14 }}>
    <Text style={{ color: '#4a6340', fontSize: 16 }}>语音唤醒 · 夏鱼洛</Text>
    <Text accessibilityLiveRegion="polite" style={{ color: '#738267', marginVertical: 12, lineHeight: 22 }}>{status.message}</Text>
    <Pressable accessibilityRole="button" disabled={busy || (!status.running && (disabled || !connection || !status.available))}
      onPress={() => status.running ? void stopVoice().then(refresh).catch(() => Alert.alert('停止失败', '请通过常驻通知停止，或在系统设置强制停止本 App。')) : automatic ? void enable() : Alert.alert('开启语音与只读问答授权？', '持续使用麦克风并显示常驻通知，可能增加耗电。本地离线识别，不保存或上传环境录音。唤醒后文字经已配对电脑，连同近期上下文和相关记忆发送到官方 Codex CLI，不使用付费 API。语音通道仅只读问答，其他操作请用对应入口并遵循电脑执行方式。系统离线中文朗读和电脑 Codex ChatGPT 登录需可用。停止监听即撤销授权，断连授权最迟 150 秒过期。', [{ text: '取消' }, { text: '同意并开启', onPress: () => void enable() }])}
      style={{ backgroundColor: '#46895f', padding: 13, borderRadius: 10, alignItems: 'center', opacity: busy || (!status.running && (!connection || !status.available || disabled)) ? .45 : 1 }}>
      <Text style={{ color: '#fff' }}>{busy ? '正在开启…' : status.running ? '停止监听' : '开启语音唤醒'}</Text>
    </Pressable>
    {!!status.session_id && <Pressable accessibilityRole="button" onPress={() => onSession(status.session_id!)} style={{ paddingVertical: 12 }}><Text style={{ color: '#46895f' }}>查看语音对话</Text></Pressable>}
    {Platform.OS === 'android' && !!native && <Pressable accessibilityRole="button" onPress={() => void native.openPowerSettings().catch(() => Alert.alert('后台运行设置', '请到系统设置 → 应用 → 夏鱼洛 → 电池，手动允许后台运行/不限电量。'))} style={{ paddingVertical: 12 }}><Text style={{ color: '#46895f' }}>后台运行设置{status.battery_exempt ? ' · 已豁免系统电池优化' : ' · 尚未豁免系统电池优化'}</Text></Pressable>}
    <Text style={{ color: '#738267', marginTop: 10, fontSize: 12, lineHeight: 20 }}>默认不开麦；点击开启才持续监听并显示通知，会增加耗电。只将唤醒后文字、上下文和相关记忆经电脑发送给 Codex，不上传环境录音。说“夏鱼洛”唤醒，30 秒内可接着说；“结束对话”返回待唤醒，“停止监听”关闭麦克风。回答时暂停识别，暂不支持语音打断。后台运行依赖系统前台服务；强制停止、划掉 App、省电限制或麦克风被其他应用占用时可能中断，不能保证全天候唤醒。电脑必须在线，关闭/解除配对即停止。</Text>
  </View>;
}
