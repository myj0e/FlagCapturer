import test from 'node:test';
import assert from 'node:assert/strict';
import { join, basename } from 'node:path';
import { writeFile } from 'node:fs/promises';
import { fixture } from '../helpers.js';
import { EvidenceStore, audit, readEvents } from '../../src/evidence/store.js';
import { RunState } from '../../src/evidence/state.js';
import { ToolRegistry } from '../../src/tools/registry.js';
import { DockerRuntime, resolveImage } from '../../src/runtime/docker.js';
import { ToolProvisioner } from '../../src/provisioning/service.js';
import { installCore } from '../../src/tools/core.js';
import { installProvisioning } from '../../src/tools/provisioning.js';
import { Sessions } from '../../src/runtime/sessions.js';
import { snapshot } from '../../src/challenge/workspace.js';
import { replay } from '../../src/application/report.js';

// Final acceptance only: one locked 5.6 KiB wheel, current local image, no image pulls/builds.
test('tool supply preserves main sessions, installs a tiny locked extension and replays from verified cache offline', { timeout: 180000 }, async t => {
  const f = await fixture(t, { content: 'Base58 acceptance\n' });
  const evidence = await EvidenceStore.create(join(f.root, 'runs'));
  const workspace = await snapshot(f.workspace.root, join(evidence.root, 'challenge'));
  const image = await resolveImage(process.env.CTFBOT_DOCKER_TEST_IMAGE ?? 'ctfbot-tools:candidate');
  const state = new RunState(evidence, { run_id: basename(evidence.root), image });
  await evidence.append('run_started', { identity: state.identity });
  const runtime = new DockerRuntime({ input: workspace.input, image, runId: basename(evidence.root) });
  const sessions = new Sessions(runtime, evidence);
  const cacheRoot = join(f.root, 'tool-cache');
  const owned = [];
  const provisioner = new ToolProvisioner({ runtime, evidence, state, image, runId: basename(evidence.root), cacheRoot, onOwnership: async name => owned.push(name) });
  t.after(async () => { await provisioner.close(); await sessions.closeAll(); await runtime.close(); });
  await runtime.start();
  const registry = new ToolRegistry(state, evidence);
  await installCore(registry, { workspace, runtime, sessions });
  installProvisioning(registry, { provisioner, workspace });
  const call = async (name, args) => {
    const response = await registry.invoke(name, args);
    assert.equal(response.isError, undefined, JSON.stringify(response));
    return JSON.parse(response.content[0].text);
  };
  const wait = async request => {
    let status;
    do { status = await call('tool_status', { preparation_id: request.preparation_id, wait_seconds: 10 }); }
    while (!['ready', 'failed', 'cancelled'].includes(status.state));
    assert.equal(status.state, 'ready', JSON.stringify(status));
    return status;
  };
  const session = await sessions.start(['python3', '-u', '-c', 'import sys; print("alive",flush=True); print(sys.stdin.readline().strip(),flush=True)']);
  await runtime.write('/work/keep', Buffer.from('main workspace retained'));
  const ocr = await wait(await call('tool_request', { capabilities: ['ocr'] }));
  assert.equal(ocr.delivery, 'current-image');
  const extension = await wait(await call('tool_request', { capabilities: ['base58'] }));
  assert.equal(extension.delivery, 'readonly-python-overlay');
  const mainImport = await runtime.execute(['python3', '-c', 'import base58']);
  assert.notEqual(mainImport.code, 0, 'main interpreter must remain unchanged');
  const script = await call('script_save', { language: 'python', source: 'from pathlib import Path\nimport base58,socket,os\nx=Path("/work/inputs/input.txt").read_bytes()\nPath("/work/result.txt").write_bytes(base58.b58decode(base58.b58encode(x)))\nassert not Path("/work/keep").exists()\ntry:\n Path("/opt/ctf-extension/site/writable").write_text("bad")\n raise AssertionError("overlay is writable")\nexcept OSError: pass\nprint("extension composition works")\n' });
  const execution = await call('tool_exec', { environment_id: extension.environment_id, argv: ['python3', '/work/tool-script.py'], script_id: script.id,
    inputs: [{ asset_id: 'assets-1', name: 'input.txt' }], outputs: [{ path: '/work/result.txt', display_name: '还原文本' }] });
  assert.match(execution.stdout, /composition works/);
  assert.equal((await evidence.read(execution.output_assets[0].artifact.artifact)).toString(), 'Base58 acceptance\n');
  await sessions.send(session.session_id, Buffer.from('still alive\n'));
  const output = await sessions.read(session.session_id, { collectSeconds: 1 });
  assert.match(output.output, /still alive/);
  assert.equal((await runtime.export('/work/keep')).toString(), 'main workspace retained');
  await runtime.write('/work/large', Buffer.alloc(2 * 1024 * 1024, 0x61));
  assert.equal((await runtime.export('/work/large', undefined, { maxBytes: 2 * 1024 * 1024 })).length, 2 * 1024 * 1024);
  const second = new ToolProvisioner({ runtime, evidence, state, image, runId: basename(evidence.root), cacheRoot, fetcher: async () => { throw new Error('cache must not download'); } });
  try {
    const request = await second.request(['base58']);
    await [...second.jobs.values()][0].promise;
    assert.equal((await second.status(request.preparation_id)).cache_hit, true);
  } finally { await second.close(); }
  const replayed = await replay(evidence.root, { runsRoot: join(f.root, 'replay'), image, toolCacheRoot: cacheRoot });
  assert.equal(replayed.ok, true);
  assert.ok(replayed.replayed.some(x => x.tool === 'tool_exec'));
  assert.equal((await audit(evidence.root)).ok, true);
  assert.equal((await audit(replayed.run_dir)).ok, true);
  const events = await readEvents(evidence.root);
  assert.equal(events.filter(x => x.type === 'tool_installation').length, 1);
  assert.ok(owned.length >= 3);
  const file = join(cacheRoot, extension.environment_id, 'site/base58/__init__.py');
  await writeFile(file, '# changed cache');
  await assert.rejects(provisioner.execute(extension.environment_id, ['true'], undefined, async () => {}), /integrity/);
});
