import test from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { PassThrough } from 'node:stream';
import { join } from 'node:path';
import { runProcess, checkArgv } from '../src/runtime/process.js';
import { DockerRuntime, imageReference } from '../src/runtime/docker.js';
import { Sessions } from '../src/runtime/sessions.js';
import { fixture, IMAGE } from './helpers.js';
import { EvidenceStore, readEvents } from '../src/evidence/store.js';
test('controller process bounds output and enforces deadline', async () => {
  const overflow = await runProcess('python3', ['-c', 'import sys; sys.stdout.write("x"*100000)'], {
    limit: 4096
  });
  assert.equal(overflow.truncated, true);
  assert.equal(overflow.stdout.length, 4096);
  const deadline = await runProcess('/bin/sleep', ['1'], {
    timeout: 100
  });
  assert.equal(deadline.timedOut, true);
});
test('argv and images reject mutable or injected values', () => {
  for (const image of ['ubuntu:latest', '--privileged', 'sha256:short']) assert.throws(() => imageReference(image));
  for (const argv of [[], ['x\0'], [1], ['x'.repeat(4097)]]) assert.throws(() => checkArgv(argv));
  assert.equal(imageReference(IMAGE), IMAGE);
});
test('Docker uses isolated mounts, immutable image and non-root user; cancellation kills inside container', async t => {
  const {
      workspace
    } = await fixture(t),
    calls = [];
  const runtime = new DockerRuntime({
    input: workspace.input,
    image: IMAGE,
    runId: 'fixture',
    runner: async (executable, args) => {
      calls.push(args);
      return {
        code: 0,
        stdout: Buffer.from('ready'),
        stderr: Buffer.alloc(0),
        cancelled: args.at(-1) === JSON.stringify(['trigger-cancel'])
      };
    }
  });
  await runtime.start();
  await runtime.execute(['trigger-cancel']);
  await runtime.close();
  await runtime.close();
  const create = calls.find(args => args[0] === 'create');
  for (const value of ['--network=none', '--read-only', '--cap-drop=ALL', '--security-opt=no-new-privileges:true', '--pull=never']) assert.ok(create.includes(value));
  assert.ok(create.some(x => x.includes('target=/challenge,readonly')));
  assert.equal(calls.filter(args => args[0] === 'rm').length, 1);
  assert.ok(calls.some(args => args.includes('-c') && args.some(x => x.includes('os.killpg') && !x.includes('subprocess'))));
});
test('uncertain Docker create still gets a cleanup attempt', async t => {
  const {
      workspace
    } = await fixture(t),
    calls = [];
  const runtime = new DockerRuntime({
    input: workspace.input,
    image: IMAGE,
    runId: 'fixture',
    runner: async (_cmd, args) => {
      calls.push(args);
      return {
        code: args[0] === 'create' ? null : 0,
        timedOut: args[0] === 'create',
        stdout: Buffer.alloc(0),
        stderr: Buffer.alloc(0)
      };
    }
  });
  await assert.rejects(runtime.start(), /create failed/);
  await runtime.close();
  assert.ok(calls.some(x => x[0] === 'rm'));
});
test('interactive session drains output, records stdin, and returns observed exit state', async t => {
  const {
      root
    } = await fixture(t),
    evidence = await EvidenceStore.create(join(root, 'sessions'));
  const child = new EventEmitter();
  Object.assign(child, {
    stdin: new PassThrough(),
    stdout: new PassThrough(),
    stderr: new PassThrough(),
    kill: () => child.emit('close', 137)
  });
  const runtime = {
    execArgs: () => [],
    killSession: async () => child.emit('close', 137)
  };
  const sessions = new Sessions(runtime, evidence, {
    spawnProcess: () => child
  });
  const started = await sessions.start(['cat']);
  child.stdout.write('hello');
  await sessions.send(started.session_id, Buffer.from('input'));
  const read = await sessions.read(started.session_id);
  assert.equal(read.output, 'hello');
  assert.equal(read.state, 'running');
  child.emit('close', 0);
  assert.equal((await sessions.read(started.session_id)).state, 'exited');
  await sessions.closeAll();
  const events = await readEvents(evidence.root);
  assert.ok(events.some(x => x.direction === 'stdin'));
});
test('concurrent intentional session closure terminates once and preserves observed exit code', async t => {
  const {
    root
  } = await fixture(t);
  const evidence = await EvidenceStore.create(join(root, 'sessions'));
  const child = new EventEmitter();
  Object.assign(child, {
    stdin: new PassThrough(),
    stdout: new PassThrough(),
    stderr: new PassThrough(),
    kill: () => child.emit('close', 137)
  });
  let kills = 0;
  const sessions = new Sessions({
    execArgs: () => [],
    killSession: async () => {
      kills++;
      child.emit('close', 137);
    }
  }, evidence, {
    spawnProcess: () => child
  });
  const item = await sessions.start(['cat']);
  const results = await Promise.all([sessions.close(item.session_id), sessions.close(item.session_id)]);
  assert.equal(kills, 1);
  assert.ok(results.every(result => result.state === 'closed' && result.exit_code === 137));
});

test('artifact export supports declared multi-megabyte limit and rejects malformed base64', async t => {
  const data = await fixture(t);
  const bytes = Buffer.alloc(2 * 1024 * 1024, 0x61);
  const runtime = new DockerRuntime({ input: data.workspace.input, image: IMAGE, runId: 'fixture' });
  runtime.execute = async () => ({ code: 0, stdout: Buffer.from(bytes.toString('base64')), stderr: Buffer.alloc(0) });
  assert.deepEqual(await runtime.export('/work/large', undefined, { maxBytes: bytes.length }), bytes);
  runtime.execute = async () => ({ code: 0, stdout: Buffer.from('a==='), stderr: Buffer.alloc(0) });
  await assert.rejects(runtime.export('/work/large'), /base64/);
});
