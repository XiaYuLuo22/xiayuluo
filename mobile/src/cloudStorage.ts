/** Private, bounded offline storage. Credentials belong in SecureStore, not snapshots. */
import { sha256 } from '@noble/hashes/sha2.js';
import { bytesToHex, utf8ToBytes } from '@noble/hashes/utils.js';
import type { CloudStateStore } from './cloudSync';

export class CloudStorageError extends Error {}

export type CloudConnection = { schema: 1; origin: string; deviceId: string; token: string };
export const MAX_CLOUD_STATE_BYTES = 2 * 1024 * 1024;
export const MAX_CLOUD_DIRECTORY_ITEMS = 1024;
const SECRET_FIELD = /^(?:token|device_token|lease_token|api_key|password|secret|private_key|openai_api_key|assistant_model_key|codex_api_key|__proto__|constructor|prototype)$/i;
const SNAPSHOT = /^state-([0-9]{12})-([A-Za-z0-9_-]{1,48})-([a-f0-9]{64})\.json$/;
const SAFE_ID = /^[A-Za-z0-9_-]{1,48}$/;

export function validateCloudConnection(value: unknown): CloudConnection {
  try {
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error();
    const item = value as Record<string, unknown>;
    if (Object.keys(item).sort().join(',') !== 'deviceId,origin,schema,token' || item.schema !== 1 ||
        typeof item.origin !== 'string' || item.origin.length > 1024 ||
        typeof item.deviceId !== 'string' || typeof item.token !== 'string' ||
        !/^[A-Za-z0-9_-]{1,128}$/.test(item.deviceId) || !/^[\x21-\x7e]{20,512}$/.test(item.token) ||
        utf8ToBytes(JSON.stringify(item)).length > 2048) throw new Error();
    const origin = new URL(item.origin.trim());
    if (origin.protocol !== 'https:' || !origin.hostname || origin.username || origin.password ||
        origin.search || origin.hash || !['', '/'].includes(origin.pathname)) throw new Error();
    return { schema: 1, origin: origin.origin, deviceId: item.deviceId, token: item.token };
  } catch {
    // Never include a rejected URL, credential, or original parser exception.
    throw new CloudStorageError('云连接配置无效；请使用独立 HTTPS 地址及设备专用凭据，不要使用管理员密钥。');
  }
}

export function scopeForCloud(connection: CloudConnection): string {
  const item = validateCloudConnection(connection);
  return bytesToHex(sha256(utf8ToBytes(`${item.origin}\0${item.deviceId}`))).slice(0, 32);
}

/** All names are relative to ONE private per-origin/device directory.
 * write/move must refuse an existing destination, rather than overwrite it.
 * read must bound native file size before allocating its contents.
 */
export interface CloudFileAdapter {
  list(): Promise<string[]>;
  read(name: string): Promise<string | null>;
  write(name: string, value: string): Promise<void>;
  move(from: string, to: string): Promise<void>;
  remove(name: string): Promise<void>;
}

type Snapshot = { name: string; generation: number; digest: string };
function digest(value: string): string { return bytesToHex(sha256(utf8ToBytes(value))); }
function fail(): never { throw new CloudStorageError('本机云同步档案损坏或超过上限；原文件保留，不会自动清空或回退为空。'); }

function validateState(value: string): void {
  if (typeof value !== 'string' || value.length > MAX_CLOUD_STATE_BYTES ||
      utf8ToBytes(value).length > MAX_CLOUD_STATE_BYTES) fail();
  let parsed: unknown;
  try { parsed = JSON.parse(value); } catch { fail(); }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) fail();
  const item = parsed as Record<string, unknown>;
  if (Object.keys(item).sort().join(',') !== 'cursor,outbox,records,schema,tasks' || item.schema !== 1 ||
      !Number.isSafeInteger(item.cursor) || Number(item.cursor) < 0 ||
      !Array.isArray(item.records) || item.records.length > 100000 ||
      !Array.isArray(item.outbox) || item.outbox.length > 10000 ||
      !Array.isArray(item.tasks) || item.tasks.length > 10000) fail();
  const visit = (entry: unknown, depth: number) => {
    if (depth > 64 || (typeof entry === 'number' && !Number.isFinite(entry))) fail();
    if (Array.isArray(entry)) { for (const child of entry) visit(child, depth + 1); }
    else if (entry && typeof entry === 'object') {
      for (const [key, child] of Object.entries(entry)) {
        if (SECRET_FIELD.test(key)) fail();
        visit(child, depth + 1);
      }
    }
  };
  visit(parsed, 0);
}

