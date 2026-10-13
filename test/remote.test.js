import test from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { join } from 'node:path';
import { writeFile } from 'node:fs/promises';
import { tools } from './helpers.js';
import { NetworkAuthority, targetSpec } from '../src/runtime/authority.js';
import { importRemote, preview } from '../src/challenge/workspace.js';
import { loadGrant } from '../src/runtime/network.js';
import { memoryFixture } from './context-fixture.js';
import { validateMemory } from '../src/context/format.js';
import { ContextMemory } from '../src/context/memory.js';
import { restoreExecutionState } from '../src/application/resume.js';
import { ToolActivity, toolCardText } from '../src/ui/activity.js';
import { installRemote } from '../src/tools/remote.js';

test('remote-only workspace is admitted without attachments; target hints do not grant network scope', async t => {
  const { root, evidence, state } = await tools(t);
  const workspace = await importRemote('https://example.test/challenge', join(root, 'imports'), { authorized: true, basis: 'fixture' });
  assert.equal((await preview(workspace.root, { requireAuthorization: true })).manifest.mode, 'remote');
  assert.deepEqual(workspace.manifest.files, []);
  const authority = new NetworkAuthority({ evidence, state });
  assert.deepEqual(authority.status(), []);
  assert.throws(() => authority.find('example.test', 443, 'http'), /scope_blocked/);
  await assert.rejects(importRemote('ssh://user:secret@example.test', root), /credentials/);
});

test('authority approval fixes resolved addresses and records exact endpoint scope', async t => {
  const { evidence, state } = await tools(t); let seen;
  const authority = new NetworkAuthority({ evidence, state, resolve: async () => [{ address: '192.0.2.1' }], approve: async request => { seen = request; return true; } });
  const granted = await authority.grant(targetSpec('https://example.test'));
  assert.deepEqual(seen.target.ips, ['192.0.2.1']); assert.equal(granted.port, 443);
  assert.equal(authority.find('example.test', 443, 'http').id, granted.id);
  assert.throws(() => authority.find('example.test', 80, 'http'), /scope_blocked/);
  assert.throws(() => authority.check(granted.id, 'tcp'), /permit/);
  const record = [...state.sections.network_targets.values()][0];
  assert.equal(record.target_id, granted.id); assert.notEqual(record.id, granted.id); await authority.close();
});

test('missing UI never silently approves; expired and revoked grants cannot connect', async t => {
  const { evidence } = await tools(t);
  const authority = new NetworkAuthority({ evidence });
  const spec = targetSpec('tcp://192.0.2.1:31337');
  assert.equal((await authority.grant(spec)).state, 'approval_required');
  assert.equal(authority.status().length, 0);
  await assert.rejects(authority.grant({ ...spec, expires_at: '2000-01-01' }, { authorized: true }), /expired/);
  const result = await authority.grant(spec, { authorized: true });
  await authority.revoke(result.id); assert.throws(() => authority.check(result.id, 'tcp'), /revoked/);
  await authority.close();
});

test('revocation destroys pending sockets before recording the change', async t => {
  const { evidence } = await tools(t); let socket, dialed;
  const authority = new NetworkAuthority({ evidence, connect: options => {
    dialed = options; socket = new EventEmitter(); socket.destroy = error => { queueMicrotask(() => { socket.emit('error', error); socket.emit('close'); }); }; return socket;
  } });
  const target = await authority.grant(targetSpec('tcp://192.0.2.1:31337'), { authorized: true });
  const pending = authority.dial(target.id, 'tcp'); const rejected = assert.rejects(pending, /revoked/);
  await authority.revoke(target.id); await rejected;
  assert.equal(dialed.host, '192.0.2.1'); await authority.close();
});

