import React, { useState } from 'react';
import { Alert, Platform, Pressable, Text, View } from 'react-native';
import { checkUpdate, currentVersion, installUpdate } from './updates';
import type { Release } from './updateProtocol';
import { downloadUpdate } from './downloadUpdate';

export function UpdateCard({ disabled, automatic }: { disabled: boolean; automatic: boolean }) {
  const [release, setRelease] = useState<Release | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState(`当前版本 ${currentVersion}。电脑离线也可检查已发布的更新。`);
  async function check() {
    if (busy) return;
    setBusy(true); setRelease(null); setMessage('正在检查签名更新清单…');
    try {
      const result = await checkUpdate();
      setRelease(result.available ? result.release : null);
      setMessage(result.available ? `发现 ${result.release.version}\n${result.release.notes}` : `当前 ${currentVersion}，暂无更高版本。`);
    } catch (error) { setMessage(error instanceof Error ? error.message : '检查失败，请稍后重试。'); }
    finally { setBusy(false); }
  }
  async function download() {
    if (busy || disabled) return;
    if (Platform.OS !== 'android') { setMessage('目前仅有安卓安装包，尚未提供 iOS 安装包。'); return; }
    setBusy(true); setMessage('正在检查签名更新清单…');
    try {
      await downloadUpdate<Release>({
        check: async () => { const result = await checkUpdate(); setRelease(result.available ? result.release : null); return result; },
        approve: target => automatic ? Promise.resolve(true) : new Promise(resolve => Alert.alert(
          `更新到 ${target.version}？`, '在 App 内下载并校验后交给系统安装器。不要卸载旧版，配对与电脑记忆会保留。', [
            { text: '取消', style: 'cancel', onPress: () => resolve(false) },
            { text: '下载并安装', onPress: () => resolve(true) },
          ], { cancelable: true, onDismiss: () => resolve(false) })),
        install: target => installUpdate(target, setMessage),
        noUpdate: () => setMessage(`当前 ${currentVersion}，暂无更高版本。`),
      });
    } catch (error) { setMessage(error instanceof Error ? error.message : '更新未完成，当前版本保留，不会跳转网页。'); }
    finally { setBusy(false); }
  }
  return <View style={{ borderWidth: 1, borderColor: '#e2e9db', borderRadius: 15, padding: 18, marginBottom: 14 }}>
    <Text style={{ color: '#4a6340', fontSize: 16 }}>版本与更新</Text>
    <Text accessibilityLiveRegion="polite" style={{ color: '#738267', marginVertical: 12, lineHeight: 22 }}>{message}</Text>
    <Pressable accessibilityRole="button" disabled={busy} onPress={() => void check()} style={{ paddingVertical: 12 }}><Text style={{ color: '#46895f' }}>{busy ? '正在处理…' : '检查更新'}</Text></Pressable>
    <Pressable accessibilityRole="button" accessibilityLabel="下载更新" disabled={busy || disabled} onPress={() => void download()} style={{ padding: 13, backgroundColor: busy || disabled ? '#95ad9a' : '#46895f', borderRadius: 10, alignItems: 'center' }}><Text style={{ color: '#fff' }}>下载更新</Text></Pressable>
    <Text style={{ color: '#738267', marginTop: 10, fontSize: 12, lineHeight: 20 }}>点击后在 App 内检查、下载并校验，不跳转网页。有可用签名新版才交给安卓系统安装器；系统安装确认不能跳过。发布源未就绪或校验失败时保留当前版，不代表已经安装成功。{release ? ` 已发现 ${release.version}。` : ''}</Text>
  </View>;
}
