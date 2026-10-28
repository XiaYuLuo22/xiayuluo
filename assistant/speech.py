"""Local reply playback. All SAPI/pyttsx3 calls stay on one owned COM thread."""
from dataclasses import dataclass, field
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import sys
from tempfile import NamedTemporaryFile
from threading import Condition, Event, RLock, Thread
import time
from uuid import uuid4


DEFAULT_RATE = 180
DEFAULT_VOLUME = 1.0


def _rate(value):
    if type(value) is not int or not 80 <= value <= 300:
        raise ValueError('朗读语速必须为80至300之间的整数。')
    return value


def _volume(value):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError('朗读音量必须为0至1之间的有限数值。')
    return float(value)


def _languages(raw):
    if not isinstance(raw, (list, tuple)):
        raw = [raw]
    languages = []
    for item in raw[:16]:
        if isinstance(item, bytes):
            item = item.decode('utf-8', errors='ignore')
        if not isinstance(item, str):
            continue
        # Some pyttsx3 backends prefix a language tag with its byte length.
        item = item.strip(' ' + ''.join(map(chr, range(32)))).replace('_', '-')
        if re.fullmatch(r'[A-Za-z0-9-]{1,32}', item) and item not in languages:
            languages.append(item)
    return languages


def _voice_options(raw):
    if not isinstance(raw, (list, tuple)) or len(raw) > 256:
        raise ValueError('系统声音列表无效，请检查本机语音设置。')
    voices, identifiers = [], set()
    for value in raw:
        if not isinstance(value, dict):
            continue
        identifier, name = value.get('id'), value.get('name')
        if (not isinstance(identifier, str) or not identifier or len(identifier) > 1024
                or any(ord(char) < 32 or ord(char) == 127 for char in identifier)
                or identifier in identifiers or not isinstance(name, str)):
            continue
        name = re.sub(r'\s+', ' ', name).strip()[:200]
        if not name:
            continue
        languages = _languages(value.get('languages', []))
        chinese = value.get('chinese')
        if type(chinese) is not bool:
            chinese = (any(tag.casefold().split('-')[0] in {'zh', 'cmn', 'yue'} for tag in languages)
                       or any(token in name.casefold() for token in ('chinese', 'mandarin', '中文', '普通话')))
        voices.append({'id': identifier, 'name': name, 'languages': languages, 'chinese': chinese,
                       'local': True, 'source': 'installed-system'})
        identifiers.add(identifier)
    return voices


def speech_text(text, limit=3000):
    """Read prose, not Markdown syntax, executable samples, or tracking URLs."""
    text = re.sub(r'```[\s\S]*?(?:```|$)|~~~[\s\S]*?(?:~~~|$)', '，代码内容已省略。', text)
    text = re.sub(r'!\[[^\]]*\]\([^)]*\)', '', text)
    text = re.sub(r'\[([^\]]+)\]\([^)]*\)', r'\1', text)
    text = re.sub(r'https?://[^\s<>]+', '', text)
    text = re.sub(r'<[^>]*>', '', text)
    text = re.sub(r'^\s*(?:#{1,6}\s+|>\s*|[-*+]\s+|\d+[.)]\s+)', '', text, flags=re.MULTILINE)
    text = re.sub(r'[`*_~|]', '', text)
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    truncated = len(text) > limit
    if truncated:
        text = text[:limit].rstrip() + '。回复较长，剩余内容请查看文字。'
    return text, truncated


@dataclass(eq=False)
class SpeechTicket:
    spoken_text: str
    source: str
    truncated: bool = False
    done: Event = field(default_factory=Event)
    cancelled: bool = False
    error: str = ''


