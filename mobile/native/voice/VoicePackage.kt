package com.xiayuluo.assistant.voice

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.PowerManager
import android.provider.Settings
import androidx.lifecycle.Lifecycle
import com.facebook.react.ReactPackage
import com.facebook.react.bridge.*
import com.facebook.react.uimanager.ViewManager
import java.net.URI

class VoicePackage : ReactPackage {
    override fun createNativeModules(context: ReactApplicationContext): List<NativeModule> = listOf(VoiceModule(context))
    override fun createViewManagers(context: ReactApplicationContext): List<ViewManager<*, *>> = emptyList()
}

class VoiceModule(private val context: ReactApplicationContext) : ReactContextBaseJavaModule(context), LifecycleEventListener {
    init {
        SpeechOutput.initialize(context)
        context.addLifecycleEventListener(this)
        SpeechOutput.setForeground((context.currentActivity as? androidx.lifecycle.LifecycleOwner)
            ?.lifecycle?.currentState?.isAtLeast(Lifecycle.State.RESUMED) == true)
    }
    override fun getName() = "XiayuluoVoice"
    override fun onHostResume() { SpeechOutput.setForeground(true) }
    override fun onHostPause() { SpeechOutput.setForeground(false) }
    override fun onHostDestroy() { SpeechOutput.setForeground(false) }
    override fun invalidate() {
        context.removeLifecycleEventListener(this)
        SpeechOutput.setForeground(false)
        super.invalidate()
    }
    @ReactMethod fun speechStatus(promise: Promise) { promise.resolve(SpeechOutput.status().toString()) }
    @ReactMethod fun configureSpeech(enabled: Boolean, promise: Promise) {
        try {
            SpeechOutput.configure(enabled)
            promise.resolve(SpeechOutput.status().toString())
        } catch (_: Exception) { promise.reject("SPEECH_SETTING", "无法保存本机朗读开关，请检查系统存储。") }
    }
    @ReactMethod fun speechVoices(promise: Promise) {
        SpeechOutput.voices({ promise.resolve(it.toString()) },
            { promise.reject("SPEECH_VOICES", "系统音色尚未就绪，请稍后刷新本机已安装的中文离线音色。") })
    }
    @ReactMethod fun configureSpeechPreferences(voiceId: String?, rate: Double, volume: Double, promise: Promise) {
        SpeechOutput.configurePreferences(voiceId, rate, volume, { promise.resolve(it.toString()) },
            { promise.reject("SPEECH_PREFERENCES", "声音设置未保存：请在前台选择已安装的中文离线音色，并检查语速、音量与本机存储。") })
    }
    @ReactMethod fun stopSpeech(promise: Promise) {
        SpeechOutput.stop("chat")
        promise.resolve(null)
    }
    @ReactMethod fun speakReply(text: String, promise: Promise) {
        val generation = SpeechOutput.requestChatGeneration()
        if (text.isBlank() || text.length > 12000) {
            promise.reject("SPEECH_TEXT", "朗读内容为空或超过单次长度限制。")
            return
        }
        Thread({
            try { promise.resolve(SpeechOutput.speak(context, text, "chat", generation)) }
            catch (_: Exception) { promise.reject("SPEECH_OUTPUT", "回复朗读失败，请检查系统本地中文音色；文字回复不受影响。") }
        }, "xiayuluo-chat-speech").start()
    }
    @ReactMethod fun status(promise: Promise) {
        val ready = try { context.assets.open("voice-model/am/final.mdl").close(); true } catch (_: Exception) { false }
        val power = context.getSystemService(PowerManager::class.java)
        promise.resolve(VoiceState.snapshot(ready).put("battery_exempt", power.isIgnoringBatteryOptimizations(context.packageName)).toString())
    }
    @ReactMethod fun start(url: String, token: String, promise: Promise) {
        try {
            val activity = context.currentActivity as? androidx.lifecycle.LifecycleOwner
            require(activity?.lifecycle?.currentState?.isAtLeast(Lifecycle.State.RESUMED) == true) { "请先返回 App 前台，再开启麦克风。" }
            require(context.checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) { "请允许麦克风权限。" }
            if (Build.VERSION.SDK_INT >= 33) require(context.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED) { "请允许通知，以显示监听状态和停止按钮。" }
            val parsed = URI(url)
            require(parsed.scheme == "https" && !parsed.host.isNullOrBlank() && parsed.rawUserInfo == null &&
                parsed.rawQuery == null && parsed.rawFragment == null && parsed.rawPath in listOf("", "/") && token.length in 20..512 && !token.contains('\n') && !token.contains('\r')) { "配对地址或凭据无效。" }
            context.assets.open("voice-model/am/final.mdl").close()
            require(!VoiceState.running) { "语音已经开启。" }
            VoiceState.connection = VoiceConnection(url.trimEnd('/'), token)
            val intent = Intent(context, VoiceService::class.java)
            if (Build.VERSION.SDK_INT >= 26) context.startForegroundService(intent) else context.startService(intent)
            promise.resolve(null)
        } catch (_: Exception) {
            if (!VoiceState.running) VoiceState.connection = null
            promise.reject("VOICE_START", "无法开启：请确认 App 在前台、麦克风/通知权限已允许，且安装包包含离线中文模型。")
        }
    }
    @ReactMethod fun stop(promise: Promise) {
        VoiceState.connection = null
        context.stopService(Intent(context, VoiceService::class.java))
        promise.resolve(null)
    }
    @ReactMethod fun openPowerSettings(promise: Promise) {
        try {
            val activity = context.currentActivity ?: error("App must be foregrounded")
            activity.startActivity(Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS))
            promise.resolve(null)
        } catch (_: Exception) { promise.reject("POWER_SETTINGS", "请手动进入系统设置 → 应用 → 夏鱼洛 → 电池，允许后台运行/不限电量。") }
    }
}
