"""Authorized offline microphone recognition, independent of the desktop renderer.

Only post-wake utterances reach the existing chat API. No audio is saved/uploaded.
Tool/Codex approvals are deliberately not implemented here.
"""
import importlib.util
import json
from pathlib import Path
from queue import Empty, Full, Queue
import re
import sys
from threading import Event, Lock, Thread
import time

import httpx


WAKE_NAMES = ("夏鱼洛", "夏雨洛", "夏雨落", "下雨落")
STOP_WORDS = {"停止对话", "结束对话", "退出语音", "停止监听"}


class WakeGate:
    """Feed completed offline ASR utterances, never partial hypotheses."""
    def __init__(self, clock=time.monotonic):
        self.clock, self.until = clock, 0.0

    def feed(self, text):
        text = re.sub(r"\s+", "", text)[:2000]
        wake = next(((text.find(name), name) for name in WAKE_NAMES if name in text), None)
        if wake:
            text = text[wake[0] + len(wake[1]):].strip("，。！？,.!?、")
            self.until = self.clock() + 30
        elif self.clock() >= self.until:
            return "ignore", ""
        if text.strip("，。！？,.!?、") in STOP_WORDS:
            self.until = 0
            return ("stop" if text.strip("，。！？,.!?、") in {"退出语音", "停止监听"} else "sleep"), ""
        if not text:
            return ("wake", "") if wake else ("ignore", "")
        self.until = self.clock() + 30
        return "chat", text

    def resume(self):
        self.until = self.clock() + 30


def model_directory():
    return Path(__file__).resolve().parent / "voice-model"


