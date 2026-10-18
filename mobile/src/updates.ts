import { Platform } from 'react-native';
import { fetch } from 'expo/fetch';
import * as Application from 'expo-application';
import * as FS from 'expo-file-system/legacy';
import { File } from 'expo-file-system';
import * as IntentLauncher from 'expo-intent-launcher';
import config from './update-config.json';
import appConfig from '../app.json';
import { newer, verifyRelease, type Release } from './updateProtocol';

export const currentVersion = Application.nativeApplicationVersion || appConfig.expo.version;
const currentCode = Number(Application.nativeBuildVersion || appConfig.expo.android.versionCode);
let downloading = false;
export async function checkUpdate(): Promise<{ release: Release; available: boolean }> {
  const abort = new AbortController(), timer = setTimeout(() => abort.abort(), 20000);
  try {
    // This request is independent of the paired computer, and never carries its token.
    const response = await fetch(config.manifest_url, { signal: abort.signal, credentials: 'omit' });
    if (response.status === 404) throw new Error('更新源尚未发布或仓库需要登录。可点击“下载更新”前往 GitHub。');
    if (!response.ok || !response.body) throw new Error('更新检查失败，请稍后重试。');
    const reader = response.body.getReader(), decoder = new TextDecoder();
    let text = '', size = 0;
    try {
      while (true) {
        const { done, value } = await reader.read(); if (done) break;
        size += value.byteLength;
        if (size > 32768) { await reader.cancel(); throw new Error('更新清单过大，已拒绝。'); }
        text += decoder.decode(value, { stream: true });
      }
      text += decoder.decode();
    } finally { reader.releaseLock(); }
    const release = verifyRelease(text, config);
    return { release, available: newer(release.version, currentVersion) && release.android_code > currentCode };
  } finally { clearTimeout(timer); }
}

export async function installUpdate(expected: Release, progress: (text: string) => void): Promise<void> {
  if (Platform.OS !== 'android') throw new Error('当前仅安卓安装包支持 App 内更新。');
  if (downloading) throw new Error('已有更新正在下载。');
  downloading = true;
  let file: File | null = null, handedOff = false;
  try {
    const { release, available } = await checkUpdate();
    if (!available || release.version !== expected.version || release.assets['android-arm64'].sha256 !== expected.assets['android-arm64'].sha256) throw new Error('发布版本已变化，请重新检查后确认。');
    const asset = release.assets['android-arm64'];
    // One dedicated cache file; never touch SecureStore or conversation storage.
    const uri = `${FS.cacheDirectory}xiayuluo-update.apk`;
    await FS.deleteAsync(uri, { idempotent: true }); file = new File(uri);
    let tooLarge = false;
    const download = FS.createDownloadResumable(asset.url, uri, {}, event => {
      if (event.totalBytesWritten > asset.size || event.totalBytesExpectedToWrite > asset.size) {
        tooLarge = true; void download.cancelAsync();
      }
      progress(`正在下载 ${Math.min(100, Math.floor(event.totalBytesWritten * 100 / asset.size))}%`);
    });
    const timer = setTimeout(() => { void download.cancelAsync(); }, 10 * 60 * 1000);
    let result;
    try { result = await download.downloadAsync(); } finally { clearTimeout(timer); }
    if (tooLarge || !result || result.status !== 200 || file.size !== asset.size) throw new Error('更新下载不完整或大小不符，请重试。');
    progress('正在校验安装包…');
    if (await file.digest('SHA-256') !== asset.sha256) throw new Error('安装包校验失败，已拒绝安装。');
    const content = await FS.getContentUriAsync(uri);
    await IntentLauncher.startActivityAsync('android.intent.action.VIEW', {
      data: content, type: 'application/vnd.android.package-archive', flags: 1,
    });
    handedOff = true;
    // Returning from this Intent does NOT prove installation succeeded.
    progress('已交给系统安装器。若被拦截，请允许夏鱼洛安装未知应用，再点击更新重试；不要卸载旧版。');
  } finally {
    if (file && !handedOff && file.exists) file.delete();
    downloading = false;
  }
}
