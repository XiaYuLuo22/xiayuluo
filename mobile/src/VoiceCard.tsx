import React from 'react';
import { Alert, Pressable, Text, View } from 'react-native';
import type { VoiceController } from './voice';

export function VoiceCard({ voice, disabled, automatic, onSession }: { voice: VoiceController; disabled: boolean; automatic: boolean; onSession(id: string): void }) {
  const { status, busy } = voice;
  return <View style={{ borderWidth: 1, borderColor: '#e2e9db', borderRadius: 15, padding: 18, marginBottom: 14 }}>
    <Text style={{ color: '#4a6340', fontSize: 16 }}>语音唤醒 · 夏鱼洛</Text>
    <Text accessibilityLiveRegion="polite" style={{ color: '#738267', marginVertical: 12, lineHeight: 22 }}>{status.message}</Text>
    <Pressable accessibilityRole="button" disabled={busy || (!status.running && (disabled || !voice.canStart))}
      onPress={() => status.running ? void voice.stop().catch(() => Alert.alert('停止失败', '请通过常驻通知停止，或在系统设置强制停止本 App。')) : automatic ? void voice.start() : Alert.alert('开启语音与只读问答授权？', '持续使用麦克风并显示常驻通知，可能增加耗电。本地离线识别，不保存或上传环境录音。唤醒后文字经已配对电脑，连同近期上下文和相关记忆发送到官方 Codex CLI，不使用付费 API。语音通道仅只读问答。停止监听即撤销授权。', [{ text: '取消' }, { text: '同意并开启', onPress: () => void voice.start() }])}
      style={{ backgroundColor: '#46895f', padding: 13, borderRadius: 10, alignItems: 'center', opacity: busy || (!status.running && (!voice.canStart || disabled)) ? .45 : 1 }}>
      <Text style={{ color: '#fff' }}>{busy ? '正在开启…' : status.running ? '停止监听' : '手动开启语音'}</Text>
    </Pressable>
    {!!status.session_id && <Pressable accessibilityRole="button" onPress={() => onSession(status.session_id!)} style={{ paddingVertical: 12 }}><Text style={{ color: '#46895f' }}>查看语音对话</Text></Pressable>}
    {voice.supported && <Pressable accessibilityRole="button" onPress={() => void voice.powerSettings().catch(() => Alert.alert('后台运行设置', '请到系统设置 → 应用 → 夏鱼洛 → 电池，手动允许后台运行/不限电量。'))} style={{ paddingVertical: 12 }}><Text style={{ color: '#46895f' }}>后台运行设置{status.battery_exempt ? ' · 已豁免系统电池优化' : ' · 尚未豁免系统电池优化'}</Text></Pressable>}
    <Text style={{ color: '#738267', marginTop: 10, fontSize: 12, lineHeight: 20 }}>打开 App 后，配对电脑在线且权限/本地语音就绪时自动尝试待命，不用先进入此页面。麦克风状态以实际提示和常驻通知为准。首次仍需系统授权；拒绝或出错后本次不反复重试。停止监听后本次不自动重启，下次重新打开才再尝试。只将唤醒后文字、上下文和相关记忆经电脑发送给 Codex，不上传环境录音。说“夏鱼洛”唤醒，30 秒内可接着说；“结束对话”返回待唤醒，“停止监听”关闭麦克风。回答时暂停识别，不支持语音打断。后台受系统省电、强制停止和麦克风占用限制，不能保证永不中断。</Text>
  </View>;
}