class _SystemDriver:
    """Construct, use and close on exactly the same Windows COM apartment."""
    def __init__(self):
        import pythoncom
        import pyttsx3
        self.com = pythoncom
        self.com.CoInitialize()
        try:
            self.engine = pyttsx3.init('sapi5')
        except Exception:
            self.com.CoUninitialize()
            raise

    def voices(self):
        return [{'id': str(voice.id), 'name': str(voice.name),
                 'languages': _languages(voice.languages),
                 'chinese': any(name in (str(voice.languages) + str(voice.name)).casefold()
                                for name in ('zh', 'chinese', '中文'))}
                for voice in self.engine.getProperty('voices')]

    def select(self, voice_id):
        self.engine.setProperty('voice', voice_id)

    def preferences(self, rate, volume):
        self.engine.setProperty('rate', rate)
        self.engine.setProperty('volume', volume)

    def speak(self, text, cancelled):
        if cancelled():
            return
        started = False
        try:
            self.engine.say(text.replace('<', '＜').replace('>', '＞'))
            self.engine.startLoop(False)
            started = True
            while self.engine.isBusy() and not cancelled():
                self.engine.iterate()
                time.sleep(.02)
        finally:
            self.engine.stop()
            if started:
                self.engine.endLoop()

    def close(self):
        try:
            self.engine.stop()
        finally:
            self.engine = None
            self.com.CoUninitialize()


