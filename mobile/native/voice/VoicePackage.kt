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

class VoiceModule(private val context: ReactApplicationContext) : ReactContextBaseJavaModule(context) {
    override fun getName() = "XiayuluoVoice"
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
