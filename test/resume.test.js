import test from 'node:test';
import assert from 'node:assert/strict';
import { join } from 'node:path';
import { readFile, writeFile } from 'node:fs/promises';
import { tools, fixture, IMAGE } from './helpers.js';
import { EvidenceStore, readEvents, audit } from '../src/evidence/store.js';
import { RunState } from '../src/evidence/state.js';
import { recoveryPlan, recoveryGoals } from '../src/evidence/provenance.js';
import { inspectResume, restoreExecutionState, listResumable } from '../src/application/resume.js';
import { solve } from '../src/application/solve.js';
import { snapshot } from '../src/challenge/workspace.js';
import { sha256 } from '../src/util.js';

test('commands automatically capture declared outputs with actual argv, saved script and dependency graph', async t => {
  const { call, state, evidence } = await tools(t);
  const script = await call('script_save', { language: 'python', source: 'print("authored")' });
  const execution = await call('script_run', { script_id: script.id, input_asset_ids: ['assets-1'], outputs: [{ path: '/work/one' }] });
  const first = execution.output_assets[0];
  assert.equal(first.generation.execution_id, execution.execution_id);
  assert.equal(first.generation.producer_binding, 'controller_observed');
  const record = state.select('executions', [execution.execution_id])[0];
  assert.deepEqual(record.argv, ['python3', script.path]);
  assert.equal(record.script_source.sha256, script.source_artifact.sha256);
  const request = JSON.parse((await evidence.read(record.request.artifact)).toString());
  assert.equal(request.name, 'script_run');
  const second = await call('command_run', { argv: ['python3', '-c', 'pass'], input_asset_ids: [first.id], outputs: [{ path: '/work/two' }] });
  const plan = recoveryPlan(state, [second.output_assets[0].id]);
  assert.deepEqual(plan.steps.map(step => step.asset_id), ['assets-1', first.id, second.output_assets[0].id]);
  assert.ok(plan.steps.every(step => !step.automatic_replay));
  assert.equal((await audit(evidence.root)).ok, true);
});

test('manual captures distinguish declared producer links from unknown producers and reject invented execution IDs', async t => {
  const { call } = await tools(t);
  const result = await call('command_run', { argv: ['true'] });
  const known = await call('asset_capture', { path: '/work/result', parent_asset_ids: ['assets-1'], transformation: 'reported', execution_id: result.execution_id });
  assert.equal(known.generation.producer_binding, 'model_declared');
  const unknown = await call('asset_capture', { path: '/work/other', parent_asset_ids: ['assets-1'], transformation: 'reported' });
  assert.equal(unknown.generation.producer_binding, 'unknown');
  await assert.rejects(call('asset_capture', { path: '/work/result', parent_asset_ids: ['assets-1'], transformation: 'reported', execution_id: 'executions-999' }), /Unknown executions/);
});

test('failed commands do not capture outputs; unbound outputs are rejected before execution', async t => {
  const { call, runtime, state } = await tools(t);
  let executions = 0;
  runtime.execute = async () => { executions++; return { code: 1, stdout: Buffer.alloc(0), stderr: Buffer.from('failure') }; };
  const result = await call('command_run', { argv: ['false'], input_asset_ids: ['assets-1'], outputs: [{ path: '/work/result' }] });
  assert.deepEqual(result.output_assets, []);
  assert.equal(state.select('executions', [result.execution_id])[0].succeeded, false);
  await assert.rejects(call('command_run', { argv: ['true'], outputs: [{ path: '/work/result' }] }), /input_asset_ids/);
  assert.equal(executions, 1);
});

test('resume restores saved bytes, scripts and historical materializations without running generation commands', async t => {
  const { state, evidence, call } = await tools(t);
  const script = await call('script_save', { language: 'python', source: 'print("saved")' });
  const first = await call('asset_capture', { path: '/work/result', parent_asset_ids: ['assets-1'], transformation: 'old' });
  const bytes = Buffer.from('new version');
  const second = await state.add('assets', { origin: 'derived', captured_path: '/work/result', parents: [first.id], artifact: await evidence.artifact(bytes), sha256: sha256(bytes), bytes: bytes.length });
  await state.add('asset_locations', { asset_id: first.id, path: '/work/.assets/previous-materialization' });
  await state.add('tool_environments', { preparation_id: 'preparation-1', environment_id: 'old-env', capabilities: ['python.base58'], state: 'ready' });
  const writes = new Map();
  const restored = await restoreExecutionState(state, evidence, { write: async (path, bytes) => { assert.ok(!writes.has(path)); writes.set(path, bytes); } });
  assert.equal(writes.get('/work/result').toString(), 'new version');
  assert.equal(writes.get(`/work/.assets/${first.id}`).toString(), 'checkpoint');
  assert.equal(writes.get('/work/.assets/previous-materialization').toString(), 'checkpoint');
  assert.equal(writes.get(script.path).toString(), 'print("saved")');
  assert.equal(restored.plan.steps.at(-1).asset_id, second.id);
  assert.match(restored.unresolved[0].reason, /newer/);
  assert.equal([...state.sections.tool_environments.values()].at(-1).state, 'interrupted');
  assert.equal((await audit(evidence.root)).ok, true);
});

