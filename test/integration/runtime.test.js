import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:net';
import { once } from 'node:events';
import { setTimeout as delay } from 'node:timers/promises';
import { join, basename } from 'node:path';
import { fixture } from '../helpers.js';
import { DockerRuntime, resolveImage } from '../../src/runtime/docker.js';
import { Sessions } from '../../src/runtime/sessions.js';
import { NetworkBroker } from '../../src/runtime/network.js';
import { EvidenceStore, audit } from '../../src/evidence/store.js';
import { RunState } from '../../src/evidence/state.js';
import { ToolRegistry } from '../../src/tools/registry.js';
import { installCore } from '../../src/tools/core.js';
import { recover } from '../../src/application/recovery.js';
import { runProcess } from '../../src/runtime/process.js';
const imageName = process.env.CTFBOT_DOCKER_TEST_IMAGE ?? 'ctfbot-tools:candidate';
async function environment(t, options = {}) {
  const data = await fixture(t, options);
  const evidence = await EvidenceStore.create(join(data.root, 'runs'));
  const image = await resolveImage(imageName);
  const runtime = new DockerRuntime({
    input: data.workspace.input,
    image,
    runId: basename(evidence.root)
  });
  const sessions = new Sessions(runtime, evidence);
  t.after(async () => {
    try {
      await sessions.closeAll();
    } finally {
      await runtime.close();
    }
  });
  await runtime.start();
  return {
    ...data,
    evidence,
    image,
    runtime,
    sessions
  };
}
test('real Docker enforces isolation, deadlines, output limits and byte-exact script transfer', async t => {
  const {
    runtime,
    workspace
  } = await environment(t);
  const result = await runtime.execute(['python3', '-I', '-c', `import os,pathlib,socket,json
assert os.getuid()!=0
assert pathlib.Path('/challenge/${workspace.manifest.files[0].path}').read_text()=='flag{synthetic_fixture}'
for path in ['/root/forbidden','/challenge/forbidden']:
 try: pathlib.Path(path).write_text('x'); raise AssertionError('write permitted')
 except (PermissionError,OSError): pass
assert sorted(socket.if_nameindex())==[(1,'lo')]
print('isolated')`]);
  assert.equal(result.code, 0, result.stderr.toString());
  assert.equal(result.stdout.toString().trim(), 'isolated');
  const configuration = await runProcess('docker', ['inspect', runtime.name]);
  const inspected = JSON.parse(configuration.stdout)[0];
  assert.equal(inspected.HostConfig.NetworkMode, 'none');
  assert.equal(inspected.HostConfig.ReadonlyRootfs, true);
  assert.deepEqual(inspected.HostConfig.CapDrop, ['ALL']);
  assert.equal(inspected.HostConfig.Privileged, false);
  assert.deepEqual(inspected.HostConfig.PortBindings, {});
  const timeout = await runtime.execute(['python3', '-u', '-c', "import time; print('partial'); time.sleep(60)"], {
    timeout: 200
  });
  assert.equal(timeout.code, 124);
  assert.equal(timeout.timedOut, true);
  assert.match(timeout.stdout.toString(), /partial/);
  const capped = await runtime.execute(['python3', '-c', "print('x'*20000)"], {
    limit: 1024
  });
  assert.equal(capped.code, 125);
  assert.equal(capped.truncated, true);
  const payload = Buffer.from('line\n'.repeat(13000));
  await runtime.write('/work/exact.txt', payload);
  assert.deepEqual(await runtime.export('/work/exact.txt'), payload);
  assert.equal((await runtime.execute(['python3', '-c', 'print("still ready")'])).code, 0);
});
test('real sandbox tools run JavaScript workflows, Python scripts, image rendering and interactive sessions', async t => {
  const {
    runtime,
    sessions,
    evidence,
    workspace
  } = await environment(t, {
    content: 'ZmxhZ3tzeW50aGV0aWNfZml4dHVyZX0='
  });
  const state = new RunState(evidence, {
    run_id: basename(evidence.root)
  });
  const registry = new ToolRegistry(state, evidence);
  await installCore(registry, {
    runtime,
    sessions,
    workspace,
    supportsImages: true
  });
  const call = async (name, args) => {
    const result = await registry.invoke(name, args);
    assert.ok(!result.isError, result.content[0].text);
    return JSON.parse(result.content[0].text);
  };
  const workflow = await call('workflow_run', {
    pack: 'crypto',
    operation: 'decode',
    path: workspace.manifest.files[0].path
  });
  const decoded = JSON.parse(workflow.stdout).candidates.find(x => x.encoding === 'base64');
  assert.equal(Buffer.from(decoded.data_base64, 'base64').toString(), 'flag{synthetic_fixture}');
  const script = await call('script_save', {
    language: 'python',
    source: "from PIL import Image\nImage.new('RGB',(2,2),(255,0,0)).save('/work/pixel.png')\nprint('image saved')\n"
  });
  assert.equal((await call('script_run', {
    script_id: script.id
  })).exit_code, 0);
  const rendered = await registry.invoke('image_view', {
    path: '/work/pixel.png'
  });
  assert.ok(!rendered.isError, rendered.content[0].text);
  assert.equal(rendered.content.find(x => x.type === 'image').mimeType, 'image/png');
  const session = await call('session_start', {
    argv: ['python3', '-u', '-c', 'import sys; print("ready",flush=True); print("echo:"+sys.stdin.readline().strip(),flush=True)']
  });
  const first = await call('session_read', {
    session_id: session.session_id,
    collect_seconds: 1
  });
  assert.match(first.output, /ready/);
  await call('session_send', {
    session_id: session.session_id,
    data_base64: Buffer.from('synthetic\n').toString('base64')
  });
  const second = await call('session_read', {
    session_id: session.session_id,
    collect_seconds: 1
  });
  assert.match(second.output, /echo:synthetic/);
  assert.equal(second.state, 'exited');
  const stopped = await call('session_start', {
    argv: ['python3', '-c', 'import time; time.sleep(60)']
  });
  assert.equal((await call('session_close', {
    session_id: stopped.session_id
  })).state, 'closed');
  await sessions.closeAll();
  assert.equal((await audit(evidence.root)).ok, true);
});
test('real TCP broker bounds replies and denies endpoints outside its grant', async t => {
  const {
    root
  } = await fixture(t);
  const evidence = await EvidenceStore.create(join(root, 'runs'));
  const server = createServer({
    allowHalfOpen: true
  }, socket => {
    socket.on('error', () => {});
    socket.resume();
    socket.on('end', () => socket.end(Buffer.alloc(20000, 65)));
  });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  t.after(() => new Promise(resolve => server.close(resolve)));
  const grant = {
    kind: 'tcp',
    ip: '127.0.0.1',
    port: server.address().port,
    expires_at: new Date(Date.now() + 30000).toISOString()
  };
  const broker = new NetworkBroker(grant, {
    evidence
  });
  t.after(() => broker.close());
  await broker.start();
  const request = {
    host: grant.ip,
    port: grant.port,
    protocol: 'tcp',
    data_base64: ''
  };
  await assert.rejects(broker.exchange({
    ...request,
    host: '127.0.0.2'
  }), /outside grant/);
  const reply = await broker.exchange(request);
  assert.equal(Buffer.from(reply.data_base64, 'base64').length, 16384);
  assert.equal(reply.coverage.truncated, true);
  assert.equal((await audit(evidence.root)).ok, true);
});
test('real local-service broker uses container loopback and recovery removes only owned containers', async t => {
  const {
    runtime,
    evidence,
    image
  } = await environment(t);
  const server = `import socket,pathlib
s=socket.socket(); s.bind(('127.0.0.1',23456)); s.listen(); pathlib.Path('/work/ready').write_text('ready')
while True:
 c,a=s.accept(); data=b''
 while True:
  part=c.recv(4096)
  if not part: break
  data+=part
 c.sendall(b'echo:'+data); c.close()
`;
  const broker = new NetworkBroker({
    kind: 'local-service',
    port: 23456,
    expires_at: new Date(Date.now() + 30000).toISOString(),
    service: {
      image,
      argv: ['python3', '-u', '-c', server]
    }
  }, {
    evidence
  });
  t.after(() => broker.close());
  await broker.start();
  let ready = false;
  for (let n = 0; n < 20; n++) {
    const probe = await broker.runtime.execute(['python3', '-c', "import pathlib; exit(0 if pathlib.Path('/work/ready').exists() else 1)"]);
    if (probe.code === 0) {
      ready = true;
      break;
    }
    await delay(50);
  }
  assert.ok(ready);
  const reply = await broker.exchange({
    host: 'challenge',
    port: 23456,
    protocol: 'tcp',
    data_base64: Buffer.from('fixture').toString('base64')
  });
  assert.equal(Buffer.from(reply.data_base64, 'base64').toString(), 'echo:fixture');
  await broker.close();
  const serviceRemoved = await runProcess('docker', ['inspect', broker.runtime.name]);
  assert.notEqual(serviceRemoved.code, 0);
  await evidence.document('recovery.json', {
    schema_version: 1,
    run_id: basename(evidence.root),
    container: runtime.name,
    pending: true
  });
  const recovered = await recover(evidence.root);
  assert.equal(recovered.recovered, true);
  const removed = await runProcess('docker', ['inspect', runtime.name]);
  assert.notEqual(removed.code, 0);
  assert.equal((await recover(evidence.root)).already_closed, true);
});
test('immediate session_close cancels a supervisor that is still starting', async t => {
  const {
    runtime,
    sessions
  } = await environment(t);
  const session = await sessions.start(['python3', '-c', "import time,pathlib; time.sleep(0.5); pathlib.Path('/work/escaped-stop').write_text('late')"]);
  await sessions.close(session.session_id);
  await delay(700);
  const result = await runtime.execute(['python3', '-c', "import pathlib; assert not pathlib.Path('/work/escaped-stop').exists(); print('stopped')"]);
  assert.equal(result.code, 0, result.stderr.toString());
});
test('real TCP disconnects reject the exchange and broker closure interrupts a waiting peer', async t => {
  const {
    root
  } = await fixture(t);
  const evidence = await EvidenceStore.create(join(root, 'runs'));
  const sockets = new Set();
  const server = createServer({
    allowHalfOpen: true
  }, socket => {
    sockets.add(socket);
    socket.on('error', () => {});
    socket.on('close', () => sockets.delete(socket));
    socket.on('data', bytes => {
      if (bytes.toString() === 'disconnect') socket.resetAndDestroy();
    });
  });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  t.after(() => {
    for (const socket of sockets) socket.destroy();
    return new Promise(resolve => server.close(resolve));
  });
  const grant = {
    kind: 'tcp',
    ip: '127.0.0.1',
    port: server.address().port,
    expires_at: new Date(Date.now() + 30000).toISOString()
  };
  const broker = new NetworkBroker(grant, {
    evidence
  });
  t.after(() => broker.close());
  await broker.start();
  const request = {
    host: grant.ip,
    port: grant.port,
    protocol: 'tcp',
    data_base64: Buffer.from('disconnect').toString('base64')
  };
  await assert.rejects(broker.exchange(request), /reset|closed/i);
  const connected = once(server, 'connection');
  const waiting = broker.exchange({
    ...request,
    data_base64: Buffer.from('wait').toString('base64')
  });
  const cancelled = assert.rejects(waiting, /cancelled/);
  await connected;
  await broker.close();
  await cancelled;
});
test('local-service grant expiry stops its actual service container automatically', async t => {
  const {
    runtime,
    evidence,
    image
  } = await environment(t);
  const broker = new NetworkBroker({
    kind: 'local-service',
    port: 23457,
    expires_at: new Date(Date.now() + 5000).toISOString(),
    service: {
      image,
      argv: ['python3', '-u', '-c', 'import time; time.sleep(60)']
    }
  }, {
    evidence
  });
  t.after(() => broker.close());
  await broker.start();
  for (let n = 0; n < 120 && !broker.closed; n++) await delay(50);
  assert.equal(broker.closed, true);
  await broker.close();
  assert.notEqual((await runProcess('docker', ['inspect', broker.runtime.name])).code, 0);
  assert.equal((await runtime.execute(['python3', '-c', 'print("solver remains available")'])).code, 0);
});

