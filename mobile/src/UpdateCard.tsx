import React, { useState } from 'react';
import { Alert, Platform, Pressable, Text, View } from 'react-native';
import { checkUpdate, currentVersion, installUpdate } from './updates';
import type { Release } from './updateProtocol';

export function UpdateCard({ disabled }: { disabled: boolean }) {
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
  function install() {
    if (!release || busy || disabled) return;
    Alert.alert(`更新到 ${release.version}？`, '下载并校验后打开系统安装器。需要你确认安装，不要卸载旧版，配对与电脑记忆会保留。', [
      { text: '取消', style: 'cancel' }, { text: '下载更新', onPress: () => {
        setBusy(true);
        installUpdate(release, setMessage)
          .catch(error => setMessage(error instanceof Error ? error.message : '更新未完成，当前版本保留。'))
          .finally(() => setBusy(false));
      } },
    ]);
  }
  return <View style={{ borderWidth: 1, borderColor: '#e2e9db', borderRadius: 15, padding: 18, marginBottom: 14 }}>
    <Text style={{ color: '#4a6340', fontSize: 16 }}>版本与更新</Text>
    <Text accessibilityLiveRegion="polite" style={{ color: '#738267', marginVertical: 12, lineHeight: 22 }}>{message}</Text>
    <Pressable accessibilityRole="button" disabled={busy} onPress={() => void check()} style={{ paddingVertical: 12 }}><Text style={{ color: '#46895f' }}>{busy ? '正在处理…' : '检查更新'}</Text></Pressable>
    {!!release && Platform.OS === 'android' && <Pressable accessibilityRole="button" disabled={busy || disabled} onPress={install} style={{ paddingVertical: 12 }}><Text style={{ color: '#46895f' }}>下载并安装更新</Text></Pressable>}
  </View>;
}
