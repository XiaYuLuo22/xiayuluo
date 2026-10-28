import { useEffect, useRef, useState } from 'react';
import { Alert, Platform, Pressable, Text, View } from 'react-native';
import { cancelUpdate, checkUpdate, currentVersion, getUpdateState, installUpdate,
  openInstallPermissionSettings, reopenInstaller, subscribeUpdateState } from './updates';
import type { Release } from './updateProtocol';
import { downloadUpdate } from './downloadUpdate';

export function UpdateCard({ disabled, automatic }: { disabled: boolean; automatic: boolean }) {
  const [release, setRelease] = useState<Release | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState(`当前版本 ${currentVersion}。点击「GitHub 一键更新」从固定 GitHub Releases 获取新版，无需电脑在线。`);
  const [installation, setInstallation] = useState(getUpdateState);
  const backgroundBusy = ['checking', 'downloading', 'verifying'].includes(installation.phase);
  const mounted = useRef(true), working = useRef(false), checking = useRef<AbortController | null>(null);
  const notify = (text: string) => { if (mounted.current) setMessage(text); };
  useEffect(() => {
    mounted.current = true;
    const unsubscribe = subscribeUpdateState(state => {
      if (!mounted.current) return;
      setInstallation(state);
      if (state.phase !== 'idle') setMessage(state.message);
    });
    return () => { mounted.current = false; checking.current?.abort(); unsubscribe(); };
  }, []);
  function begin() {
    if (working.current || backgroundBusy) return false;
    working.current = true; setBusy(true); return true;
  }
  function finish() { working.current = false; checking.current = null; if (mounted.current) setBusy(false); }
  async function check() {
    if (!begin()) return;
    const abort = checking.current = new AbortController();
    setRelease(null); notify('正在检查 GitHub Releases 的签名更新清单…');
    try {
      const result = await checkUpdate(abort.signal);
      if (mounted.current) setRelease(result.available ? result.release : null);
      notify(result.available ? `发现 ${result.release.version}\n${result.release.notes}` : `当前 ${currentVersion}，暂无更高版本。`);
    } catch (error) { notify(error instanceof Error ? error.message : 'GitHub 更新检查失败，请稍后重试。'); }
    finally { finish(); }
  }
  async function download() {
    if (disabled || working.current) return;
    if (Platform.OS !== 'android') { notify('目前仅有安卓安装包，尚未提供 iOS 安装包。'); return; }
    if (!begin()) return;
    const abort = checking.current = new AbortController();
    notify('正在检查 GitHub Releases 的签名更新清单…');
    try {
      await downloadUpdate<Release>({
        check: async () => { const result = await checkUpdate(abort.signal); if (mounted.current) setRelease(result.available ? result.release : null); return result; },
        approve: target => automatic ? Promise.resolve(true) : new Promise(resolve => Alert.alert(
          `更新到 ${target.version}？`, '从固定 GitHub Releases 自动下载并校验后交给安卓系统安装器，不跳转网页。不要卸载旧版，配对与电脑记忆会保留；系统安装确认仍需你操作。', [
            { text: '取消', style: 'cancel', onPress: () => { notify('已取消更新，当前版本保留。'); resolve(false); } },
            { text: '下载并安装', onPress: () => resolve(true) },
          ], { cancelable: true, onDismiss: () => { notify('已取消更新，当前版本保留。'); resolve(false); } })),
        install: async target => {
          if (!mounted.current || abort.signal.aborted) throw new Error('更新已取消，当前版本保留。');
          checking.current = null;
          await installUpdate(target, notify);
        },
        noUpdate: () => notify(`当前 ${currentVersion}，暂无更高版本。`),
      });
    } catch (error) { notify(error instanceof Error ? error.message : 'GitHub 更新未完成，当前版本保留，不会跳转网页。'); }
    finally { finish(); }
  }
  async function resume() {
    if (disabled || !begin()) return;
    try { await reopenInstaller(); }
    catch (error) { notify(error instanceof Error ? error.message : '安装器未能打开，已保留当前版。'); }
    finally { finish(); }
  }
  async function permissions() {
    if (!begin()) return;
    try { await openInstallPermissionSettings(); notify('返回后请点击“再次打开安装器”；系统确认仍需你操作。'); }
    catch (error) { notify(error instanceof Error ? error.message : '请在安卓设置检查安装权限。'); }
    finally { finish(); }
  }
  const cancellable = (busy && checking.current !== null) || backgroundBusy;
  return <View style={{ borderWidth: 1, borderColor: '#e2e9db', borderRadius: 15, padding: 18, marginBottom: 14 }}>
    <Text style={{ color: '#4a6340', fontSize: 16 }}>版本与更新</Text>
    <Text accessibilityLiveRegion="polite" style={{ color: '#738267', marginVertical: 12, lineHeight: 22 }}>{message}</Text>
    <Pressable accessibilityRole="button" disabled={busy || backgroundBusy} onPress={() => void check()} style={{ paddingVertical: 12 }}><Text style={{ color: '#46895f' }}>{busy || backgroundBusy ? '正在处理…' : '检查 GitHub 更新'}</Text></Pressable>
    <Pressable accessibilityRole="button" accessibilityLabel="GitHub 一键更新" disabled={busy || backgroundBusy || disabled} onPress={() => void download()} style={{ padding: 13, backgroundColor: busy || backgroundBusy || disabled ? '#95ad9a' : '#46895f', borderRadius: 10, alignItems: 'center' }}><Text style={{ color: '#fff' }}>GitHub 一键更新</Text></Pressable>
    {cancellable && <Pressable accessibilityRole="button" onPress={() => { checking.current?.abort(); cancelUpdate(); }} style={{ paddingVertical: 12 }}><Text style={{ color: '#46895f' }}>取消检查或下载</Text></Pressable>}
    {installation.cached && <Pressable accessibilityRole="button" disabled={busy || disabled} onPress={() => void resume()} style={{ paddingVertical: 12 }}><Text style={{ color: '#46895f' }}>再次打开安装器（重新校验）</Text></Pressable>}
    {installation.cached && Platform.OS === 'android' && <Pressable accessibilityRole="button" disabled={busy} onPress={() => void permissions()} style={{ paddingVertical: 12 }}><Text style={{ color: '#46895f' }}>检查安装未知应用权限</Text></Pressable>}
    <Text style={{ color: '#738267', marginTop: 10, fontSize: 12, lineHeight: 20 }}>点击后自动从固定 GitHub Releases 检查、下载并校验，不跳转网页，无需电脑在线或另建更新服务器。有可用签名新版才交给安卓系统安装器；系统安装确认不能跳过。GitHub 尚未发布可验证版本、不可访问或校验失败时保留当前版；返回 App 不代表已经安装成功。{release ? ` 已发现 ${release.version}。` : ''}</Text>
  </View>;
}
