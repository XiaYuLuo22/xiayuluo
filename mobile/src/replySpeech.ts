/** Local playback only. No networking, microphone permission, or history observer. */
export type SpeechStatus = { available: boolean; enabled: boolean; speaking: boolean; message: string;
  provider: 'system'; voice_id: string | null; owner?: 'chat' | 'voice' | null; ready?: boolean;
  rate?: number; volume?: number; personalization_available?: boolean };
export type SpeechVoice = { id: string; name: string; languages: string[]; local: true;
  network_required: false; installed: true };
export type SpeechNative = { speechStatus(): Promise<string>; configureSpeech(enabled: boolean): Promise<string>;
  speakReply(text: string): Promise<boolean>; stopSpeech(): Promise<void>;
  speechVoices?(): Promise<string>;
  configureSpeechPreferences?(voiceId: string | null, rate: number, volume: number): Promise<string> };
export type SpeechView = { known: boolean; changing: boolean; pending: boolean; error: string; status: SpeechStatus;
  voices: SpeechVoice[]; voicesKnown: boolean; loadingVoices: boolean };

export function validSpeechPreferences(voiceId: unknown, rate: unknown, volume: unknown): boolean {
  return (voiceId === null || (typeof voiceId === 'string' && voiceId.trim().length > 0 && voiceId.length <= 1024 &&
      !/[\u0000-\u001f\u007f]/.test(voiceId))) &&
    typeof rate === 'number' && Number.isInteger(rate) && rate >= 80 && rate <= 300 &&
    typeof volume === 'number' && Number.isFinite(volume) && volume >= 0 && volume <= 1;
}

export function parseSpeechVoices(raw: string): SpeechVoice[] {
  if (typeof raw !== 'string' || raw.length > 512000) throw new Error('本机音色列表无效。');
  const value: unknown = JSON.parse(raw);
  if (!Array.isArray(value) || value.length > 256) throw new Error('本机音色列表无效。');
  const seen = new Set<string>();
  return value.map(item => {
    if (!item || typeof item !== 'object' || Array.isArray(item)) throw new Error('本机音色列表无效。');
    const voice = item as Record<string, unknown>;
    if (!validSpeechPreferences(voice.id, 180, 1) || typeof voice.id !== 'string' || seen.has(voice.id) ||
        typeof voice.name !== 'string' || !voice.name.trim() || voice.name.length > 200 ||
        /[\u0000-\u001f\u007f]/.test(voice.name) || voice.local !== true || voice.network_required !== false ||
        voice.installed !== true || !Array.isArray(voice.languages) || !voice.languages.length || voice.languages.length > 16 ||
        voice.languages.some(language => typeof language !== 'string' || !/^(?:zh|cmn|yue)(?:-[A-Za-z0-9]{1,8})*$/i.test(language))) {
      throw new Error('音色必须是系统已安装、无需网络的中文声音。');
    }
    seen.add(voice.id);
    return { id: voice.id, name: voice.name, languages: [...voice.languages] as string[],
      local: true, network_required: false, installed: true };
  });
}

const empty: SpeechStatus = { available: false, enabled: false, speaking: false, message: '正在读取本机朗读状态…',
  provider: 'system', voice_id: null, owner: null };

export function parseSpeechStatus(raw: string): SpeechStatus {
  const value: unknown = JSON.parse(raw);
  if (!value || typeof value !== 'object') throw new Error('朗读状态无效。');
  const data = value as Record<string, unknown>;
  if (typeof data.available !== 'boolean' || typeof data.enabled !== 'boolean' || typeof data.speaking !== 'boolean' ||
      typeof data.message !== 'string' || data.provider !== 'system' ||
      !(data.voice_id === null || typeof data.voice_id === 'string') ||
      !(data.ready === undefined || typeof data.ready === 'boolean') ||
      !(data.owner === undefined || data.owner === null || data.owner === 'chat' || data.owner === 'voice') ||
      !(data.rate === undefined || (typeof data.rate === 'number' && Number.isInteger(data.rate) && data.rate >= 80 && data.rate <= 300)) ||
      !(data.volume === undefined || (typeof data.volume === 'number' && Number.isFinite(data.volume) && data.volume >= 0 && data.volume <= 1)) ||
      !(data.personalization_available === undefined || typeof data.personalization_available === 'boolean')) {
    throw new Error('朗读状态无效，请更新 App 后重试。');
  }
  return { available: data.available, enabled: data.enabled, speaking: data.speaking, message: data.message,
    provider: 'system', voice_id: data.voice_id, owner: data.owner,
    ...(data.ready === undefined ? {} : { ready: data.ready }),
    ...(data.rate === undefined ? {} : { rate: data.rate }),
    ...(data.volume === undefined ? {} : { volume: data.volume }),
    ...(data.personalization_available === undefined ? {} : { personalization_available: data.personalization_available }) } as SpeechStatus;
}

