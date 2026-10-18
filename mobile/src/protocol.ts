export function endpoint(raw: string): string {
  const value = new URL(raw.trim());
  if (value.protocol !== 'https:' || value.username || value.password || value.search || value.hash ||
      !['', '/', '/mobile', '/mobile/'].includes(value.pathname)) {
    throw new Error('请输入你自己的 HTTPS 同步服务地址，例如 https://assistant.example.com');
  }
  return value.origin;
}

export type Packet = { type: string; text?: string; message?: string; action?: { title: string } };

export class SSEDecoder {
  private buffer = '';
  feed(chunk: string): Packet[] {
    this.buffer = (this.buffer + chunk).replace(/\r\n/g, '\n');
    if (this.buffer.length > 1048576) throw new Error('响应过大，已停止接收。');
    const packets: Packet[] = [];
    let boundary: number;
    while ((boundary = this.buffer.indexOf('\n\n')) !== -1) {
      const block = this.buffer.slice(0, boundary);
      this.buffer = this.buffer.slice(boundary + 2);
      const data = block.split('\n').filter(line => line.startsWith('data:'))
        .map(line => line.slice(5).trimStart()).join('\n');
      if (!data) continue;
      const packet: unknown = JSON.parse(data);
      if (!packet || typeof packet !== 'object' || !('type' in packet) || typeof packet.type !== 'string') {
        throw new Error('同步服务返回了无效响应。');
      }
      packets.push(packet as Packet);
    }
    return packets;
  }
}
