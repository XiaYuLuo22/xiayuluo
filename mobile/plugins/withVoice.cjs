const { withAndroidManifest, withMainApplication, withAppBuildGradle, withDangerousMod } = require('expo/config-plugins');
const fs = require('node:fs');
const path = require('node:path');

module.exports = function withVoice(config) {
  config = withAndroidManifest(config, mod => {
    const manifest = mod.modResults.manifest;
    // Android 11+ package visibility: discover only TTS services, not all apps.
    manifest.queries ||= [];
    if (!manifest.queries.some(query => query.intent?.some(intent => intent.action?.some(
      action => action.$?.['android:name'] === 'android.intent.action.TTS_SERVICE')))) {
      manifest.queries.push({ intent: [{ action: [{ $: { 'android:name': 'android.intent.action.TTS_SERVICE' } }] }] });
    }
    manifest['uses-permission'] ||= [];
    for (const name of ['RECORD_AUDIO', 'FOREGROUND_SERVICE', 'FOREGROUND_SERVICE_MICROPHONE', 'POST_NOTIFICATIONS', 'WAKE_LOCK']) {
      const permission = `android.permission.${name}`;
      if (!manifest['uses-permission'].some(item => item.$['android:name'] === permission))
        manifest['uses-permission'].push({ $: { 'android:name': permission } });
    }
    const app = manifest.application[0];
    app.service ||= [];
    if (!app.service.some(item => item.$['android:name'] === '.voice.VoiceService'))
      app.service.push({ $: { 'android:name': '.voice.VoiceService', 'android:exported': 'false',
        'android:foregroundServiceType': 'microphone', 'android:stopWithTask': 'true' } });
    return mod;
  });
  config = withMainApplication(config, mod => {
    if (!mod.modResults.contents.includes('add(com.xiayuluo.assistant.voice.VoicePackage())')) {
      const anchor = 'PackageList(this).packages.apply {';
      if (!mod.modResults.contents.includes(anchor)) throw new Error('Native package hook changed; do not ship without voice registration.');
      mod.modResults.contents = mod.modResults.contents.replace(anchor, `${anchor}\n          add(com.xiayuluo.assistant.voice.VoicePackage())`);
    }
    return mod;
  });
  config = withAppBuildGradle(config, mod => {
    if (!mod.modResults.contents.includes('com.alphacephei:vosk-android:0.3.75')) mod.modResults.contents += `
dependencies {
    implementation 'com.alphacephei:vosk-android:0.3.75@aar'
    implementation 'net.java.dev.jna:jna:5.18.1@aar'
}
`;
    return mod;
  });
  return withDangerousMod(config, ['android', async mod => {
    const project = mod.modRequest.projectRoot;
    const java = path.join(project, 'android/app/src/main/java/com/xiayuluo/assistant/voice');
    fs.mkdirSync(java, { recursive: true });
    fs.cpSync(path.join(project, 'native/voice'), java, { recursive: true });
    const proguard = path.join(project, 'android/app/proguard-rules.pro');
    const keep = '\n# Xiayuluo offline voice uses JNA/native symbol reflection.\n-keep class org.vosk.** { *; }\n-keep class com.sun.jna.** { *; }\n-keepclassmembers class * extends com.sun.jna.** { public *; }\n-dontwarn java.awt.**\n';
    if (!fs.readFileSync(proguard, 'utf8').includes('# Xiayuluo offline voice')) fs.appendFileSync(proguard, keep);
    const model = path.resolve(project, '../assistant/voice-model');
    if (fs.existsSync(path.join(model, 'am/final.mdl'))) {
      const assets = path.join(project, 'android/app/src/main/assets/voice-model');
      fs.mkdirSync(assets, { recursive: true });
      fs.cpSync(model, assets, { recursive: true });
    } else console.warn('Offline Chinese voice model missing: voice must report unavailable; run tools/prepare_voice_model.py before distribution.');
    return mod;
  }]);
};