class NativeSpeech:
    def __init__(self, data_dir, *, platform=None, driver_factory=None):
        self.path = Path(data_dir) / 'speech.json'
        self.platform = platform or sys.platform
        self.lock = RLock()
        self.condition = Condition(self.lock)
        self.enabled = True
        self.voice_id = ''
        self.rate, self.volume = DEFAULT_RATE, DEFAULT_VOLUME
        self._voice_options = []
        self.personalization_available = False
        self._invalid_configuration = False
        self.generation = 0
        self.available = False
        self.phase, self.message, self.reason = 'off', '回复朗读尚未就绪。', ''
        self.warning = ''
        self.active = self.pending = None
        self.closing = False
        self.thread = None
        self.activity_callback = None
        self.truncated = False
        self.ready = Event()
        self._load()
        if driver_factory is None:
            if self.platform != 'win32':
                self.reason = '系统回复朗读仅在 Windows 原生 App 中运行；浏览器/云端不播放电脑声音。'
            elif not all(importlib.util.find_spec(name) for name in ('pyttsx3', 'pythoncom')):
                self.reason = '缺少系统朗读依赖，请使用含语音的安装包或安装 .[voice]。'
            else:
                driver_factory = _SystemDriver
        if driver_factory is not None:
            self.phase, self.message = 'starting', '正在加载系统中文声音…'
            self.thread = Thread(target=self._run, args=(driver_factory,), name='xiayuluo-system-speech', daemon=True)
            self.thread.start()
        else:
            self.message = self.reason
            self.ready.set()

    def _load(self):
        if not self.path.exists():
            return
        try:
            if self.path.stat().st_size > 16384:
                raise ValueError('oversize')
            config = json.loads(self.path.read_text(encoding='utf-8'))
            if (not isinstance(config, dict) or not {'schema', 'enabled', 'voice_id'} <= set(config)
                    or set(config) - {'schema', 'enabled', 'voice_id', 'rate', 'volume'}
                    or type(config['schema']) is not int or config['schema'] != 1 or type(config['enabled']) is not bool
                    or not isinstance(config['voice_id'], str) or len(config['voice_id']) > 1024):
                raise ValueError('invalid')
            rate = _rate(config.get('rate', DEFAULT_RATE))
            volume = _volume(config.get('volume', DEFAULT_VOLUME))
            self.enabled, self.voice_id = config['enabled'], config['voice_id']
            self.rate, self.volume = rate, volume
        except (OSError, ValueError, TypeError):
            self.enabled = False
            self._invalid_configuration = True
            self.warning = '朗读配置损坏，已关闭自动朗读；请在设置重新选择。'

    def _save(self, enabled, voice_id, rate=DEFAULT_RATE, volume=DEFAULT_VOLUME):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        config = {'schema': 1, 'enabled': enabled, 'voice_id': voice_id}
        if rate != DEFAULT_RATE or volume != DEFAULT_VOLUME:
            config.update(rate=rate, volume=volume)
        try:
            with NamedTemporaryFile(mode='w', encoding='utf-8', prefix='.speech-', suffix='.tmp',
                                    dir=self.path.parent, delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(config, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            if self._invalid_configuration and self.path.exists():
                if self.path.is_symlink() or not self.path.is_file():
                    raise OSError('Invalid configuration is not a regular file.')
                # Only an explicit settings action repairs a broken config. Keep
                # its original bytes under an exclusive name before replacing it.
                backup = self.path.with_name('speech.invalid-' + uuid4().hex + '.json')
                os.link(self.path, backup, follow_symlinks=False)
            os.replace(temporary, self.path)
            self._invalid_configuration = False
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def status(self):
        with self.lock:
            return {'available': self.available, 'enabled': self.enabled, 'speaking': self.active is not None,
                    'phase': self.phase, 'message': self.warning or self.message, 'reason': self.reason,
                    'engine': 'system', 'voice_id': self.voice_id,
                    'voices': self.voices(), 'rate': self.rate, 'volume': self.volume,
                    'personalization_available': self.personalization_available,
                    'truncated': self.truncated, 'generation': self.generation}

    def voices(self):
        """Cached installed voices; never call a COM driver from an API thread."""
        with self.lock:
            return [{**voice, 'languages': list(voice['languages'])} for voice in self._voice_options]

    def set_activity_callback(self, callback):
        with self.lock:
            self.activity_callback = callback

    def configure(self, enabled, voice_id=None, rate=None, volume=None):
        if type(enabled) is not bool:
            raise ValueError('朗读开关必须是布尔值。')
        with self.condition:
            selected = self.voice_id if voice_id is None else voice_id
            if not isinstance(selected, str) or len(selected) > 1024:
                raise ValueError('音色编号格式不正确。')
            if selected and selected not in {voice['id'] for voice in self._voice_options}:
                raise ValueError('系统中未找到该音色，请刷新后选择。')
            selected_rate = self.rate if rate is None else _rate(rate)
            selected_volume = self.volume if volume is None else _volume(volume)
            if ((rate is not None or volume is not None) and not self.personalization_available):
                raise ValueError('当前系统驱动不支持语速和音量设置；原设置保持不变。')
            try:
                self._save(enabled, selected, selected_rate, selected_volume)
            except OSError as error:
                raise ValueError('无法保存朗读设置，原设置保持不变。') from error
            if (enabled, selected, selected_rate, selected_volume) != (self.enabled, self.voice_id, self.rate, self.volume):
                self.generation += 1
                self._cancel_locked()
            self.enabled, self.voice_id = enabled, selected
            self.rate, self.volume = selected_rate, selected_volume
            self.warning = ''
            self.message = '朗读设置已保存，下次新回复播放时生效。' if enabled else '回复朗读已关闭；不会重播旧回复。'
            self.condition.notify_all()
            return self.status()

    def say(self, text, *, source='chat', expected_generation=None):
        if not isinstance(text, str) or len(text) > 100000:
            raise ValueError('朗读文本过长或格式不正确。')
        cleaned, truncated = speech_text(text)
        if not cleaned:
            raise ValueError('本轮没有可朗读的正文。')
        with self.condition:
            if expected_generation is not None and (type(expected_generation) is not int or expected_generation != self.generation):
                raise ValueError('朗读状态已改变，旧回复不会继续播放。')
            if not self.enabled or self.closing:
                raise ValueError('回复朗读已关闭。')
            if not self.available:
                raise ValueError(self.reason or self.message)
            self._cancel_locked()
            ticket = SpeechTicket(cleaned, source, truncated)
            self.pending = ticket
            self.truncated = truncated
            self.phase, self.message = 'queued', '回复已加入本机朗读；可随时停止。'
            self.condition.notify_all()
            return ticket

    def _cancel_locked(self, source=None):
        for name in ('active', 'pending'):
            ticket = getattr(self, name)
            if ticket is not None and (source is None or ticket.source == source):
                ticket.cancelled = True
                if name == 'pending':
                    ticket.done.set()
                    self.pending = None
        if self.active is None and self.pending is None and self.available:
            self.phase = 'idle'

    def stop(self, source=None):
        with self.condition:
            if source is None or any(ticket is not None and ticket.source == source for ticket in (self.active, self.pending)):
                self.generation += 1
                self._cancel_locked(source)
                self.message = '朗读已停止；不会重播旧回复。'
            self.condition.notify_all()
            return self.status()

    def close(self):
        with self.condition:
            self.closing = True
            self.generation += 1
            self._cancel_locked()
            self.condition.notify_all()
        if self.thread:
            self.thread.join(timeout=5)
            if self.thread.is_alive():
                raise RuntimeError('系统朗读仍在停止，请稍后退出。')
        with self.lock:
            self.available = False
            self.phase, self.message = 'off', '系统朗读已退出。'

    def _run(self, factory):
        driver = None
        try:
            driver = factory()
            voices = _voice_options(driver.voices())
            chinese = next((voice for voice in voices if voice['chinese']), None)
            if not chinese:
                raise ValueError('缺少 Windows 系统本地中文声音，请在系统语言/语音设置中安装中文声音。')
            with self.condition:
                self._voice_options = voices
                self.personalization_available = callable(getattr(driver, 'preferences', None))
                if (not self.personalization_available
                        and (self.rate != DEFAULT_RATE or self.volume != DEFAULT_VOLUME)):
                    raise ValueError('当前系统驱动无法应用已保存的语速和音量，原配置保留。')
                if self.voice_id and self.voice_id not in {voice['id'] for voice in voices}:
                    self.warning = '先前的系统音色不可用，已使用默认中文声音。'
                    self.voice_id = ''
                self.available = True
                self.phase, self.message = 'idle', '使用系统中文声音；新完成回复可自动朗读，无需开启麦克风。'
                self.ready.set()
            while True:
                with self.condition:
                    self.condition.wait_for(lambda: self.closing or self.pending is not None)
                    if self.closing:
                        break
                    ticket, self.pending = self.pending, None
                    self.active = ticket
                    voice_id = self.voice_id or chinese['id']
                    rate, volume = self.rate, self.volume
                    callback = self.activity_callback
                    self.phase, self.message = 'speaking', '正在系统朗读；已暂停语音识别。'
                try:
                    if callback:
                        callback(True)
                    if not ticket.cancelled:
                        driver.select(voice_id)
                        if self.personalization_available:
                            driver.preferences(rate, volume)
                        if not ticket.cancelled and not self.closing:
                            driver.speak(ticket.spoken_text, lambda: ticket.cancelled or self.closing)
                except Exception:
                    ticket.error = '系统朗读失败，请检查系统中文声音和音频输出设备。'
                finally:
                    # Let the last output buffers drain before re-enabling ASR.
                    time.sleep(.3)
                    if callback:
                        try:
                            callback(False)
                        except Exception:
                            pass
                    with self.condition:
                        self.active = None
                        self.phase = 'idle'
                        self.message = ticket.error or ('朗读已停止。' if ticket.cancelled else
                            '朗读完成；长回复剩余部分请查看文字。' if ticket.truncated else '朗读完成。')
                        ticket.done.set()
        except Exception as error:
            with self.condition:
                self.available = False
                self.phase = 'error'
                self.reason = str(error) if isinstance(error, ValueError) else '无法加载系统中文声音，请检查系统语音和音频设备。'
                self.message = self.reason
                self._cancel_locked()
        finally:
            if driver:
                try:
                    driver.close()
                except Exception:
                    pass
            self.ready.set()
