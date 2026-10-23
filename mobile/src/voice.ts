import { useEffect, useRef, useState } from 'react';
import { AppState, NativeModules, PermissionsAndroid, Platform, type Permission } from 'react-native';
import type { Connection } from './client';
import { VoiceAutostart } from './voiceAutostart';

export type VoiceStatus = { available: boolean; running: boolean; phase: string; message: string;
  session_id?: string | null; battery_exempt?: boolean };
type VoiceModule = { status(): Promise<string>; start(url: string, token: string): Promise<void>;
  stop(): Promise<void>; openPowerSettings(): Promise<void> };
const native = NativeModules.XiayuluoVoice as VoiceModule | undefined;
const supported = Platform.OS === 'android' && !!native;
const failure = (error: unknown) => error instanceof Error ? error.message : '请检查麦克风权限、离线中文语音和电脑连接。';

async function foregroundAfterPermission(): Promise<boolean> {
  if (AppState.currentState === 'active') return true;
  // Permission activities can briefly leave RN inactive even after permission is
  // granted. Wait for the real foreground event, never start a background mic.
  return new Promise(resolve => {
    const listener = AppState.addEventListener('change', state => {
      if (state === 'active') { clearTimeout(timer); listener.remove(); resolve(true); }
    });
    const timer = setTimeout(() => { listener.remove(); resolve(false); }, 8000);
  });
}

/** Lives with Home, even when Devices/VoiceCard is unmounted or JS is backgrounded. */
export function useVoice(connection: Connection | null, ready: boolean, online: boolean) {
  const [status, setStatus] = useState<VoiceStatus>({ available: false, running: false, phase: 'off', message: '正在读取语音状态…' });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [foreground, setForeground] = useState(AppState.currentState === 'active');
  const gate = useRef(new VoiceAutostart());
  const operation = useRef(false);
  const alive = useRef(true);
  const generation = useRef(0);

  async function refresh() {
    if (!supported || !native) {
      if (alive.current) setStatus({ available: false, running: false, phase: 'off', message: '需要包含原生语音模块的安卓安装包；Expo Go / iOS / 网页不支持。' });
      return;
    }
    try {
      const value = JSON.parse(await native.status()) as VoiceStatus;
      if (alive.current) setStatus(value);
    } catch { if (alive.current) setError('无法读取原生语音状态，请重新打开 App。'); }
  }

  useEffect(() => {
    alive.current = true;
    generation.current += 1;
    void refresh();
    const timer = setInterval(() => { if (AppState.currentState === 'active') void refresh(); }, 2000);
    const listener = AppState.addEventListener('change', state => {
      setForeground(state === 'active');
      if (state === 'active') void refresh();
    });
    return () => {
      alive.current = false;
      generation.current += 1;
      clearInterval(timer); listener.remove();
      // Native onTaskRemoved/process death owns App closure. A renderer reload or
      // Activity recreation must not stop/restart a healthy background service.
    };
  }, []);

  async function start(automatic = false) {
    if (!connection || !native || !supported || AppState.currentState !== 'active' || operation.current) return;
    if (!automatic) gate.current.manualStart();
    operation.current = true; setBusy(true); setError('');
    const launch = generation.current;
    try {
      const permissions: Permission[] = [PermissionsAndroid.PERMISSIONS.RECORD_AUDIO];
      if (Number(Platform.Version) >= 33) permissions.push(PermissionsAndroid.PERMISSIONS.POST_NOTIFICATIONS);
      const result = await PermissionsAndroid.requestMultiple(permissions);
      if (permissions.some(name => result[name] !== PermissionsAndroid.RESULTS.GRANTED)) {
        throw new Error('未获麦克风/通知权限，未开始监听。本次不会反复请求；允许权限后可手动开启，或重新打开 App。');
      }
      if (!await foregroundAfterPermission()) throw new Error('App 尚未返回前台，未启动麦克风；请返回后手动开启。');
      if (!alive.current || launch !== generation.current || gate.current.suspended || AppState.currentState !== 'active') return;
      const current = JSON.parse(await native.status()) as VoiceStatus;
      if (!alive.current || launch !== generation.current || gate.current.suspended || AppState.currentState !== 'active') return;
      if (!current.running) await native.start(connection.url, connection.token);
      await refresh();
    } catch (cause) { if (alive.current) setError('语音未开启：' + failure(cause)); }
    finally { operation.current = false; if (alive.current) setBusy(false); }
  }

  useEffect(() => {
    if (gate.current.shouldStart({ ready, connected: !!connection, online, foreground,
      available: supported && status.available, running: status.running, busy: operation.current })) {
      void start(true);
    }
  }, [ready, connection, online, foreground, status.available, status.running]);

  async function stop() {
    gate.current.stop();
    if (supported && native) await native.stop();
    if (alive.current) { setError('本次已停止监听，不会因切换页面或返回前台自动重启；可手动开启。'); await refresh(); }
  }
  async function powerSettings() { if (native && supported) await native.openPowerSettings(); }
  const message = error || (ready && !connection ? '请先配对电脑；本次打开会在配对并在线后自动尝试免唤醒语音对话。' : status.message);
  return { status: { ...status, message }, busy, supported, canStart: supported && !!connection && status.available,
    start: () => start(false), stop, powerSettings };
}

export type VoiceController = ReturnType<typeof useVoice>;
