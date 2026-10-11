import test from 'node:test';
import assert from 'node:assert/strict';
import { authenticationErrorMessage } from '../src/pi/auth-errors.js';
test('authentication diagnostics distinguish installation, callback, storage and network failures', () => {
  for (const [error, expected] of [[new Error('Sign in with ChatGPT requires a device ID (UUID) for this installation'), /设备 ID/], [new Error('Port 1455 is in use, probably by an unfinished login'), /1455/], [Object.assign(new Error('denied'), {
    code: 'EACCES'
  }), /权限不足/], [new Error('OAuth state mismatch'), /不匹配/], [new Error('Paste the full callback URL from the browser'), /URL 不完整/], [new Error('fetch failed', {
    cause: Object.assign(new Error('dns'), {
      code: 'ENOTFOUND'
    })
  }), /网络连接失败/], [Object.assign(new Error('Cannot persist settings'), {
    code: 'CTFBOT_AUTH_SETTINGS'
  }), /安装设置/]]) assert.match(authenticationErrorMessage(error), expected);
});
test('diagnostics never disclose token responses, callback URLs or synchronization credentials', () => {
  for (const message of ['OpenAI OAuth token request failed (401): {"access_token":"secret-value"}', 'unknown provider error https://127.0.0.1:1455/auth/callback?code=secret-value', 'ChatGPT authorization failed: secret-value']) {
    const error = new Error(message);
    error.credential = {
      access: 'secret-value'
    };
    const result = authenticationErrorMessage(error);
    assert.ok(!result.includes('secret-value'));
    assert.ok(!result.includes('http://'));
  }
  const error = Object.assign(new Error('sync'), {
    name: 'CredentialSynchronizationError',
    credential: {
      access: 'secret-value'
    },
    cause: new Error('fetch failed')
  });
  assert.match(authenticationErrorMessage(error), /认证已完成/);
  assert.ok(!authenticationErrorMessage(error).includes('secret-value'));
});
