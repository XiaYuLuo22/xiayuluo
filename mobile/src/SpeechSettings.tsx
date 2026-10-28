import { useEffect, useState } from 'react';
import { Pressable, StyleSheet, Text, TextInput, View } from 'react-native';
import type { useReplySpeech } from './speech';

/** Explicit local settings only; never starts a microphone, download or preview. */
export function SpeechSettings({ speech, disabled = false }: {
  speech: ReturnType<typeof useReplySpeech>; disabled?: boolean;
}) {
  const [voiceId, setVoiceId] = useState<string | null>(speech.status.voice_id);
  const [rate, setRate] = useState(String(speech.status.rate ?? 180));
  const [volume, setVolume] = useState(String(Math.round((speech.status.volume ?? 1) * 100)));
  useEffect(() => {
    setVoiceId(speech.status.voice_id); setRate(String(speech.status.rate ?? 180));
    setVolume(String(Math.round((speech.status.volume ?? 1) * 100)));
  }, [speech.status.voice_id, speech.status.rate, speech.status.volume]);
  const locked = disabled || speech.changing || speech.loadingVoices;
  return <View style={s.card}>
    <Text style={s.heading}>个性化声音 · 本机设置</Text>
    <Text style={s.hint}>只列系统已安装、无需网络的中文音色，不下载或克隆声音。语速是 Android 相对档位，不是精确每分钟字数；保存后下次新回复生效，不重播历史。</Text>
    {!speech.personalizationSupported ? <Text style={s.hint}>当前原生模块尚不支持音色设置；原有朗读开关不受影响，需要安装含此功能的新 APK。</Text> : <>
      <Pressable accessibilityRole="button" disabled={locked} style={[s.button, locked && s.disabled]} onPress={() => void speech.loadVoices()}>
        <Text style={s.buttonText}>{speech.loadingVoices ? '正在读取…' : '刷新本机中文离线音色'}</Text></Pressable>
      {speech.voicesKnown && !speech.voices.length && <Text style={s.hint}>没有可用音色，请先在安卓系统文字转语音设置安装中文离线声音，再返回刷新。</Text>}
      {speech.voicesKnown && !!speech.voices.length && <>
        <Pressable accessibilityRole="radio" accessibilityState={{ selected: voiceId === null }} disabled={locked}
          onPress={() => setVoiceId(null)} style={s.choice}><Text style={s.text}>{voiceId === null ? '● ' : '○ '}默认可用中文音色</Text></Pressable>
        {speech.voices.slice(0, 32).map(voice => <Pressable key={voice.id} accessibilityRole="radio"
          accessibilityState={{ selected: voiceId === voice.id }} disabled={locked} onPress={() => setVoiceId(voice.id)} style={s.choice}>
          <Text style={s.text}>{voiceId === voice.id ? '● ' : '○ '}{voice.name} · {voice.languages.join(', ')}</Text></Pressable>)}
        {speech.voices.length > 32 && <Text style={s.hint}>当前显示前32个本机音色。</Text>}
        <Text style={s.label}>相对语速档位（80–300，默认180）</Text>
        <TextInput style={s.input} editable={!locked} keyboardType="number-pad" maxLength={3} value={rate}
          onChangeText={setRate} accessibilityLabel="本机朗读相对语速" />
        <Text style={s.label}>音量百分比（0–100）</Text>
        <TextInput style={s.input} editable={!locked} keyboardType="number-pad" maxLength={3} value={volume}
          onChangeText={setVolume} accessibilityLabel="本机朗读音量" />
        <Pressable accessibilityRole="button" disabled={locked || !speech.known || !rate.trim() || !volume.trim()}
          style={[s.button, (locked || !speech.known) && s.disabled]}
          onPress={() => void speech.configurePreferences(voiceId, Number(rate), Number(volume) / 100)}>
          <Text style={s.buttonText}>{speech.changing ? '正在保存…' : '保存本机声音设置'}</Text></Pressable>
      </>}
    </>}
    {!!speech.error && <Text accessibilityLiveRegion="polite" style={s.hint}>{speech.error}</Text>}
  </View>;
}

const s = StyleSheet.create({
  card: { borderWidth: 1, borderColor: '#e2e9db', borderRadius: 15, padding: 18, marginBottom: 14 },
  heading: { color: '#4a6340', fontSize: 15 }, hint: { color: '#738267', fontSize: 12, lineHeight: 20, marginTop: 8 },
  label: { color: '#4a6340', fontSize: 12, marginTop: 12 }, text: { color: '#506b43', fontSize: 12, lineHeight: 20 },
  choice: { paddingVertical: 8 }, input: { borderWidth: 1, borderColor: '#dde6d4', borderRadius: 9, padding: 10, color: '#506b43', marginTop: 6 },
  button: { backgroundColor: '#46895f', padding: 11, borderRadius: 10, marginTop: 12, alignItems: 'center' },
  buttonText: { color: '#fff', fontSize: 12 }, disabled: { opacity: .5 },
});