test('workflow chains verified assets, extracts bounded ZIP members, and reconstructs explicitly ordered fragments', async t => {
  const encoded = Buffer.from(Buffer.from('flag{asset_chain}').toString('base64')).toString('hex');
  const { runtime, sessions, evidence, workspace, image } = await environment(t, { content: encoded });
  const state = new RunState(evidence, { run_id: basename(evidence.root), image });
  const registry = new ToolRegistry(state, evidence);
  await installCore(registry, { runtime, sessions, workspace });
  const call = async (name, args, error = false) => {
    const reply = await registry.invoke(name, args);
    assert.equal(reply.isError === true, error, reply.content[0].text);
    return JSON.parse(reply.content[0].text);
  };
  const first = await call('workflow_run', { pack: 'crypto', operation: 'decode', asset_id: 'assets-1', parameters: { encoding: 'hex' } });
  assert.equal(first.output_assets.length, 1);
  const second = await call('workflow_run', { pack: 'crypto', operation: 'decode', asset_id: first.output_assets[0].id, parameters: { encoding: 'base64' } });
  const flag = second.output_assets[0];
  assert.deepEqual(flag.parents, [first.output_assets[0].id]);
  assert.equal((await call('asset_read', { asset_id: flag.id, offset: 0, length: 100, encoding: 'utf8' })).data, 'flag{asset_chain}');
  const again = await call('workflow_run', { pack: 'crypto', operation: 'decode', asset_id: first.output_assets[0].id, parameters: { encoding: 'base64' } });
  assert.deepEqual(again.repeated_observation_ids, [second.observation_id]);
  assert.ok(again.repeat_notice);
  const candidate = await call('candidate_submit', { candidate: 'flag{asset_chain}' });
  assert.equal((await call('candidate_check', { candidate_id: candidate.id, operation: 'bytes_equals', asset_id: flag.id })).passed, true);
  // Alter the mutable source after capture; the next operation must use saved bytes.
  assert.equal((await runtime.execute(['python3', '-I', '-c', 'import pathlib,sys; pathlib.Path(sys.argv[1]).write_bytes(b"changed")', flag.captured_path])).code, 0);
  const left = await call('workflow_run', { pack: 'forensics', operation: 'slice', asset_id: flag.id, parameters: { offset: 0, length: 5 } });
  const right = await call('workflow_run', { pack: 'forensics', operation: 'slice', asset_id: flag.id, parameters: { offset: 5, length: 12 } });
  const joined = await call('workflow_run', { pack: 'forensics', operation: 'reassemble', asset_ids: [right.output_assets[0].id, left.output_assets[0].id], parameters: { positions: [1, 0], expected_parts: 2 } });
  assert.equal(joined.output_assets[0].sha256, flag.sha256);
  const missing = await call('workflow_run', { pack: 'forensics', operation: 'reassemble', asset_ids: [left.output_assets[0].id], parameters: { positions: [0], expected_parts: 2 } }, true);
  assert.equal(missing.status, 'failed');
  const unsupported = await call('workflow_run', { pack: 'stego', operation: 'png', asset_id: flag.id }, true);
  assert.equal(unsupported.status, 'unsupported');
  const env = await call('workflow_environment', { method_id: 'method/rsa-parameters' });
  assert.ok(env.capabilities.binaries.python3.path);
  assert.equal(typeof env.capabilities.modules.sympy.importable, 'boolean');
  // Archive filenames, including traversal text, must never control output paths.
  assert.equal((await runtime.execute(['python3', '-I', '-c', `import zipfile
with zipfile.ZipFile('/work/bundle.zip','w',compression=zipfile.ZIP_DEFLATED) as z:
 z.writestr('../../escape.txt', b'A'*300000)
 z.writestr('second.txt', b'second')`])).code, 0);
  const zip = await call('asset_capture', { path: '/work/bundle.zip', parent_asset_ids: ['assets-1'], transformation: 'authored synthetic ZIP fixture' });
  const extracted = await call('workflow_run', { pack: 'forensics', operation: 'extract', asset_id: zip.id, parameters: { entry_index: 0 } });
  assert.equal(extracted.output_assets[0].bytes, 300000);
  assert.equal(extracted.result.entry.crc_valid, true);
  assert.match(extracted.output_assets[0].captured_path, /^\/work\/\.workflow-output\/[a-f0-9-]+\/0$/);
  assert.equal((await call('asset_read', { asset_id: extracted.output_assets[0].id, offset: 299990, length: 10, encoding: 'utf8' })).data, 'AAAAAAAAAA');
  assert.equal((await runtime.execute(['python3', '-I', '-c', `import zipfile
with zipfile.ZipFile('/work/many.zip','w') as z:
 for i in range(1100): z.writestr(str(i)+'x'*300,b'x')`])).code, 0);
  const many = await call('asset_capture', { path: '/work/many.zip', parent_asset_ids: ['assets-1'], transformation: 'authored long-directory fixture' });
  const listed = await call('workflow_run', { pack: 'forensics', operation: 'archive', asset_id: many.id });
  assert.equal(listed.status, 'partial');
  assert.equal(listed.result_view_truncated, true);
  assert.equal(listed.asset_ids[0], many.id);
  assert.ok(listed.result.entries.length <= 10);
  assert.equal((await audit(evidence.root)).ok, true);
});
