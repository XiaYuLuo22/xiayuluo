import { fetch } from 'expo/fetch';
import * as SecureStore from 'expo-secure-store';
import { Platform } from 'react-native';
import { endpoint, SSEDecoder, type Packet } from './protocol';

export type Connection = { url: string; token: string; name: string; id: string };
export type Session = { id: string; title: string };
export type Message = { id: number; role: string; content: string };
export type Memory = { id: string; tier: string; content: string; expires_at: string | null };
export type Workspace = { id: string | null; name: string; configured: boolean; available: boolean;
  sandbox: 'read-only' | 'workspace-write'; approvals: 'desktop-only' };
export type Action = { id: string; title: string; session_id: string; created_at: string;
  tool: string; arguments: { prompt?: string }; workspace?: Workspace };
const storageKey = 'xiayuluo.connection.v1';

export async function loadConnection(): Promise<Connection | null> {
  if (Platform.OS === 'web') return null; // Layout preview only; never put device tokens in browser storage.
  const raw = await SecureStore.getItemAsync(storageKey);
  if (!raw) return null;
  try {
    const connection = JSON.parse(raw) as Connection;
    if (typeof connection.token !== 'string' || connection.token.length < 20 || typeof connection.name !== 'string' ||
        typeof connection.id !== 'string') return null;
    return { ...connection, url: endpoint(connection.url) };
  } catch { return null; }
}
export async function saveConnection(connection: Connection | null) {
  if (!connection) return SecureStore.deleteItemAsync(storageKey);
  await SecureStore.setItemAsync(storageKey, JSON.stringify(connection), {
    keychainAccessible: SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY,
  });
}

export class Client {
  constructor(readonly connection: Connection) {}
  async request(path: string, init: RequestInit = {}, publicPair = false) {
    const url = endpoint(this.connection.url) + '/mobile/' + (publicPair ? 'pair' : 'v1/' + path);
    const response = await fetch(url, { ...init, redirect: 'error', headers: {
      'Content-Type': 'application/json',
      ...(publicPair ? {} : { Authorization: `Bearer ${this.connection.token}` }),
    } });
    if (!response.ok) {
      let detail = `同步请求失败（${response.status}）`;
      try { const error = await response.json(); if (typeof error.detail === 'string') detail = error.detail; } catch {}
      throw new Error(detail);
    }
    return response;
  }
  async json<T>(path: string, body?: unknown, publicPair = false): Promise<T> {
    const controller = new AbortController(); const timer = setTimeout(() => controller.abort(), 15000);
    try {
      return await (await this.request(path, { signal: controller.signal, method: body === undefined ? 'GET' : 'POST',
        ...(body === undefined ? {} : { body: JSON.stringify(body) }) }, publicPair)).json() as T;
    } finally { clearTimeout(timer); }
  }
  async stream(body: unknown, signal: AbortSignal, onPacket: (packet: Packet) => void) {
    const response = await this.request('chat', { method: 'POST', body: JSON.stringify(body), signal });
    const reader = response.body?.getReader();
    if (!reader) throw new Error('当前设备不支持流式响应。');
    const decoder = new TextDecoder(); const parser = new SSEDecoder(); let done = false;
    try {
      while (true) {
        const result = await reader.read();
        for (const packet of parser.feed(result.done ? decoder.decode() : decoder.decode(result.value, { stream: true }))) {
          if (packet.type === 'error') throw new Error(packet.message || '本轮回复失败。');
          if (packet.type === 'done') done = true;
          onPacket(packet);
        }
        if (result.done) break;
      }
      if (!done) throw new Error('连接中断，回复未完成。任务不会自动重发。');
    } finally { await reader.cancel().catch(() => {}); }
  }
}

export async function pairDevice(url: string, code: string, name: string): Promise<Connection> {
  if (Platform.OS === 'web') throw new Error('浏览器仅用于界面预览，请使用 Android 或 iOS App 配对。');
  const client = new Client({ url: endpoint(url), token: '', name, id: '' });
  const result = await client.json<{ token: string; device: { id: string; name: string } }>('', { code, name }, true);
  return { url: endpoint(url), token: result.token, name: result.device.name, id: result.device.id };
}
