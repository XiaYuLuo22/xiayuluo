package com.xiayuluo.assistant.voice

import android.content.Context
import android.media.AudioManager
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import android.speech.tts.Voice
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/** One local system-TTS owner for typed and microphone replies. Never opens a mic. */
object SpeechOutput {
    private val lock = Any()
    private val handler = Handler(Looper.getMainLooper())
    private var context: Context? = null
    private var engine: TextToSpeech? = null
    private val ready = CountDownLatch(1)
    private var initialized = false
    @Volatile private var available = false
    @Volatile private var enabled = true
    @Volatile private var message = "正在读取系统中文朗读音色…"
    private var settingsWarning: String? = null
    private var selectedVoice: String? = null
    private var preferences = SpeechPreferences(null, SpeechPreferences.DEFAULT_RATE, SpeechPreferences.DEFAULT_VOLUME)
    private var voiceChoices: List<Voice> = emptyList()
    private var sequence = 0L
    private var chatEpoch = 0L
    private var foreground = false
    @Volatile private var current: Request? = null
    val active: Boolean get() = current != null

    private class Request(val id: Long, val owner: String, val chunks: List<String>) {
        val lastId get() = "$id:${chunks.lastIndex}"
        val done = CountDownLatch(1)
        @Volatile var success = false
        @Volatile var error: String? = null
    }