/** Publish new snapshots exclusively. Keep two generations only AFTER read-back.
 * A corrupt latest generation is an error, not permission to lose queued edits.
 * Unpublished temporary/unknown files are never promoted or deleted by recovery.
 */
export class DurableCloudStore implements CloudStateStore {
  private readonly files: CloudFileAdapter;
  private readonly makeId: () => string;
  private serial: Promise<unknown> = Promise.resolve();
  constructor(files: CloudFileAdapter, makeId: () => string = () =>
    `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`) {
    this.files = files; this.makeId = makeId;
  }
  private exclusive<T>(operation: () => Promise<T>): Promise<T> {
    const pending = this.serial.then(operation, operation);
    this.serial = pending.catch(() => {}); return pending;
  }
  private async inventory(): Promise<{ all: string[]; snapshots: Snapshot[] }> {
    const all = await this.files.list();
    if (!Array.isArray(all) || all.length > MAX_CLOUD_DIRECTORY_ITEMS ||
        all.some(name => typeof name !== 'string' || name.length > 255)) fail();
    const snapshots: Snapshot[] = [];
    for (const name of all) {
      const match = SNAPSHOT.exec(name);
      if (match) {
        const generation = Number(match[1]);
        if (generation < 1 || snapshots.some(item => item.generation === generation)) fail();
        snapshots.push({ name, generation, digest: match[3] });
      } else if (name.startsWith('state-')) {
        // A published-looking malformed filename must not erase the whole store.
        fail();
      }
    }
    snapshots.sort((a, b) => b.generation - a.generation);
    return { all, snapshots };
  }
  private async readSnapshot(snapshot: Snapshot): Promise<string> {
    const value = await this.files.read(snapshot.name);
    if (value === null) fail();
    validateState(value);
    if (digest(value) !== snapshot.digest) fail();
    return value;
  }
  load(): Promise<string | null> {
    return this.exclusive(async () => {
      const { snapshots } = await this.inventory();
      return snapshots.length ? this.readSnapshot(snapshots[0]) : null;
    });
  }
  save(value: string): Promise<void> {
    return this.exclusive(async () => {
      validateState(value);
      const { all, snapshots } = await this.inventory();
      if (snapshots.length && await this.readSnapshot(snapshots[0]) === value) return;
      if (all.length >= MAX_CLOUD_DIRECTORY_ITEMS) fail();
      const generation = (snapshots[0]?.generation || 0) + 1;
      if (generation > 999999999999) fail();
      const id = this.makeId();
      if (typeof id !== 'string' || !SAFE_ID.test(id)) fail();
      const finalName = `state-${String(generation).padStart(12, '0')}-${id}-${digest(value)}.json`;
      const temporary = `.cloud-state-${id}.tmp`;
      if (all.includes(finalName) || all.includes(temporary)) {
        throw new CloudStorageError('本机同步临时文件已存在；未覆盖任何文件，请稍后重试。');
      }
      let written = false, published = false;
      try {
        await this.files.write(temporary, value); written = true;
        if (await this.files.read(temporary) !== value) fail();
        await this.files.move(temporary, finalName); published = true;
        await this.readSnapshot({ name: finalName, generation, digest: digest(value) });
      } catch (error) {
        if (published) { try { await this.files.remove(finalName); } catch {} }
        throw error;
      } finally {
        if (written) { try { await this.files.remove(temporary); } catch {} }
      }
      // At most one old snapshot survives beside the new generation. Failure to
      // prune is non-destructive and never turns a successful save into a resend.
      for (const old of snapshots.slice(1)) { try { await this.files.remove(old.name); } catch {} }
    });
  }
}