test('recovery rejects cycles and unknown parents, and includes assets created after the last summary', async t => {
  const { state } = await tools(t);
  const first = await state.add('assets', { origin: 'derived', parents: ['assets-1'] });
  const irrelevant = await state.add('assets', { origin: 'derived', parents: ['assets-1'] });
  await state.add('context_snapshots', { asset_count: state.sections.assets.size, memory: { references: [{ asset_id: first.id }] }, recovery_plan: recoveryPlan(state, [first.id]) });
  const late = await state.add('assets', { origin: 'derived', parents: [first.id] });
  assert.ok(recoveryGoals(state).includes(late.id));
  assert.ok(!recoveryGoals(state).includes(irrelevant.id), 'Discarded historical outputs are not mistaken for post-summary work');
  const plan = recoveryPlan(state, [late.id]);
  assert.equal(plan.unresolved.length, 2);
  state.sections.assets.get(first.id).parents = [late.id];
  assert.throws(() => recoveryPlan(state, [late.id]), /Cyclic/);
  state.sections.assets.get(first.id).parents = ['assets-999'];
  assert.throws(() => recoveryPlan(state, [first.id]), /Unknown assets/);
});

async function stoppedRun(t) {
  const { root, workspace } = await fixture(t);
  const evidence = await EvidenceStore.create(join(root, 'runs'));
  await snapshot(workspace.root, join(evidence.root, 'challenge'));
  const identity = { run_id: 'original', image: IMAGE, input_files: workspace.manifest.files };
  const state = new RunState(evidence, identity);
  await evidence.append('run_started', { identity, provider: 'fixture', model: 'fixture', thinking_level: 'high', initial_guidance: '中文报告', task: workspace.task });
  const bytes = Buffer.from('saved output');
  await state.add('assets', { origin: 'input', ...workspace.manifest.files[0], parents: [] });
  await state.add('assets', { origin: 'derived', parents: ['assets-1'], captured_path: '/work/saved.txt', artifact: await evidence.artifact(bytes), bytes: bytes.length, sha256: sha256(bytes) });
  const session = [{ type: 'session', version: 3, id: 'fixture', timestamp: new Date().toISOString(), cwd: evidence.root },
    { type: 'message', id: 'user', parentId: null, timestamp: new Date().toISOString(), message: { role: 'user', content: 'original task', timestamp: Date.now() } }];
  await evidence.append('pi_session_checkpoint', { artifact: await evidence.artifact(session.map(x => JSON.stringify(x)).join('\n') + '\n', 'application/x-ndjson') });
  await evidence.document('recovery.json', { pending: false });
  await evidence.append('run_finished', { status: 'cancelled' });
  return { root, evidence, state };
}

test('application resume inherits self-contained evidence and IDs, uses the original image and preserves the source run', async t => {
  const { root, evidence } = await stoppedRun(t);
  const before = await readFile(join(evidence.root, 'events.jsonl'));
  let selectedImage, selectedOptions, createOptions;
  const writes = new Map();
  const result = await solve({ resumeFrom: evidence.root, runsRoot: join(root, 'runs'), confirmModelUsage: true }, {
    resolveImage: async image => { selectedImage = image; return IMAGE; },
    runtimeFactory: () => ({ name: 'synthetic-resume', start: async () => {}, close: async () => {}, write: async (path, bytes) => writes.set(path, bytes) }),
    preparePi: async options => { selectedOptions = options; return { create: async options => {
      createOptions = options;
      return { prompt: async text => {
        assert.match(text, /Resume the authorized task/);
        assert.equal(options.state.sections.assets.size, 2);
        const candidate = await options.state.submit({ candidate: 'flag{fixture}' });
        await options.state.complete({ outcome: 'completed', candidate_ids: [candidate.id], unresolved: [], summary: 'resumed', writeup: '# Restored' });
      }, stats: () => ({}), close: async () => {} };
    } }; }
  });
  assert.equal(result.status, 'completed', JSON.stringify(result));
  assert.equal(selectedImage, IMAGE);
  assert.equal(selectedOptions.thinkingLevel, 'high');
  assert.equal(createOptions.guidance, '中文报告');
  assert.ok(createOptions.resumeSession.startsWith(result.run_dir));
  assert.equal(writes.get('/work/saved.txt').toString(), 'saved output');
  assert.deepEqual(await readFile(join(evidence.root, 'events.jsonl')), before);
  assert.equal((await audit(result.run_dir)).ok, true);
  assert.equal((await listResumable(join(root, 'runs'))).length, 1);
  const events = await readEvents(result.run_dir);
  assert.equal(events.find(event => event.type === 'run_started').identity.parent_run_id, evidence.root.split('/').at(-1));
  await assert.rejects(inspectResume(result.run_dir), /already completed/);
});

test('resume refuses unconfirmed cleanup, wrong images and corrupted source evidence before starting Docker', async t => {
  const { root, evidence } = await stoppedRun(t);
  await evidence.document('recovery.json', { pending: true });
  await assert.rejects(inspectResume(evidence.root), /cleanup is unconfirmed/);
  await evidence.document('recovery.json', { pending: false });
  let started = 0;
  const deps = { preparePi: async () => ({}), resolveImage: async () => 'sha256:' + 'b'.repeat(64), runtimeFactory: () => { started++; return {}; } };
  await assert.rejects(solve({ resumeFrom: evidence.root, runsRoot: join(root, 'runs'), confirmModelUsage: true }, deps), /original immutable/);
  deps.resolveImage = async () => IMAGE;
  const event = (await readEvents(evidence.root)).find(x => x.type === 'state_record');
  await writeFile(join(evidence.root, event.artifact.artifact), 'corrupt');
  const result = await solve({ resumeFrom: evidence.root, runsRoot: join(root, 'runs'), confirmModelUsage: true }, deps);
  assert.equal(result.status, 'failed');
  assert.match(result.error, /integrity/);
  assert.equal(started, 0);
});
