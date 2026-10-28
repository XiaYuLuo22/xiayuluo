import { Platform } from 'react-native';
import { fetch } from 'expo/fetch';
import * as Application from 'expo-application';
import * as FS from 'expo-file-system/legacy';
import { File, FileMode } from 'expo-file-system';
import * as IntentLauncher from 'expo-intent-launcher';
import config from './update-config.json';
import appConfig from '../app.json';
import { fetchUpdateResource, manifestUrl, newer, verifyRelease, type Asset, type Release } from './updateProtocol';
import { AndroidUpdateInstaller, type UpdateState } from './updateInstaller';

export const currentVersion = Application.nativeApplicationVersion || appConfig.expo.version;
const currentCode = Number(Application.nativeBuildVersion || appConfig.expo.android.versionCode);
export async function checkUpdate(signal?: AbortSignal): Promise<{ release: Release; available: boolean }> {
  const abort = new AbortController(), timer = setTimeout(() => abort.abort(), 20000);
  const cancel = () => abort.abort();
  signal?.addEventListener('abort', cancel, { once: true });
  if (signal?.aborted) abort.abort();
  try {
    // This request is independent of the paired computer, and never carries its token.
    const response = await fetchUpdateResource(manifestUrl(config), abort.signal, fetch);
    if (response.status === 404) throw new Error('GitHub Releases 尚未发布可验证版本或当前不可访问，无法下载更新。请待 GitHub 签名版本发布后重试；当前版本保留，不会跳转网页。');
    if (!response.ok || !response.body) throw new Error('GitHub 更新检查失败，请稍后重试；当前版本保留。');
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
  } catch (error) {
    if (abort.signal.aborted) throw new Error(signal?.aborted ? 'GitHub 更新检查已取消。' : 'GitHub 更新检查超时，请稍后重试。');
    throw error;
  } finally { clearTimeout(timer); signal?.removeEventListener('abort', cancel); }
}

async function downloadPackage(asset: Asset, signal: AbortSignal, progress: (received: number) => void): Promise<File> {
  if (!FS.cacheDirectory) throw new Error('安卓更新缓存不可用；未更改当前应用或个人数据。');
  const uri = `${FS.cacheDirectory}xiayuluo-update.apk`;
  await FS.deleteAsync(uri, { idempotent: true });
  const file = new File(uri);
  const abort = new AbortController(), cancel = () => abort.abort();
  signal.addEventListener('abort', cancel, { once: true });
  if (signal.aborted) abort.abort();
  let timedOut = false, finished = false;
  const timer = setTimeout(() => { timedOut = true; abort.abort(); }, 10 * 60 * 1000);
  try {
    const response = await fetchUpdateResource(asset.url, abort.signal, fetch);
    const declared = response.headers.get('content-length');
    if (response.status !== 200 || !response.body || (declared !== null && (!/^\d+$/.test(declared) || Number(declared) !== asset.size))) {
      await response.body?.cancel();
      throw new Error('安装包下载响应无效或大小不符，请稍后重试。');
    }
    file.create();
    const handle = file.open(FileMode.WriteOnly), reader = response.body.getReader();
    let received = 0;
    try {
      while (true) {
        if (abort.signal.aborted) throw new Error('更新下载已停止。');
        const result = await reader.read();
        if (result.done) break;
        received += result.value.byteLength;
        if (received > asset.size) throw new Error('安装包下载大小超限，已停止。');
        await handle.writeBytes(result.value);
        progress(received);
      }
      if (received !== asset.size) throw new Error('安装包下载中断或不完整，请重试。');
    } finally { await reader.cancel().catch(() => {}); handle.close(); }
    finished = true;
    return file;
  } catch (error) {
    if (timedOut) throw new Error('安装包下载超时，当前版本保留，请稍后重试。');
    throw error;
  } finally {
    clearTimeout(timer); signal.removeEventListener('abort', cancel);
    if (!finished && file.exists) file.delete();
  }
}

const installer = new AndroidUpdateInstaller<File>({
  check: checkUpdate,
  download: downloadPackage,
  verify: async (file, asset) => file.exists && file.size === asset.size && (await file.digest('SHA-256')).toLowerCase() === asset.sha256,
  launch: async file => {
    const content = await FS.getContentUriAsync(file.uri);
    await IntentLauncher.startActivityAsync('android.intent.action.VIEW', {
      data: content, type: 'application/vnd.android.package-archive', flags: 1,
    });
  },
  discard: async file => { if (file.exists) file.delete(); },
});

function requireAndroidApp() {
  if (Platform.OS !== 'android') throw new Error('当前仅安卓安装包支持 App 内更新。');
  if (Application.applicationId !== appConfig.expo.android.package) throw new Error('请使用夏鱼洛正式安卓 App；预览环境不能安装更新。');
}
export const getUpdateState = (): UpdateState => installer.status();
export const subscribeUpdateState = (listener: (state: UpdateState) => void) => installer.subscribe(listener);
export const cancelUpdate = () => installer.cancel();
export async function installUpdate(expected: Release, progress: (text: string) => void): Promise<void> {
  requireAndroidApp();
  const unsubscribe = installer.subscribe(state => progress(state.message));
  try { await installer.install(expected); } finally { unsubscribe(); }
}
export async function reopenInstaller(): Promise<void> { requireAndroidApp(); await installer.reopen(); }
export async function openInstallPermissionSettings(): Promise<void> {
  requireAndroidApp();
  try {
    await IntentLauncher.startActivityAsync('android.settings.MANAGE_UNKNOWN_APP_SOURCES', {
      data: `package:${appConfig.expo.android.package}`,
    });
  } catch { throw new Error('无法打开安装权限设置，请在安卓设置中允许夏鱼洛安装未知应用，然后再次打开安装器。'); }
}
