import test from 'node:test';
import assert from 'node:assert/strict';
import { join } from 'node:path';
import { writeFile } from 'node:fs/promises';
import { EventEmitter } from 'node:events';
import { fixture, tools } from './helpers.js';
import { loadGrant, NetworkBroker } from '../src/runtime/network.js';
import { installOptional } from '../src/tools/optional.js';
import { recover } from '../src/application/recovery.js';
import { sha256 } from '../src/util.js';
function grantFor(workspace) {
  return {
    schema_version: 1,
    kind: 'tcp',
    challenge_id: workspace.manifest.id,
    task_sha256: workspace.manifest.task_sha256,
    input_sha256: workspace.manifest.files.map(x => x.sha256),
    basis: 'Synthetic test scope',
    expires_at: new Date(Date.now() + 60000).toISOString(),
    ip: '192.0.2.1',
    port: 12345
  };
}
test('network grants bind task and attachment hashes and require unexpired literal IP scope', async t => {
  const {
      root,
      workspace
    } = await fixture(t),
    path = join(root, 'grant.json'),
    grant = grantFor(workspace);
  await writeFile(path, JSON.stringify(grant));
  assert.equal((await loadGrant(path, workspace)).ip, grant.ip);
  for (const change of [{
    ip: 'example.com'
  }, {
    task_sha256: 'b'.repeat(64)
  }, {
    input_sha256: ['b'.repeat(64)]
  }, {
    expires_at: '2020-01-01T00:00:00Z'
  }]) {
    await writeFile(path, JSON.stringify({
      ...grant,
      ...change
    }));
    await assert.rejects(loadGrant(path, workspace));
  }
});
test('broker rejects mismatched endpoint before connecting and limits exact response bytes', async t => {
  const {
    workspace,
    evidence
  } = await tools(t);
  let connections = 0;
  const connect = () => {
    connections++;
    const socket = new EventEmitter();
    socket.destroy = () => {};
    socket.end = () => queueMicrotask(() => {
      socket.emit('data', Buffer.alloc(20000, 65));
      socket.emit('end');
    });
    queueMicrotask(() => socket.emit('connect'));
    return socket;
  };
  const broker = new NetworkBroker(grantFor(workspace), {
    evidence,
    connect
  });
  await assert.rejects(broker.exchange({
    host: '192.0.2.2',
    port: 12345,
    protocol: 'tcp',
    data_base64: ''
  }), /outside/);
  assert.equal(connections, 0);
  const result = await broker.exchange({
    host: '192.0.2.1',
    port: 12345,
    protocol: 'tcp',
    data_base64: 'aGk='
  });
  assert.equal(result.coverage.truncated, true);
  assert.equal(Buffer.from(result.data_base64, 'base64').length, 16384);
  assert.equal((await evidence.read(result.request.artifact)).toString(), 'hi');
  await broker.close();
});
test('broker enforces grant expiry and rejects calls after closure', async t => {
  const {
      workspace,
      evidence
    } = await tools(t),
    grant = grantFor(workspace);
  const broker = new NetworkBroker(grant, {
    evidence,
    now: () => Date.parse(grant.expires_at) + 1
  });
  await assert.rejects(broker.exchange({
    host: grant.ip,
    port: grant.port,
    protocol: 'tcp',
    data_base64: ''
  }), /expired/);
  await broker.close();
  await assert.rejects(broker.exchange({
    host: grant.ip,
    port: grant.port,
    protocol: 'tcp',
    data_base64: ''
  }), /closed/);
});
test('memory exposes reviewed hash-verified entries only and refuses holdout contamination', async t => {
  const {
      root,
      registry,
      call
    } = await tools(t),
    path = join(root, 'memory.json');
  const entry = {
    id: 'reference-1',
    namespace: 'general',
    body: 'Synthetic technique',
    body_sha256: sha256('Synthetic technique'),
    source_url: 'https://example.com/authored',
    license: 'MIT',
    tags: ['synthetic'],
    contamination: 'general'
  };
  const memory = {
    schema_version: 1,
    reviewed: true,
    basis: 'Synthetic review',
    namespaces: ['general'],
    entries: [entry]
  };
  await writeFile(path, JSON.stringify(memory));
  await installOptional(registry, {
    memoryPath: path
  });
  const matches = await call('workflow_search', { query: 'synthetic technique' });
  assert.equal(matches.matches[0].id, 'memory/reference-1');
  assert.equal((await call('workflow_read', { id: matches.matches[0].id })).entry.body, entry.body);
  assert.equal((await call('memory_search', { query: 'synthetic' })).matches[0].id, entry.id);
  assert.equal((await call('memory_read', {
    id: 'reference-1'
  })).entry.body, 'Synthetic technique');
  await assert.rejects(call('memory_read', {
    id: 'outside'
  }), /Unknown/);
  const other = await tools(t);
  await writeFile(path, JSON.stringify({
    ...memory,
    entries: [{
      ...entry,
      contamination: 'holdout'
    }]
  }));
  await assert.rejects(installOptional(other.registry, {
    memoryPath: path
  }), /Holdout/);
});
test('recovery checks ownership and cannot delete another run container', async t => {
  const {
      root
    } = await fixture(t),
    runId = '11111111-1111-4111-8111-111111111111',
    container = 'ctfbot-22222222-2222-4222-8222-222222222222';
  await writeFile(join(root, 'recovery.json'), JSON.stringify({
    schema_version: 1,
    run_id: runId,
    container,
    pending: true
  }));
  const calls = [];
  await assert.rejects(recover(root, async (_cmd, args) => {
    calls.push(args);
    return {
      code: 0,
      stdout: Buffer.from(JSON.stringify({
        'flagcapturer.run': 'someone-else'
      }))
    };
  }), /ownership mismatch/);
  assert.equal(calls.length, 1);
  const result = await recover(root, async (_cmd, args) => ({
    code: 0,
    stdout: Buffer.from(args[0] === 'inspect' ? JSON.stringify({
      'flagcapturer.run': runId
    }) : '')
  }));
  assert.equal(result.recovered, true);
});
test('closing broker cancels an in-flight exchange and rejects new calls immediately', async t => {
  const {
    workspace,
    evidence
  } = await tools(t);
  let socket;
  const broker = new NetworkBroker(grantFor(workspace), {
    evidence,
    connect: () => {
      socket = new EventEmitter();
      socket.end = () => {};
      socket.destroy = () => {
        socket.destroyed = true;
      };
      return socket;
    }
  });
  const exchange = broker.exchange({
    host: '192.0.2.1',
    port: 12345,
    protocol: 'tcp',
    data_base64: ''
  });
  const cancelled = assert.rejects(exchange, /cancelled/);
  await broker.close();
  await cancelled;
  assert.equal(socket.destroyed, true);
  await assert.rejects(broker.exchange({
    host: '192.0.2.1',
    port: 12345,
    protocol: 'tcp',
    data_base64: ''
  }), /closed/);
});
test('broker expiry closes pending sockets without waiting for their peers', async t => {
  const {
    workspace,
    evidence
  } = await tools(t);
  const grant = {
    ...grantFor(workspace),
    expires_at: new Date(Date.now() + 100).toISOString()
  };
  let destroyed = false;
  const broker = new NetworkBroker(grant, {
    evidence,
    connect: () => {
      const socket = new EventEmitter();
      socket.end = () => {};
      socket.destroy = () => {
        destroyed = true;
      };
      return socket;
    }
  });
  t.after(() => broker.close());
  await broker.start();
  await assert.rejects(broker.exchange({
    host: grant.ip,
    port: grant.port,
    protocol: 'tcp',
    data_base64: ''
  }), /cancelled|expiry/);
  assert.equal(destroyed, true);
});
test('broker closure waits for an uncertain local-service start before removing the container', async t => {
  const {
    workspace,
    evidence
  } = await tools(t);
  let release,
    removed = 0,
    owned = false;
  const starting = new Promise(resolve => {
    release = resolve;
  });
  const broker = new NetworkBroker({
    ...grantFor(workspace),
    kind: 'local-service',
    ip: undefined,
    service: {
      image: 'sha256:' + 'a'.repeat(64),
      argv: ['python3', '-c', 'pass']
    }
  }, {
    evidence,
    onOwnership: async () => {
      owned = true;
    },
    runtimeFactory: () => ({
      name: 'ctfbot-fixture',
      start: () => starting,
      close: async () => {
        removed++;
      }
    })
  });
  const opening = broker.start();
  const interrupted = assert.rejects(opening, /abort|closed/i);
  const closing = broker.close();
  assert.equal(broker.closed, true);
  assert.equal(removed, 0);
  release();
  await interrupted;
  await closing;
  assert.equal(owned, true);
  assert.equal(removed, 1);
});
