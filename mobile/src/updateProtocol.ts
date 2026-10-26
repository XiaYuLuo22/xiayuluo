import { ed25519 } from '@noble/curves/ed25519.js';
import { hexToBytes, utf8ToBytes } from '@noble/hashes/utils.js';

export type UpdateConfig = { repository: string; manifest_url: string; public_key: string };
export type Asset = { url: string; size: number; sha256: string };
export type Release = { schema: number; application: string; version: string; android_code: number;
  notes: string; assets: Record<'windows-x64' | 'android-arm64', Asset> };
export function trustedUpdateLocation(value: string): string {
  const url = new URL(value);
  if (url.protocol !== 'https:' || !['github.com', 'release-assets.githubusercontent.com', 'objects.githubusercontent.com'].includes(url.hostname)
    || url.port || url.username || url.password || url.hash) throw new Error('更新重定向不在受信任的 HTTPS 发布源内。');
  return url.toString();
}
export function manifestUrl(config: UpdateConfig): string {
  if (!/^[A-Za-z0-9][A-Za-z0-9-]{0,38}\/[A-Za-z0-9_.-]{1,100}$/.test(config.repository)
    || ['.', '..'].includes(config.repository.split('/')[1])) throw new Error('更新仓库配置无效。');
  const expected = `https://github.com/${config.repository}/releases/latest/download/update.json`;
  if (config.manifest_url !== expected) throw new Error('更新清单地址不是固定发布源。');
  return expected;
}

/** Follow only bounded, explicitly validated public redirects; never carry device credentials. */
export async function fetchUpdateResource(url: string, signal: AbortSignal,
  fetcher: (url: string, init: RequestInit) => Promise<Response>): Promise<Response> {
  let current = trustedUpdateLocation(url);
  for (let step = 0; step < 6; step++) {
    const response = await fetcher(current, { signal, redirect: 'manual', credentials: 'omit',
      headers: { Accept: 'application/octet-stream', 'Accept-Encoding': 'identity' } });
    if ([301, 302, 303, 307, 308].includes(response.status)) {
      const location = response.headers.get('location');
      await response.body?.cancel();
      if (!location) throw new Error('更新重定向缺少目标地址。');
      current = trustedUpdateLocation(new URL(location, current).toString());
      continue;
    }
    // Some native networking stacks may follow despite manual; still fail closed.
    if (response.url) trustedUpdateLocation(response.url);
    return response;
  }
  throw new Error('更新重定向次数过多，已停止。');
}
export function version(value: string): number[] {
  if (typeof value !== 'string' || !/^(0|[1-9][0-9]{0,4})\.(0|[1-9][0-9]{0,4})\.(0|[1-9][0-9]{0,4})$/.test(value)) throw new Error('更新版本号无效。');
  return value.split('.').map(Number);
}
export function newer(candidate: string, current: string): boolean {
  const left = version(candidate), right = version(current);
  for (let i = 0; i < 3; i++) if (left[i] !== right[i]) return left[i] > right[i];
  return false;
}
export function verifyRelease(raw: string, config: UpdateConfig): Release {
  try {
    if (utf8ToBytes(raw).length > 32768) throw new Error();
    const envelope = JSON.parse(raw);
    if (!/^[a-f0-9]{64}$/.test(config.public_key) || typeof envelope.payload !== 'string'
      || !/^[a-f0-9]{128}$/.test(envelope.signature)
      || !ed25519.verify(hexToBytes(envelope.signature), utf8ToBytes(envelope.payload), hexToBytes(config.public_key), { zip215: false })) throw new Error();
    const data: Release = JSON.parse(envelope.payload);
    version(data.version);
    if (data.schema !== 1 || data.application !== 'com.xiayuluo.assistant'
      || !Number.isInteger(data.android_code) || data.android_code < 1 || data.android_code > 2100000000
      || typeof data.notes !== 'string' || data.notes.length > 4000) throw new Error();
    for (const [platform, suffix] of [['windows-x64', '.zip'], ['android-arm64', '.apk']] as const) {
      const asset = data.assets[platform], url = new URL(asset.url);
      const prefix = `/${config.repository}/releases/download/v${data.version}/`;
      if (url.protocol !== 'https:' || url.hostname !== 'github.com' || url.port || url.username || url.password || url.search || url.hash
        || !url.pathname.startsWith(prefix) || !/^[A-Za-z0-9._-]+$/.test(url.pathname.slice(prefix.length)) || !url.pathname.endsWith(suffix)
        || !Number.isInteger(asset.size) || asset.size < 1 || asset.size > 512 * 1024 * 1024 || !/^[0-9a-f]{64}$/.test(asset.sha256)) throw new Error();
    }
    return data;
  } catch { throw new Error('更新清单签名或内容无效，已拒绝更新。'); }
}
