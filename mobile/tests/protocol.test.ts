import assert from 'node:assert/strict';
import { test } from 'node:test';
import { endpoint, SSEDecoder } from '../src/protocol.ts';

test('only explicitly configured HTTPS origins may receive pairing credentials', () => {
  assert.equal(endpoint('https://phone.example/mobile/'), 'https://phone.example');
  for (const url of ['http://phone.example', 'https://user:pass@phone.example',
    'https://phone.example/path', 'https://phone.example?key=secret', 'file:///data', 'https://phone.example/#secret']) {
    assert.throws(() => endpoint(url));
  }
});
test('SSE handles fragmented CRLF, UTF-8 text, and multiple events', () => {
  const parser = new SSEDecoder();
  assert.deepEqual(parser.feed(': heartbeat\r\n\r\ndata: {"type":"text","text":"你'), []);
  assert.deepEqual(parser.feed('好"}\r'), []);
  assert.deepEqual(parser.feed('\n\r\ndata: {"type":"done"}\n\n'), [
    { type: 'text', text: '你好' }, { type: 'done' },
  ]);
});
test('malformed and oversized events fail without silently reporting success', () => {
  assert.throws(() => new SSEDecoder().feed('data: {bad}\n\n'));
  assert.throws(() => new SSEDecoder().feed('data: []\n\n'));
  assert.throws(() => new SSEDecoder().feed('a'.repeat(1048577)));
});