/** Read prose, not code blocks or remote addresses. Never alters the displayed reply. */
export function spokenReply(text: string): string {
  const prose = text.replace(/```[\s\S]*?(?:```|$)/g, '（代码请查看文字）')
    .replace(/~~~[\s\S]*?(?:~~~|$)/g, '（代码请查看文字）')
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/https?:\/\/[^\s<>]+/gi, '（链接请查看文字）')
    .replace(/^\s{0,3}(?:#{1,6}\s+|>\s*|[-*+]\s+)/gm, '')
    .replace(/\*\*([^*]+)\*\*/g, '$1').replace(/__([^_]+)__/g, '$1')
    .replace(/`([^`]+)`/g, '$1').replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/g, '')
    .replace(/\n{3,}/g, '\n\n').trim();
  return prose.length > 8000 ? prose.slice(0, 7940) + '。回复较长，其余内容请查看文字。' : prose;
}

/** An epoch belongs to one newly submitted request; stop/off/on can never replay it. */
export class ReplySpeech {
  private native: SpeechNative | undefined;
  private changed: (view: SpeechView) => void;
  private view: SpeechView = { known: false, changing: false, pending: false, error: '', status: { ...empty },
    voices: [], voicesKnown: false, loadingVoices: false };
  private epoch = 0;
  private readVersion = 0;
  private delivered = -1;
  private active = false;
  private alive = true;
  private blocked = -1;

  constructor(native: SpeechNative | undefined, changed: (view: SpeechView) => void = () => {}) {
    this.native = native; this.changed = changed;
    if (!native) this.view = { ...this.view, status: { ...empty,
      message: '需要包含新版原生朗读模块的安卓 App；Expo Go / iOS / 网页暂不支持。' } };
  }

  snapshot(): SpeechView { return { ...this.view, status: { ...this.view.status },
    voices: this.view.voices.map(voice => ({ ...voice, languages: [...voice.languages] })) }; }
  get personalizationSupported(): boolean {
    return typeof this.native?.speechVoices === 'function' && typeof this.native.configureSpeechPreferences === 'function';
  }
  attach() {
    // React development lifecycle may set up the same effect again after its
    // cleanup. Old native completions remain invalid because dispose advanced epoch.
    this.alive = true;
  }
  private publish(patch: Partial<SpeechView>) {
    this.view = { ...this.view, ...patch };
    if (this.alive) this.changed(this.snapshot());
  }
  private error(cause: unknown): string { return cause instanceof Error ? cause.message : '请检查系统本地中文声音。'; }

  async refresh() {
    if (!this.native || !this.alive) return;
    const version = ++this.readVersion, epoch = this.epoch;
    try {
      const status = parseSpeechStatus(await this.native.speechStatus());
      if (this.alive && epoch === this.epoch && version === this.readVersion && !this.view.changing)
        this.publish({ status, known: true });
    } catch (cause) {
      if (this.alive && epoch === this.epoch && version === this.readVersion)
        this.publish({ known: false, error: '无法读取朗读状态：' + this.error(cause) });
    }
  }

  async setActive(active: boolean) {
    if (this.active === active) return;
    this.active = active;
    if (active) await this.refresh();
    else await this.stop();
  }

  private async stopAt(epoch: number) {
    if (!this.native) return;
    try {
      await this.native.stopSpeech();
      if (epoch === this.epoch && this.alive) {
        this.publish({ pending: false });
        await this.refresh();
      }
    } catch (cause) {
      if (epoch === this.epoch && this.alive) {
        this.blocked = epoch;
        this.publish({ known: false, error: '停止朗读失败：' + this.error(cause) });
      }
    }
  }

  async stop() {
    const epoch = ++this.epoch; ++this.readVersion;
    await this.stopAt(epoch);
  }

  async beginTurn(): Promise<number> {
    const epoch = ++this.epoch; ++this.readVersion;
    await this.stopAt(epoch);
    return epoch;
  }

  async configure(enabled: boolean) {
    if (!this.native || !this.alive || this.view.changing) return;
    const epoch = ++this.epoch; ++this.readVersion;
    this.publish({ changing: true, error: '' });
    try {
      const status = parseSpeechStatus(await this.native.configureSpeech(enabled));
      if (this.alive && epoch === this.epoch) this.publish({ status, known: true, pending: false });
    } catch (cause) {
      if (this.alive && epoch === this.epoch) this.publish({ known: false, error: '朗读设置失败：' + this.error(cause) });
    } finally {
      if (this.alive) this.publish({ changing: false });
      await this.refresh(); // A failed setting is never displayed as accepted consent.
    }
  }

  async loadVoices() {
    if (!this.alive || this.view.loadingVoices || this.view.changing) return;
    if (!this.personalizationSupported || !this.native?.speechVoices) {
      this.publish({ error: '需要包含音色设置的新安卓原生模块；旧 APK 仍可使用原有朗读开关。' }); return;
    }
    const epoch = this.epoch;
    this.publish({ loadingVoices: true, error: '' });
    try {
      const voices = parseSpeechVoices(await this.native.speechVoices());
      if (this.alive && epoch === this.epoch) this.publish({ voices, voicesKnown: true });
    } catch (cause) {
      if (this.alive && epoch === this.epoch) this.publish({ voicesKnown: false, error: '无法读取本机音色：' + this.error(cause) });
    } finally { if (this.alive) this.publish({ loadingVoices: false }); }
  }

  async configurePreferences(voiceId: string | null, rate: number, volume: number) {
    if (!this.alive || this.view.changing || this.view.loadingVoices) return;
    if (!this.personalizationSupported || !this.native?.configureSpeechPreferences) {
      this.publish({ error: '当前 APK 不支持音色个性化；请安装含新版原生模块的 APK。' }); return;
    }
    if (!validSpeechPreferences(voiceId, rate, volume) || !this.view.voicesKnown || !this.view.voices.length ||
        (voiceId !== null && !this.view.voices.some(voice => voice.id === voiceId))) {
      this.publish({ error: '请刷新并选择本机中文离线音色，语速80–300、音量0–100%。' }); return;
    }
    const epoch = ++this.epoch; ++this.readVersion;
    this.publish({ changing: true, pending: false, error: '' });
    try {
      await this.native.stopSpeech();
      if (!this.alive || epoch !== this.epoch) return;
      const status = parseSpeechStatus(await this.native.configureSpeechPreferences(voiceId, rate, volume));
      if (status.rate !== rate || status.volume === undefined || Math.abs(status.volume - volume) > .00001 ||
          status.personalization_available !== true || (voiceId !== null && status.voice_id !== voiceId))
        throw new Error('系统尚未确认所选音色设置。');
      if (this.alive && epoch === this.epoch) this.publish({ status, known: true, pending: false });
    } catch (cause) {
      if (this.alive && epoch === this.epoch) this.publish({ known: false, error: '声音设置未确认：' + this.error(cause) });
    } finally {
      if (this.alive) this.publish({ changing: false });
      await this.refresh();
    }
  }

  async completeTurn(epoch: number, text: string, options: { mode: 'chat' | 'codex'; complete: boolean;
    pending: boolean; aborted: boolean }): Promise<boolean> {
    if (epoch !== this.epoch || this.delivered === epoch) return false;
    this.delivered = epoch;
    const prose = spokenReply(text);
    if (!this.native || !this.alive || !this.active || !this.view.known || this.view.changing ||
        !this.view.status.available || !this.view.status.enabled || this.blocked === epoch ||
        options.mode !== 'chat' || !options.complete || options.pending || options.aborted || !prose) return false;
    this.publish({ pending: true, error: '' });
    try {
      const done = await this.native.speakReply(prose);
      return epoch === this.epoch && this.alive && done;
    } catch (cause) {
      if (epoch === this.epoch && this.alive) this.publish({ error: '文字已回复，朗读失败：' + this.error(cause) });
      return false;
    } finally {
      if (epoch === this.epoch && this.alive) {
        this.publish({ pending: false }); await this.refresh();
      }
    }
  }

  dispose() {
    this.alive = false; this.active = false; ++this.epoch; ++this.readVersion;
    // Only chat playback is stopped; the native voice service owns its lifecycle.
    void this.native?.stopSpeech().catch(() => {});
  }
}
