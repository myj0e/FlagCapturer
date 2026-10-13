import test from 'node:test';
import assert from 'node:assert/strict';
import { join } from 'node:path';
import { parseConnectionDescription, remoteConnectionPrompt } from '../src/challenge/connection.js';
import { publicUrl } from '../src/urls.js';
import { importRemote, snapshot } from '../src/challenge/workspace.js';
import { fixture } from './helpers.js';

test('nc, socat and SSH descriptions become endpoints without executing commands', () => {
  for (const value of ['nc pwnable.kr 10001', 'nc -nv -w 5 pwnable.kr 10001', 'pwnable.kr:10001']) {
    assert.deepEqual(parseConnectionDescription(value).targets, ['tcp://pwnable.kr:10001']);
  }
  const input = 'socat STDIO,raw,echo=0,opost,onlcr TCP:pwnable.kr:10001';
  const result = parseConnectionDescription(input);
  assert.deepEqual(result.targets, ['tcp://pwnable.kr:10001']);
  assert.deepEqual(result.connection.services[0].terminal_options, ['raw', 'echo=0', 'opost', 'onlcr']);
  assert.equal(result.connection.description, input);
  const ssh = parseConnectionDescription('ssh -tt -p 2222 player@challenge.test ./vuln');
  assert.deepEqual(ssh.targets, ['ssh://challenge.test:2222']);
  assert.deepEqual(ssh.connection.services[0].terminal_options, ['pty']);
  assert.match(ssh.connection.description, /player@challenge.test \.\/vuln/);
});

test('natural language preserves interaction requirements and supports multiple explicit services', () => {
  const input = '先访问 https://web.example.test/challenge/login，再通过 TCP 连接 pwn.example.test的10001端口，保持连接，先读菜单再提交 payload';
  const result = parseConnectionDescription(input);
  assert.deepEqual(result.targets, ['https://web.example.test/challenge/login', 'tcp://pwn.example.test:10001']);
  assert.match(result.connection.description, /保持连接，先读菜单/);
  assert.deepEqual(parseConnectionDescription('Connect to localhost on port 31337').targets, ['tcp://localhost:31337']);
  assert.deepEqual(parseConnectionDescription('先通过 SSH 登录 challenge.test，再运行题目程序').targets, ['ssh://challenge.test:22']);
  assert.deepEqual(parseConnectionDescription('nc -n [::1] 10001').targets, ['tcp://[::1]:10001']);
  assert.deepEqual(parseConnectionDescription('Use SSH to login.test:22 and TCP to pwn.test:10001').targets, ['ssh://login.test:22', 'tcp://pwn.test:10001']);
  const privateHop = parseConnectionDescription('先 SSH 登录 jump.test，再在远端运行 nc 127.0.0.1 10001');
  assert.deepEqual(privateHop.targets, []);
  assert.match(privateHop.connection.description, /127.0.0.1 10001/);
});

test('incomplete instructions and unsupported transports stay hints without guessed grants', () => {
  for (const input of ['先 SSH 登录题目服务器，再运行程序', 'nc -l 31337', 'nc host.test 1234; cat /private/key', 'socat EXEC:sh TCP:host.test:1234']) {
    const result = parseConnectionDescription(input);
    assert.deepEqual(result.targets, []);
    assert.ok(result.connection.warnings.length);
  }
  for (const input of ['nc -uv host.test 1234', 'ncat --ssl host.test 443', 'socat STDIO OPENSSL:host.test:443', 'udp://host.test:1234', 'openssl s_client -connect host.test:443']) {
    const result = parseConnectionDescription(input);
    assert.deepEqual(result.targets, []);
    assert.equal(result.connection.services[0].supported, false);
    assert.ok(result.connection.warnings.some(text => /尚无内置/.test(text)));
  }
  assert.throws(() => parseConnectionDescription('tcp://host.test'), /port/);
  assert.throws(() => parseConnectionDescription('nc host.test 65536'), /port|Invalid URL/);
  assert.throws(() => parseConnectionDescription('ssh://user:secret@host.test'), /credentials/);
  assert.throws(() => parseConnectionDescription(''), /description/);
});

test('generated tasks and hints redact URL values, preserve routes and survive snapshots', async t => {
  const { root } = await fixture(t);
  const url = 'https://host.test/challenge/login?token=SECRET&tag=a&tag=b#PRIVATE';
  assert.equal(new URL(publicUrl(url)).searchParams.getAll('tag').length, 2);
  const punctuation = 'https://host.test/path)?token=SECRET!';
  const exact = parseConnectionDescription(punctuation);
  assert.deepEqual(exact.targets, [punctuation]);
  assert.match(exact.connection.description, /\/path\)/);
  assert.doesNotMatch(exact.connection.description, /SECRET/);
  for (const task of [undefined, '获取 Flag']) {
    const workspace = await importRemote(url, join(root, 'imports'), { task, authorized: true, basis: 'fixture' });
    const copy = await snapshot(workspace.root, join(root, `snapshot-${task ? 'custom' : 'default'}`));
    assert.equal(copy.task, workspace.task);
    assert.deepEqual(copy.manifest.connection, workspace.manifest.connection);
    assert.deepEqual(workspace.manifest.targets, [url]); // Private original remains usable by the controller.
    const prompt = remoteConnectionPrompt(workspace.manifest, [url]);
    assert.doesNotMatch(workspace.task + prompt, /SECRET|PRIVATE/);
    assert.match(workspace.task + prompt, /\/challenge\/login/);
    assert.match(prompt, /not network authorization/);
    assert.equal(JSON.parse(prompt.split('\n')[3]).targets.length, 1);
  }
  const draft = await importRemote('先登录 SSH 服务器，再运行程序；地址稍后提供', join(root, 'imports'));
  assert.deepEqual(draft.manifest.targets, []);
  assert.match(draft.task, /地址稍后提供/);
});
