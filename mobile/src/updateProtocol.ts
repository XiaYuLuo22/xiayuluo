import { ed25519 } from '@noble/curves/ed25519.js';
import { hexToBytes, utf8ToBytes } from '@noble/hashes/utils.js';

export type UpdateConfig = { repository: string; manifest_url: string; public_key: string };
export type Asset = { url: string; size: number; sha256: string };
export type Release = { schema: number; application: string; version: string; android_code: number;
  notes: string; assets: Record<'windows-x64' | 'android-arm64', Asset> };
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
