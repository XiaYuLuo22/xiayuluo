import { useEffect, useRef, useState } from 'react';
import { AppState, NativeModules, Platform } from 'react-native';
import { ReplySpeech, type SpeechNative } from './replySpeech';

const candidate = NativeModules.XiayuluoVoice as Partial<SpeechNative> | undefined;
const native = Platform.OS === 'android' && candidate &&
  ['speechStatus', 'configureSpeech', 'speakReply', 'stopSpeech'].every(name => typeof candidate[name as keyof SpeechNative] === 'function')
  ? candidate as SpeechNative : undefined;

/** Reads only native playback state, never chat history or the microphone. */
export function useReplySpeech(connected: boolean, online: boolean) {
  const [view, setView] = useState(() => new ReplySpeech(native).snapshot());
  const controller = useRef<ReplySpeech | null>(null);
  if (!controller.current) controller.current = new ReplySpeech(native, setView);
  const speech = controller.current;
  const [foreground, setForeground] = useState(AppState.currentState === 'active');

  useEffect(() => {
    speech.attach();
    void speech.refresh();
    const timer = setInterval(() => { if (AppState.currentState === 'active') void speech.refresh(); }, 1000);
    const listener = AppState.addEventListener('change', state => {
      setForeground(state === 'active');
      if (state !== 'active') void speech.setActive(false);
    });
    return () => { clearInterval(timer); listener.remove(); speech.dispose(); };
  }, [speech]);

  useEffect(() => { void speech.setActive(connected && online && foreground); }, [speech, connected, online, foreground]);
  return { ...view, supported: !!native,
    speaking: view.pending || (view.status.speaking && view.status.owner !== 'voice'),
    beginTurn: () => speech.beginTurn(), completeTurn: speech.completeTurn.bind(speech),
    stop: () => speech.stop(), configure: (enabled: boolean) => speech.configure(enabled) };
}