    fun initialize(source: Context) {
        synchronized(lock) {
            if (initialized) return
            initialized = true
            context = source.applicationContext
            val prefs = source.getSharedPreferences("xiayuluo-speech", Context.MODE_PRIVATE)
            try {
                enabled = prefs.getBoolean("enabled", true)
                selectedVoice = prefs.getString("voice_id", null)
                preferences = SpeechPreferences.validated(selectedVoice,
                    prefs.getInt("rate", SpeechPreferences.DEFAULT_RATE).toDouble(),
                    prefs.getFloat("volume", SpeechPreferences.DEFAULT_VOLUME).toDouble())
            } catch (_: Exception) {
                enabled = false
                selectedVoice = null
                settingsWarning = "本机朗读设置无效，已关闭自动播放；原设置保留，请明确重新保存。"
            }
            handler.post {
                try {
                    engine = TextToSpeech(source.applicationContext) { result ->
                        handler.post {
                            synchronized(lock) {
                                try {
                                    if (result == TextToSpeech.SUCCESS) {
                                        updateVoiceChoicesLocked()
                                        val selected = selectedVoice?.let { id -> voiceChoices.firstOrNull { it.name == id } }
                                            ?: if (selectedVoice == null) voiceChoices.firstOrNull() else null
                                        available = selected != null && engine?.setVoice(selected) == TextToSpeech.SUCCESS &&
                                            engine?.setSpeechRate(preferences.speed) == TextToSpeech.SUCCESS
                                        if (available) selectedVoice = selected?.name
                                    }
                                    message = if (available) "使用系统本地中文音色，不上传文字到语音服务。"
                                              else "系统本地中文朗读音色不可用，请在系统文字转语音设置安装中文离线声音。"
                                } catch (_: Exception) {
                                    available = false
                                    message = "无法读取系统本地中文音色，请检查系统文字转语音设置。"
                                } finally { ready.countDown() }
                            }
                        }
                    }
                    engine?.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
                        override fun onStart(id: String?) {}
                        override fun onDone(id: String?) { finish(id, true) }
                        @Deprecated("TTS compatibility") override fun onError(id: String?) {
                            finish(id, false, "系统朗读失败，请检查本地中文音色。")
                        }
                        override fun onStop(id: String?, interrupted: Boolean) { finish(id, false) }
                    })
                } catch (_: Exception) {
                    available = false
                    message = "无法初始化系统本地中文朗读。"
                    ready.countDown()
                }
            }
        }
    }

    fun status(): JSONObject = synchronized(lock) {
        JSONObject().put("available", available).put("enabled", enabled).put("speaking", active)
            .put("ready", ready.count == 0L)
            .put("owner", current?.owner ?: JSONObject.NULL).put("provider", "system")
            .put("voice_id", selectedVoice ?: JSONObject.NULL).put("message", settingsWarning ?: message)
            .put("rate", preferences.rate).put("volume", preferences.volume.toDouble())
            .put("personalization_available", ready.count == 0L && voiceChoices.isNotEmpty())
    }

    /** Called only on the TTS/main thread. Never offers network or uninstalled voices. */
    private fun updateVoiceChoicesLocked() {
        voiceChoices = engine?.voices?.filter {
            it.locale.language == "zh" && !it.isNetworkConnectionRequired &&
                TextToSpeech.Engine.KEY_FEATURE_NOT_INSTALLED !in it.features.orEmpty() &&
                it.name.isNotBlank() && it.name.length <= 1024 &&
                it.name.none { char -> char.code < 32 || char.code == 127 }
        }?.sortedBy { it.name }?.take(256).orEmpty()
    }

    fun voices(success: (JSONArray) -> Unit, failure: () -> Unit) {
        handler.post {
            try {
                val result = synchronized(lock) {
                    check(ready.count == 0L && engine != null)
                    updateVoiceChoicesLocked()
                    JSONArray().apply {
                        for (voice in voiceChoices) put(JSONObject().put("id", voice.name)
                            .put("name", voice.name.take(200)).put("languages", JSONArray().put(voice.locale.toLanguageTag()))
                            .put("local", true).put("network_required", false).put("installed", true))
                    }
                }
                success(result)
            } catch (_: Exception) { failure() }
        }
    }

    fun configurePreferences(voiceId: String?, rate: Double, volume: Double,
                             success: (JSONObject) -> Unit, failure: () -> Unit) {
        val requested = try { SpeechPreferences.validated(voiceId, rate, volume) }
                        catch (_: Exception) { failure(); return }
        synchronized(lock) { chatEpoch++; stopLocked(null) }
        handler.post {
            try {
                val result = synchronized(lock) {
                    check(foreground && ready.count == 0L)
                    updateVoiceChoicesLocked()
                    val chosen = if (requested.voiceId == null) voiceChoices.firstOrNull()
                                 else voiceChoices.firstOrNull { it.name == requested.voiceId }
                    check(chosen != null)
                    val tts = engine ?: error("TTS unavailable")
                    val oldVoice = tts.voice
                    try {
                        check(tts.setVoice(chosen) == TextToSpeech.SUCCESS)
                        check(tts.setSpeechRate(requested.speed) == TextToSpeech.SUCCESS)
                        val editor = context?.getSharedPreferences("xiayuluo-speech", Context.MODE_PRIVATE)?.edit()
                            ?: error("Storage unavailable")
                        if (requested.voiceId == null) editor.remove("voice_id") else editor.putString("voice_id", requested.voiceId)
                        check(editor.putBoolean("enabled", enabled).putInt("rate", requested.rate)
                            .putFloat("volume", requested.volume).commit())
                    } catch (error: Exception) {
                        if (oldVoice != null) tts.setVoice(oldVoice)
                        tts.setSpeechRate(preferences.speed)
                        throw error
                    }
                    preferences = requested
                    selectedVoice = chosen.name
                    available = true
                    settingsWarning = null
                    message = "本机音色、相对语速和音量已保存；不会重播旧回复。"
                    status()
                }
                success(result)
            } catch (_: Exception) { failure() }
        }
    }

    fun setForeground(value: Boolean) {
        synchronized(lock) {
            if (foreground != value) { foreground = value; chatEpoch++ }
            if (!value) stopLocked("chat")
        }
    }

    fun requestChatGeneration(): Long = synchronized(lock) {
        chatEpoch++
        stopLocked("chat")
        chatEpoch
    }

    fun configure(value: Boolean) {
        synchronized(lock) {
            if (value && settingsWarning != null) return
            enabled = value
            context?.getSharedPreferences("xiayuluo-speech", Context.MODE_PRIVATE)
                ?.edit()?.putBoolean("enabled", value)?.apply()
            if (!value) { chatEpoch++; stopLocked(null) }
        }
    }

    fun stop(owner: String? = null) {
        synchronized(lock) {
            if (owner == null || owner == "chat") chatEpoch++
            stopLocked(owner)
        }
    }

    private fun stopLocked(owner: String?) {
        val request = current
        if (request != null && (owner == null || request.owner == owner)) {
            current = null; request.done.countDown()
            val ticket = ++sequence
            handler.post { synchronized(lock) { if (sequence == ticket) engine?.stop() } }
        }
    }

    private fun finish(id: String?, success: Boolean, error: String? = null) {
        synchronized(lock) {
            val request = current ?: return
            if (id == null || !id.startsWith("${request.id}:")) return
            if (success && id != request.lastId) return
            request.success = success; request.error = error
            current = null
            if (error != null) message = error
            request.done.countDown()
        }
    }

    /** Blocks only a dedicated worker, not the UI thread; cancellation invalidates queued calls. */
    fun speak(source: Context, text: String, owner: String, generation: Long? = null): Boolean {
        initialize(source)
        val chunks = SpeechText.chunks(text)
        if (chunks.isEmpty()) return false
        val request: Request
        synchronized(lock) {
            if (!enabled || (owner == "chat" && (!foreground || generation != chatEpoch))) return false
            stopLocked(null)
            request = Request(++sequence, owner, chunks)
            current = request
            handler.post { synchronized(lock) { if (current === request) engine?.stop() } }
        }
        try {
            check(ready.await(15, TimeUnit.SECONDS) && available) { "系统中文朗读音色不可用。" }
            // A voice service may still be in its bounded AudioRecord.read. Wait
            // for its explicit pause acknowledgement before producing any sound.
            val deadline = android.os.SystemClock.elapsedRealtime() + 3000
            while (current === request && VoiceState.micRecording) {
                check(android.os.SystemClock.elapsedRealtime() < deadline) { "麦克风尚未暂停，未播放回复。" }
                Thread.sleep(20)
            }
            synchronized(lock) {
                if (current !== request || !enabled) return false
                check((source.getSystemService(Context.AUDIO_SERVICE) as AudioManager).mode == AudioManager.MODE_NORMAL) {
                    "通话期间不播放回复。"
                }
                handler.post {
                    synchronized(lock) {
                        if (current === request && enabled) {
                            try { updateVoiceChoicesLocked() }
                            catch (_: Exception) {
                                finish(request.lastId, false, "本机中文音色状态无法核实，未开始朗读。")
                                return@synchronized
                            }
                            if (voiceChoices.none { it.name == selectedVoice } || engine?.voice?.name != selectedVoice) {
                                finish(request.lastId, false, "所选中文离线音色已不可用，请重新选择。")
                                return@synchronized
                            }
                            for ((index, chunk) in request.chunks.withIndex()) {
                                val mode = if (index == 0) TextToSpeech.QUEUE_FLUSH else TextToSpeech.QUEUE_ADD
                                val options = Bundle().apply { putFloat(TextToSpeech.Engine.KEY_PARAM_VOLUME, preferences.volume) }
                                if (engine?.speak(chunk, mode, options, "${request.id}:$index") != TextToSpeech.SUCCESS) {
                                    engine?.stop()
                                    finish(request.lastId, false, "系统朗读未能开始。")
                                    break
                                }
                            }
                        }
                    }
                }
            }
            check(request.done.await(180, TimeUnit.SECONDS)) { "朗读超时，已停止。" }
            request.error?.let { throw IllegalStateException(it) }
            return request.success
        } finally {
            synchronized(lock) { if (current === request) stopLocked(owner) }
        }
    }
}
