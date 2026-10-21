import React, { useState } from 'react';
import { Alert, Linking, Platform, Pressable, Text, View } from 'react-native';
import { checkUpdate, currentVersion, installUpdate } from './updates';
import type { Release } from './updateProtocol';
import config from './update-config.json';
import { downloadPage } from './downloadLinks';

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
  function install() {
    if (!release || busy || disabled) return;
    if (automatic) { void performInstall(release); return; }
    Alert.alert(`更新到 ${release.version}？`, '下载并校验后打开系统安装器。需要你确认安装，不要卸载旧版，配对与电脑记忆会保留。', [
      { text: '取消', style: 'cancel' }, { text: '下载更新', onPress: () => {
        setBusy(true);
        installUpdate(release, setMessage)
          .catch(error => setMessage(error instanceof Error ? error.message : '更新未完成，当前版本保留。'))
          .finally(() => setBusy(false));
      } },
    ]);
  }
  async function performInstall(target: Release) {
    setBusy(true);
    try { await installUpdate(target, setMessage); }
    catch (error) { setMessage(error instanceof Error ? error.message : '更新未完成，当前版本保留。'); }
    finally { setBusy(false); }
  }
  async function download() {
    if (busy || disabled) return;
    if (Platform.OS !== 'android') { setMessage('目前仅有安卓安装包，尚未提供 iOS 安装包。'); return; }
    if (release) { install(); return; }
    setBusy(true);
    try {
      await Linking.openURL(downloadPage(config.repository, 'android'));
      setMessage('已请求打开 GitHub 下载页。私有仓库请登录后选择安卓 APK；若尚无安装包，需先完成发布。下载后由系统确认覆盖安装，不要卸载旧版。这不表示已经更新成功。');
    } catch { setMessage('无法打开下载页，请检查系统浏览器和网络后重试。'); }
    finally { setBusy(false); }
  }
  return <View style={{ borderWidth: 1, borderColor: '#e2e9db', borderRadius: 15, padding: 18, marginBottom: 14 }}>
    <Text style={{ color: '#4a6340', fontSize: 16 }}>版本与更新</Text>
    <Text accessibilityLiveRegion="polite" style={{ color: '#738267', marginVertical: 12, lineHeight: 22 }}>{message}</Text>
    <Pressable accessibilityRole="button" disabled={busy} onPress={() => void check()} style={{ paddingVertical: 12 }}><Text style={{ color: '#46895f' }}>{busy ? '正在处理…' : '检查更新'}</Text></Pressable>
    <Pressable accessibilityRole="button" accessibilityLabel="下载更新" disabled={busy || disabled} onPress={() => void download()} style={{ padding: 13, backgroundColor: busy || disabled ? '#95ad9a' : '#46895f', borderRadius: 10, alignItems: 'center' }}><Text style={{ color: '#fff' }}>下载更新</Text></Pressable>
    <Text style={{ color: '#738267', marginTop: 10, fontSize: 12, lineHeight: 20 }}>无需先检查即可打开私有下载页。若已检测到签名新版本，则下载校验后交由系统确认安装。打开页面不等于安装成功。</Text>
  </View>;
}
