package com.xiayuluo.assistant.voice

import android.Manifest
import android.app.*
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.media.*
import android.os.*
import org.json.JSONObject
import org.vosk.Model
import org.vosk.Recognizer
import org.vosk.LibVosk
import org.vosk.LogLevel
import java.io.File

object VoiceState {
    val modelLock = Any()
    @Volatile var connection: VoiceConnection? = null
    @Volatile var running = false
    @Volatile var phase = "off"
    @Volatile var message = "语音未开启。"
    @Volatile var session: String? = null
    @Volatile var micRecording = false
    fun snapshot(modelReady: Boolean) = JSONObject().put("available", modelReady).put("running", running)
        .put("phase", phase).put("message", if (modelReady) message else "安装包缺少离线中文模型，不能开启语音。")
        .put("session_id", session ?: JSONObject.NULL)
}

/** Explicitly enabled microphone foreground service. No boot/restart receiver or hidden audio collection. */
class VoiceService : Service() {
    private val handler = Handler(Looper.getMainLooper())
    @Volatile private var stopped = false
    @Volatile private var recorder: AudioRecord? = null
    private var worker: Thread? = null
    private var chat: VoiceChat? = null
    private var power: PowerManager.WakeLock? = null
    private val renewPower = object : Runnable {
        override fun run() {
            if (!stopped && VoiceState.running) {
                power?.acquire(10 * 60 * 1000L)
                handler.postDelayed(this, 9 * 60 * 1000L)
            }
        }
    }
    private val heartbeat = object : Runnable {
        override fun run() {
            if (stopped) return
            Thread({
                try {
                    chat?.renew()
                    if (!stopped) handler.postDelayed(this, 40_000)
                } catch (_: Exception) { if (!stopped) handler.post { fail() } }
            }, "xiayuluo-voice-lease").start()
        }
    }
    override fun onBind(intent: Intent?) = null
    override fun onCreate() {
        super.onCreate()
        if (Build.VERSION.SDK_INT >= 26) {
            val channel = NotificationChannel("voice", "夏鱼洛语音监听", NotificationManager.IMPORTANCE_LOW)
            channel.description = "本地监听唤醒词，点停止立即关闭；持续监听会增加耗电。"
            getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
        }
    }
    private fun notification(text: String): Notification {
        val stop = PendingIntent.getService(this, 1, Intent(this, VoiceService::class.java).setAction("STOP"), PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        val open = PendingIntent.getActivity(this, 2, packageManager.getLaunchIntentForPackage(packageName)!!, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        val builder = if (Build.VERSION.SDK_INT >= 26) Notification.Builder(this, "voice") else Notification.Builder(this)
        return builder.setContentTitle("夏鱼洛 · 语音已开启").setContentText(text)
            .setSmallIcon(android.R.drawable.ic_btn_speak_now).setOngoing(true).setContentIntent(open)
            .setVisibility(Notification.VISIBILITY_PRIVATE).addAction(Notification.Action.Builder(null, "停止监听", stop).build()).build()
    }
    private fun state(phase: String, text: String) {
        if (stopped) return
        VoiceState.phase = phase; VoiceState.message = text
        getSystemService(NotificationManager::class.java).notify(4001, notification(text))
    }
    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == "STOP") { stopSelf(); return START_NOT_STICKY }
        val config = VoiceState.connection
        if (config == null) { stopSelf(); return START_NOT_STICKY }
        if (worker != null) return START_NOT_STICKY
        try {
            check(checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED)
            val notice = notification("正在加载离线中文模型…")
            if (Build.VERSION.SDK_INT >= 29) startForeground(4001, notice, ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE) else startForeground(4001, notice)
            VoiceState.running = true; VoiceState.phase = "starting"; VoiceState.message = "正在加载本地语音…"
            power = (getSystemService(POWER_SERVICE) as PowerManager).newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "Xiayuluo:Voice")
            power?.setReferenceCounted(false)
            renewPower.run()
            SpeechOutput.initialize(this)
            chat = VoiceChat(config) { stopped }
            worker = Thread({ listen() }, "xiayuluo-offline-voice").apply { start() }
        } catch (_: Exception) { fail() }
        return START_NOT_STICKY // Never resurrect a microphone after process death/reboot.
    }
    private fun prepareModel(): File {
      synchronized(VoiceState.modelLock) {
        check(!stopped)
        val metadata = assets.open("voice-model/xiayuluo-model.json").bufferedReader().use { it.readText() }
        val target = File(filesDir, "voice-model")
        if (File(target, "am/final.mdl").isFile && File(target, "xiayuluo-model.json").takeIf { it.isFile }?.readText() == metadata) return target
        val stage = File(filesDir, "voice-model-staging-" + java.util.UUID.randomUUID().toString())
        check(stage.mkdir())
        fun copy(asset: String, destination: File) {
            check(!stopped)
            val children = assets.list(asset) ?: emptyArray()
            if (children.isEmpty()) assets.open(asset).use { source -> destination.outputStream().use { source.copyTo(it) } }
            else { destination.mkdirs(); children.forEach { copy("$asset/$it", File(destination, it)) } }
        }
        try {
            copy("voice-model", stage)
            check(!stopped)
            check(File(stage, "am/final.mdl").isFile)
            if (target.exists()) check(target.deleteRecursively()) // Rebuildable model cache, never pairing/history.
            check(stage.renameTo(target))
        } finally { if (stage.exists()) stage.deleteRecursively() }
        return target
      }
    }
    private fun speak(text: String) {
        if (stopped) return
        state("speaking", "正在回答，暂停识别以避免自唤醒。")
        SpeechOutput.speak(this, text, "voice")
    }
    private fun listen() {
        var model: Model? = null; var recognizer: Recognizer? = null
        try {
            LibVosk.setLogLevel(LogLevel.WARNINGS)
            val directory = prepareModel()
            model = Model(directory.absolutePath); recognizer = Recognizer(model, 16000f)
            check(!stopped)
            chat!!.begin()
            check(!stopped)
            VoiceState.session = chat!!.session
            handler.postDelayed(heartbeat, 40_000)
            val min = AudioRecord.getMinBufferSize(16000, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
            check(min > 0)
            val mic = AudioRecord(MediaRecorder.AudioSource.VOICE_RECOGNITION, 16000,
                AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, maxOf(min, 8192))
            recorder = mic
            check(mic.state == AudioRecord.STATE_INITIALIZED)
            val gate = WakeGate(); val buffer = ShortArray(3200)
            mic.startRecording(); check(mic.recordingState == AudioRecord.RECORDSTATE_RECORDING)
            VoiceState.micRecording = true
            state("listening", "正在本地监听“夏鱼洛”，前台/后台均由此服务运行。")
            while (!stopped) {
                if (SpeechOutput.active) {
                    mic.stop(); VoiceState.micRecording = false
                    recognizer.reset()
                    state("speaking", "正在朗读文字回复，暂停麦克风识别。")
                    while (!stopped && SpeechOutput.active) Thread.sleep(25)
                    if (stopped) break
                    Thread.sleep(300)
                    mic.startRecording(); check(mic.recordingState == AudioRecord.RECORDSTATE_RECORDING)
                    VoiceState.micRecording = true
                    state("listening", "朗读结束，恢复本地唤醒监听。")
                    continue
                }
                check(!(getSystemService(AUDIO_SERVICE) as AudioManager).isMicrophoneMute) { "系统已关闭麦克风。" }
                check((getSystemService(AUDIO_SERVICE) as AudioManager).mode == AudioManager.MODE_NORMAL) { "麦克风正被通话使用。" }
                val count = mic.read(buffer, 0, buffer.size)
                check(count > 0) { "麦克风被中断。" }
                if (!recognizer.acceptWaveForm(buffer, count)) continue
                val (action, prompt) = gate.feed(JSONObject(recognizer.result).optString("text"))
                if (action == "ignore") continue
                mic.stop(); VoiceState.micRecording = false
                if (action == "stop") { handler.post { stopSelf() }; break }
                val reply = when (action) {
                    "sleep" -> "好的，等你再次呼唤夏鱼洛。"
                    "wake" -> "我在，请说。"
                    else -> {
                        state("thinking", "已唤醒，正在进行 Codex 只读问答；编程/工具请用对应入口并遵循电脑设置。")
                        val text = chat!!.ask(prompt); VoiceState.session = chat!!.session; text
                    }
                }
                speak(reply)
                recognizer.reset()
                if (action != "sleep") gate.resume()
                if (!stopped) {
                    Thread.sleep(300) // Avoid the tail of our own audio.
                    mic.startRecording(); check(mic.recordingState == AudioRecord.RECORDSTATE_RECORDING)
                    VoiceState.micRecording = true
                    state("listening", "正在本地监听；唤醒后 30 秒内可接着说。")
                }
            }
        } catch (_: Exception) { if (!stopped) handler.post { fail() } }
        finally {
            VoiceState.micRecording = false
            try { recorder?.release() } catch (_: Exception) {}
            recorder = null; recognizer?.close(); model?.close()
            if (stopped) try { chat?.revoke() } catch (_: Exception) {}
        }
    }
    private fun fail() {
        VoiceState.phase = "error"
        VoiceState.message = "语音已停止：请检查离线模型、麦克风权限/占用、系统中文朗读、配对和电脑在线状态，再手动开启。"
        stopSelf()
    }
    override fun onTaskRemoved(rootIntent: Intent?) { stopSelf(); super.onTaskRemoved(rootIntent) }
    override fun onDestroy() {
        stopped = true; VoiceState.running = false; VoiceState.connection = null
        handler.removeCallbacks(renewPower); handler.removeCallbacks(heartbeat)
        chat?.cancel(); SpeechOutput.stop("voice")
        Thread({ try { chat?.revoke() } catch (_: Exception) { /* Lease expires within 150s without heartbeat. */ } }, "xiayuluo-voice-revoke").start()
        try { recorder?.stop() } catch (_: Exception) {}
        worker?.interrupt()
        VoiceState.micRecording = false
        if (power?.isHeld == true) power?.release()
        if (VoiceState.phase != "error") { VoiceState.phase = "off"; VoiceState.message = "语音已停止，没有自动重启。" }
        stopForeground(STOP_FOREGROUND_REMOVE)
        super.onDestroy()
    }
}