test('v2 grant binds remote task and optional input hashes without changing v1 compatibility', async t => {
  const { root } = await tools(t);
  const workspace = await importRemote('tcp://192.0.2.1:1234', join(root, 'remote'), { authorized: true, basis: 'fixture' });
  const grant = { schema_version: 2, challenge_id: workspace.manifest.id, task_sha256: workspace.manifest.task_sha256, input_sha256: [], basis: 'fixture', expires_at: new Date(Date.now() + 60000).toISOString(), targets: [targetSpec('tcp://192.0.2.1:1234')] };
  const path = join(root, 'grant.json'); await writeFile(path, JSON.stringify(grant));
  assert.equal((await loadGrant(path, workspace)).targets[0].expires_at, grant.expires_at);
  grant.task_sha256 = 'a'.repeat(64); await writeFile(path, JSON.stringify(grant)); await assert.rejects(loadGrant(path, workspace), /bound/);
});

test('remote responses become saved root assets and materialize through the existing asset API', async t => {
  const { registry, state } = await tools(t); const bytes = Buffer.from([0, 255, 10]);
  const asset = await registry.assets.addRemote(bytes, { target_id: 'fixture', display_name: '下载的 ELF' });
  assert.equal(asset.origin, 'remote'); assert.deepEqual(asset.parents, []);
  assert.deepEqual((await registry.assets.read(asset.id)).bytes, bytes);
  const result = await registry.assets.materialize(asset.id); assert.match(result.sandbox_path, /^\/work\/.assets\//);
  assert.equal(state.sections.asset_locations.size, 1);
});

test('periodic summaries validate evidence-backed recovery steps and resume actively reconstructs them', async t => {
  const { registry, state, evidence, runtime } = await tools(t);
  const observation = await state.add('observations', { tool: 'remote_read', result: { generation: 'old', preview: 'menu ready' } });
  const step = { goal: '恢复菜单', prerequisites: ['新连接已建立'], action: '运行保存的菜单脚本', effect: 'connect', expected_observation: 'menu ready', volatile_state: ['ASLR leak', 'menu position'], evidence_ids: [observation.id], asset_ids: ['assets-1'], script_ids: [], fallback: '重读菜单并调整协议' };
  const value = memoryFixture({ recovery_steps: [step] });
  validateMemory(value, state);
  assert.throws(() => validateMemory(memoryFixture({ recovery_steps: [{ ...step, evidence_ids: ['assets-1'] }] }), state), /observation/);
  const memory = new ContextMemory({ state, evidence, model: { contextWindow: 128000 }, complete: async () => ({ content: [{ type: 'text', text: JSON.stringify(value) }], stopReason: 'stop' }) });
  const draft = await memory.summarize({ kind: 'refresh', messages: [{ role: 'user', content: '继续' }] }); await memory.commit(draft);
  const report = await restoreExecutionState(state, evidence, runtime);
  assert.equal(report.recovery_guide.steps[0].expected_observation, 'menu ready');
  assert.match(report.recovery_guide.policy, /Actively reconstruct/);
  assert.equal(report.recovery_guide.historical_only, true);
  assert.ok(draft.recovery_plan.goal_asset_ids.includes('assets-1'));
});

test('Pi progress is forwarded through registry and updates the existing tool card', async t => {
  const { registry } = await tools(t); const events = [];
  registry.define('fixture_progress', 'progress fixture', { type: 'object', properties: {}, additionalProperties: false }, async (_, signal, update) => { update({ content: [{ type: 'text', text: '正在连接' }] }); return { ready: true }; });
  await registry.invoke('fixture_progress', {}, undefined, update => events.push(update), 'call');
  const activity = new ToolActivity(); activity.start({ toolName: 'remote_open', toolCallId: 'call' });
  const card = activity.update({ toolCallId: 'call', partialResult: events[0] });
  assert.match(toolCardText(card), /正在连接/);
});

test('new remote tool declarations stay fixed while target scope changes', async t => {
  const { registry } = await tools(t);
  const authority = { status: () => [], denials: [] };
  installRemote(registry, { authority });
  const before = JSON.stringify([...registry.definitions.values()].map(({ name, description, parameters }) => ({ name, description, parameters })));
  authority.status = () => [{ id: 'new-target' }];
  assert.match((await registry.invoke('network_status', {})).content[0].text, /new-target/);
  assert.equal(before, JSON.stringify([...registry.definitions.values()].map(({ name, description, parameters }) => ({ name, description, parameters }))));
});