class DesktopVoice:
    def __init__(self, url, token, *, platform=None, model=None, autostart=True, speech=None):
        self.url, self.token = url, token
        self.platform = platform or sys.platform
        self.model = Path(model) if model else model_directory()
        self.stop_event, self.lock = Event(), Lock()
        self.thread = None
        self.session = None
        self.phase, self.message = "off", "语音未开启。"
        self.connection = None
        self.voice_token = None
        self.microphone = None
        self.autostart = autostart
        self.autostart_attempted = False
        self.speech = speech
        self.speech_active = Event()
        self.speech_generation = 0
        if speech is not None:
            speech.set_activity_callback(self._speech_activity)

    def _speech_activity(self, active):
        if active:
            self.speech_generation += 1
            self.speech_active.set()
        else:
            self.speech_active.clear()

    @staticmethod
    def _clear_audio(samples):
        while True:
            try:
                samples.get_nowait()
            except Empty:
                return

    def _pause_for_playback(self, stream, samples, recognizer):
        """Only the ASR worker stops/restarts its native stream, never the UI/TTS thread."""
        if not self.speech_active.is_set():
            return False
        stream.stop()
        self._clear_audio(samples)
        recognizer.Reset()
        self.phase, self.message = "speaking", "正在朗读回复，暂停麦克风识别以避免自唤醒。"
        while self.speech_active.is_set() and not self.stop_event.wait(.03):
            pass
        self._clear_audio(samples)
        recognizer.Reset()
        if not self.stop_event.is_set():
            stream.start()
            self.phase, self.message = "listening", "正在本地监听“夏鱼洛”。"
        return True

    def _speak(self, text):
        if self.speech is None or self.stop_event.is_set():
            return
        try:
            ticket = self.speech.say(text, source='voice')
        except ValueError:
            return  # A muted/unavailable speaker never silently opens another TTS path.
        while not ticket.done.wait(.03):
            if self.stop_event.is_set():
                self.speech.stop(source='voice')
                return

    def available(self):
        if self.platform != "win32":
            return False, "仅 Windows 桌面 App 支持本机语音；网页/云端不监听麦克风。"
        if not all(importlib.util.find_spec(name) for name in ("vosk", "sounddevice")):
            return False, '缺少语音依赖，请安装 .[app,desktop,voice] 或使用含语音的新版安装包。'
        if not (self.model / "am/final.mdl").is_file():
            return False, "缺少官方离线中文模型，请先运行 tools/prepare_voice_model.py。"
        return True, "本地中文识别；唤醒后才向当前聊天服务发送文字。"

    def status(self):
        available, reason = self.available()
        return {"available": available, "reason": reason, "running": bool(self.thread and self.thread.is_alive()),
                "phase": self.phase, "message": self.message, "session_id": self.session,
                "wake_word": "夏鱼洛", "follow_up_seconds": 30,
                "autostart": self.autostart, "autostart_attempted": self.autostart_attempted}

    def app_opened(self):
        """Called by the native window, never by headless service/UI polling."""
        if self.platform != "win32":
            return self.status()
        try:
            return self.start(automatic=True)
        except ValueError as error:
            self.phase, self.message = "off", "自动语音未开启：" + str(error)
            return self.status()

    def start(self, *, automatic=False):
        with self.lock:
            if automatic and (not self.autostart or self.autostart_attempted):
                return self.status()
            self.autostart_attempted = True
            if self.thread and self.thread.is_alive():
                return self.status()
            ready, reason = self.available()
            if not ready:
                raise ValueError(reason)
            self.stop_event = Event()
            self.phase, self.message = "starting", "正在加载离线模型和麦克风…"
            self.thread = Thread(target=self._run, name="xiayuluo-offline-voice", daemon=True)
            self.thread.start()
        return self.status()

    def stop(self):
        with self.lock:
            self.autostart_attempted = True  # Stop wins over a delayed/repeated window-shown event.
            self.stop_event.set()
        if self.speech is not None:
            self.speech.stop(source='voice')
        if self.microphone:
            try:
                self.microphone.abort()
            except Exception:
                pass
        connection = self.connection
        if connection:
            connection.close()
        self.phase, self.message = "off", "语音已停止；本次打开不自动重启，重新打开 App 后再尝试待命。"

    def _request(self, client, path, body=None):
        response = client.request("GET" if body is None else "POST", self.url + "/api/" + path, json=body)
        if response.status_code >= 400:
            raise ValueError("对话请求失败，请检查电脑聊天配置后重试；不会自动重发。")
        return response.json()

    def _chat(self, client, prompt):
        if not self.session:
            self.session = self._request(client, "sessions", {})["id"]
        text, done, pending = [], False, False
        with client.stream("POST", self.url + "/api/chat",
                           json={"session_id": self.session, "message": prompt, "mode": "chat",
                                 **({"voice_token": self.voice_token} if self.voice_token else {})}) as response:
            if response.status_code != 200:
                raise ValueError("对话请求失败；请查看聊天配置，不会自动重发。")
            self.connection = response
            for line in response.iter_lines():
                if self.stop_event.is_set():
                    return ""
                if len(line) > 65536:
                    raise ValueError("回复事件过大，已停止。")
                if not line.startswith("data:"):
                    continue
                event = json.loads(line[5:].strip())
                if event.get("type") == "error":
                    raise ValueError("本轮回复失败；请在聊天页查看详情。")
                if event.get("type") == "text":
                    text.append(event.get("text", ""))
                    if sum(map(len, text)) > 100000:
                        raise ValueError("回复过长，已停止。")
                if event.get("type") == "action":
                    pending = True
                if event.get("type") == "done":
                    done = True
            self.connection = None
        if not done:
            raise ValueError("对话连接中断，不会自动重发。")
        if pending:
            # Approval and tool execution remain in the desktop interface.
            self.phase, self.message = "approval", "请到电脑待确认任务批准，本轮不会自动执行。"
            return "本轮需要你在电脑待确认任务中批准。批准后的回答请查看聊天记录。"
        return "".join(text) or "本轮没有可朗读的回复。"

    def _run(self):
        stream = model = recognizer = None
        try:
            import sounddevice as sd
            import vosk
            vosk.SetLogLevel(-1)
            model = vosk.Model(str(self.model))
            recognizer = vosk.KaldiRecognizer(model, 16000)
            if self.stop_event.is_set():
                return
            samples = Queue(maxsize=30)
            overflow = Event()
            def receive(data, frames, timing, status):
                if self.speech_active.is_set() or self.stop_event.is_set():
                    return  # Discard playback/echo buffers before any ASR processing.
                if status:
                    overflow.set()
                try:
                    samples.put_nowait(bytes(data))
                except Full:
                    overflow.set()
            gate = WakeGate()
            with httpx.Client(headers={"X-Assistant-Token": self.token}, trust_env=False,
                              timeout=httpx.Timeout(100, connect=5)) as client:
                if self.stop_event.is_set():
                    return
                lease = self._request(client, "voice/sessions", {"confirm": True})
                self.session, self.voice_token = lease["session_id"], lease["voice_token"]
                if self.stop_event.is_set():
                    return
                halt = self.stop_event
                def heartbeat():
                    with httpx.Client(headers={"X-Assistant-Token": self.token}, trust_env=False, timeout=5) as pulse:
                        while not halt.wait(40):
                            try:
                                self._request(pulse, "voice/heartbeat", {"voice_token": self.voice_token})
                            except Exception:
                                if not halt.is_set():
                                    self.stop()
                                    self.phase, self.message = "error", "语音授权连接已中断，监听已停止，请重新开启。"
                Thread(target=heartbeat, name="xiayuluo-voice-lease", daemon=True).start()
                with sd.RawInputStream(samplerate=16000, blocksize=4000, dtype="int16", channels=1, callback=receive) as stream:
                    self.microphone = stream
                    observed_speech_generation = self.speech_generation
                    self.phase, self.message = "listening", "正在本地监听“夏鱼洛”；最小化后仍运行。"
                    while not self.stop_event.is_set():
                        if observed_speech_generation != self.speech_generation:
                            self._clear_audio(samples)
                            recognizer.Reset()
                            observed_speech_generation = self.speech_generation
                        if self._pause_for_playback(stream, samples, recognizer):
                            continue
                        if overflow.is_set():
                            raise ValueError("麦克风输入溢出或异常，监听已停止，请检查设备后重新开启。")
                        try:
                            audio = samples.get(timeout=.25)
                        except Empty:
                            continue
                        if self.speech_active.is_set():
                            continue
                        if not recognizer.AcceptWaveform(audio):
                            continue
                        action, prompt = gate.feed(json.loads(recognizer.Result()).get("text", ""))
                        if action == "ignore":
                            continue
                        stream.stop()  # Never hear our own synthesized voice or send ambient audio while busy.
                        if action == "stop":
                            self.stop_event.set()
                            break
                        if action == "sleep":
                            reply = "好的，等你再次呼唤夏鱼洛。"
                        elif action == "wake":
                            reply = "我在，请说。"
                        else:
                            self.phase, self.message = "thinking", "已唤醒，正在对话；回答时暂停识别。"
                            reply = self._chat(client, prompt)
                        if self.stop_event.is_set():
                            break
                        self.phase, self.message = "speaking", "正在朗读，暂停识别以避免自唤醒。"
                        self._speak(reply)
                        self._clear_audio(samples)
                        recognizer.Reset()
                        if action != "sleep":
                            gate.resume()
                        self.stop_event.wait(.3)
                        if not self.stop_event.is_set():
                            stream.start()
                            self.phase, self.message = "listening", "正在本地监听；唤醒后 30 秒内可接着说。"
        except Exception:
            if not self.stop_event.is_set():
                self.phase, self.message = "error", "语音已停止：请检查离线模型、麦克风权限/占用、系统中文语音和聊天配置，再手动开启。"
        finally:
            self.stop_event.set()
            self.microphone = None
            self.connection = None
            if self.speech is not None:
                self.speech.stop(source='voice')
            if self.voice_token:
                try:
                    with httpx.Client(headers={"X-Assistant-Token": self.token}, trust_env=False, timeout=3) as revoke:
                        self._request(revoke, "voice/revoke", {"voice_token": self.voice_token})
                except Exception:
                    pass  # Server expires a missing heartbeat in 150 seconds.
                self.voice_token = None
            if self.phase != "error":
                self.phase, self.message = "off", "语音已停止。"
