import assert from 'node:assert/strict';
import { test } from 'node:test';
import { downloadPage } from '../src/downloadLinks.ts';

test('private download buttons use fixed HTTPS GitHub pages without tokens', () => {
  assert.equal(downloadPage('XiaYuLuo22/xiayuluo', 'windows'), 'https://github.com/XiaYuLuo22/xiayuluo/actions/workflows/windows-app.yml');
  assert.equal(downloadPage('XiaYuLuo22/xiayuluo', 'android'), 'https://github.com/XiaYuLuo22/xiayuluo/releases');
  for (const repository of ['../other', 'owner/..', 'owner/.', 'https://evil.example/repo',
    'owner/repo/path', 'owner/repo?token=secret', 'owner/repo#fragment', 'owner/repo\\path']) {
    assert.throws(() => downloadPage(repository, 'android'));
  }
});
