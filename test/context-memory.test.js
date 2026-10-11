import test from 'node:test';
import assert from 'node:assert/strict';
import { tools } from './helpers.js';
import { memoryFixture } from './context-fixture.js';
import { ContextMemory } from '../src/context/memory.js';
import { publicMessages, textTokens, validateMemory } from '../src/context/format.js';
import { createProgressReader } from '../src/application/progress.js';

async function memory(t, complete, options = {}) {
  const data = await tools(t);
  const requests = [], events = [], charged = [];
  const coordinator = new ContextMemory({ ...data, model: { contextWindow: 128000, maxTokens: 4096 }, task: '恢复附件中的 Flag', guidance: '中文报告',
    complete: async (context, options) => { requests.push(context); return { content: [{ type: 'text', text: JSON.stringify(await (complete?.(context, options, data) ?? memoryFixture())) }], stopReason: 'stop', usage: { input: 10, output: 5, totalTokens: 15 } }; },
    notify: event => events.push(event), recordUsage: usage => charged.push(usage), ...options });
  return { ...data, coordinator, requests, events, charged };
}
const user = content => ({ role: 'user', content, timestamp: 1 });
const finishTurns = (coordinator, n) => { for (let i = 0; i < n; i++) coordinator.endTurn({ outcome: 'completed', message: { usage: { input: 0 } } }); };

test('live layer stays separate, preserves recent protocol messages and lists new files immediately', async t => {
  const { coordinator, state } = await memory(t);
  const messages = [user('用户补充'), { role: 'assistant', content: [{ type: 'thinking', thinking: 'PRIVATE' }, { type: 'toolCall', id: 'call', name: 'asset_read', arguments: {} }] }, { role: 'toolResult', toolCallId: 'call', content: [{ type: 'text', text: 'result' }] }];
  const projected = await coordinator.project(messages);
  assert.deepEqual(projected.slice(1), messages);
  assert.equal(projected[0].role, 'custom');
  assert.match(projected[0].content, /attachment.txt/);
  await state.add('assets', { origin: 'derived', bytes: 7, parents: ['assets-1'], transformation: { display_name: '恢复的密钥' } });
  const claim = await state.add('claims', { statement: '新观察反驳了原假设', state: 'contradicted', observation_ids: [], supersedes: [] });
  const again = await coordinator.project(projected);
  assert.equal(again.filter(m => m.customType === 'ctf-current-context').length, 1);
  assert.match(again[0].content, /恢复的密钥/);
  assert.match(again[0].content, /新观察反驳了原假设/);
  assert.match(again[0].content, new RegExp(claim.id));
  assert.equal(state.sections.context_snapshots.size, 0);
});

test('ten completed solving turns refresh state without creating compressed history or another solving turn', async t => {
  const { coordinator, state, requests, events, charged, evidence } = await memory(t, () => memoryFixture({ facts: [{ text: '文本中包含候选', evidence_ids: ['assets-1'] }], references: [{ asset_id: 'assets-1', description: '待分析的编码文本', evidence_ids: ['assets-1'] }] }));
  const messages = [user('报告使用中文'), { role: 'assistant', content: [{ type: 'thinking', thinking: 'DO_NOT_EXPORT' }, { type: 'text', text: '检查文件结构。' }] }];
  finishTurns(coordinator, 9); await coordinator.project(messages);
  assert.equal(requests.length, 0);
  coordinator.endTurn({ outcome: 'error' }); await coordinator.project(messages);
  assert.equal(requests.length, 0);
  finishTurns(coordinator, 1);
  const projected = await coordinator.project(messages);
  assert.equal(requests.length, 1);
  assert.equal(charged.length, 1);
  assert.equal(state.sections.context_snapshots.size, 1);
  assert.equal(coordinator.history, '');
  assert.ok(projected.every(m => m.role !== 'compactionSummary'));
  assert.match(projected[0].content, /待分析的编码文本/);
  assert.doesNotMatch(JSON.stringify(requests), /DO_NOT_EXPORT/);
  const snapshot = [...state.sections.context_snapshots.values()][0];
  assert.doesNotMatch((await evidence.read(snapshot.public_transcript_artifact.artifact)).toString(), /DO_NOT_EXPORT/);
  assert.equal(createProgressReader(state)().summary.facts[0], '文本中包含候选');
  assert.deepEqual(events.map(e => e.type), ['context_refresh_start', 'context_refresh_end']);
  finishTurns(coordinator, 10); await coordinator.project(messages);
  assert.equal(requests.length, 1, 'unchanged state and interaction do not trigger another LLM call');
});

test('custom compaction rolls previous history forward and commits only after Pi confirms persistence', async t => {
  const { coordinator, state, requests } = await memory(t, context => {
    const input = JSON.parse(context.messages[0].content);
    assert.equal(input.previous_history, '此前尝试 ZIP 密码，尚未找到密钥');
    return memoryFixture({ route: '此前尝试 ZIP 密码失败；随后检查 LSB，提取疑似密钥。' });
  });
  const draft = await coordinator.summarize({ kind: 'compaction', messages: [user('检查 LSB')], previousHistory: '此前尝试 ZIP 密码，尚未找到密钥' });
  assert.equal(state.sections.context_snapshots.size, 0);
  assert.match(draft.history, /随后检查 LSB/);
  await coordinator.commit(draft, 'pi-compaction-1');
  assert.equal(state.sections.context_snapshots.size, 1);
  assert.equal(coordinator.turns, 0);
  const recent = { role: 'assistant', content: [{ type: 'text', text: '保留的近期原文' }] };
  const projected = await coordinator.project([{ role: 'compactionSummary', summary: draft.history }, recent]);
  assert.equal(projected[0].role, 'compactionSummary');
  assert.equal(projected[1].customType, 'ctf-current-context');
  assert.equal(projected[2], recent);
  assert.equal(requests.length, 1, 'compaction satisfies a pending periodic refresh');
});

test('invalid maintenance output retains the old state and is retried only after another ten turns', async t => {
  const { coordinator, state, requests, events } = await memory(t, () => memoryFixture({ references: [{ asset_id: 'assets-999', description: 'invented', evidence_ids: [] }] }));
  finishTurns(coordinator, 10); await coordinator.project([user('update')]);
  assert.equal(state.sections.context_snapshots.size, 0);
  assert.equal(events.at(-1).updated, false);
  finishTurns(coordinator, 1); await coordinator.project([user('update')]);
  assert.equal(requests.length, 1);
  await assert.rejects(coordinator.summarize({ kind: 'compaction', messages: [user('update')] }), /Unknown memory asset/);
  assert.equal(state.sections.context_snapshots.size, 0);
});

test('stale or cancelled maintenance cannot overwrite newer state; completed tasks skip refresh', async t => {
  const { coordinator, state } = await memory(t, async (_context, _options, data) => { await data.state.add('claims', { statement: 'new', state: 'hypothesis' }); return memoryFixture(); });
  await assert.rejects(coordinator.summarize({ kind: 'refresh', messages: [user('x')] }), /RunState changed/);
  const abort = new AbortController(); abort.abort();
  await assert.rejects(coordinator.summarize({ kind: 'refresh', messages: [], signal: abort.signal }));
  state.completion = { outcome: 'completed' };
  finishTurns(coordinator, 20);
  assert.equal(coordinator.turns, 0);
});

test('large history is covered by ordered bounded batches and source evidence remains recoverable', async t => {
  const { coordinator, requests, evidence } = await memory(t);
  const messages = Array.from({ length: 35 }, (_, i) => user(`试验 ${i}: ` + '记录'.repeat(4000)));
  const draft = await coordinator.summarize({ kind: 'compaction', messages });
  assert.ok(requests.length > 1);
  const covered = requests.flatMap(r => JSON.parse(r.messages[0].content).interaction.map(m => m.message_id));
  assert.deepEqual(covered, publicMessages(messages).map(m => m.message_id));
  for (const request of requests) assert.ok(textTokens(JSON.stringify(request)) < coordinator.budget.maintenanceInput);
  assert.equal(JSON.parse((await evidence.read(draft.public_transcript_artifact.artifact)).toString()).length, messages.length);
});

test('context usage includes live injection once and unsupported negative conclusions are rejected', async t => {
  const { coordinator, state } = await memory(t);
  await coordinator.project([user('x')]);
  const base = { tokens: 100, contextWindow: 128000, percent: 0 };
  assert.ok(coordinator.contextUsage(base).tokens > 100);
  coordinator.endTurn({ outcome: 'completed', message: { usage: { input: 100 } } });
  assert.equal(coordinator.contextUsage(base).tokens, 100);
  assert.equal(coordinator.contextUsage({ ...base, tokens: null }).tokens, null);
  assert.throws(() => validateMemory(memoryFixture({ rejected_routes: [{ attempt: 'password', result: 'failed', scope: 'one password', evidence_ids: [] }] }), state), /require scoped evidence/);
});

test('stopping during periodic maintenance cancels its request without committing a snapshot', async t => {
  const controller = new AbortController();
  let started;
  const ready = new Promise(resolve => { started = resolve; });
  const { coordinator, state } = await memory(t, async (_context, options) => {
    started();
    await new Promise((resolve, reject) => options.signal.addEventListener('abort', () => reject(options.signal.reason), { once: true }));
    return memoryFixture();
  }, { signal: controller.signal });
  finishTurns(coordinator, 10);
  const pending = coordinator.project([user('开始实验')]);
  const rejected = assert.rejects(pending);
  await ready;
  controller.abort(new Error('user stop'));
  await rejected;
  assert.equal(state.sections.context_snapshots.size, 0);
});

test('later maintenance batches repack after a growing snapshot without losing interaction', async t => {
  const { coordinator, requests } = await memory(t, () => memoryFixture({
    route: '公开探索路线'.repeat(450),
    facts: Array.from({ length: 4 }, () => ({ text: '已经观察的公开实验结果'.repeat(20), evidence_ids: [] }))
  }));
  const messages = Array.from({ length: 45 }, (_, i) => user(`试验 ${i}: ` + '结果'.repeat(4000)));
  const draft = await coordinator.summarize({ kind: 'compaction', messages });
  assert.ok(requests.length > 2);
  assert.deepEqual(requests.flatMap(r => JSON.parse(r.messages[0].content).interaction.map(m => m.message_id)), draft.covered_messages);
  assert.equal(JSON.parse(requests.at(-1).messages[0].content).final_batch, true);
  for (const request of requests) assert.ok(textTokens(JSON.stringify(request)) < coordinator.budget.maintenanceInput);
});

test('small context windows bound the recent-record view while retaining complete external state', async t => {
  const { coordinator, state } = await memory(t, undefined, { model: { contextWindow: 16000, maxTokens: 2048 } });
  for (let i = 0; i < 4; i++) await state.add('claims', { statement: '已观察结果'.repeat(100), state: 'hypothesis', observation_ids: [], supersedes: [] });
  const view = JSON.parse(coordinator.currentLayer());
  assert.ok(view.recent_claims.length < 4);
  assert.equal(state.sections.claims.size, 4);
  assert.equal(view.task, '恢复附件中的 Flag');
  assert.equal(view.references[0].asset_id, 'assets-1');
});

test('live context usage compensation uses the injected Pi estimator', async t => {
  const { coordinator } = await memory(t, undefined, { contextTextTokens: () => 321 });
  await coordinator.project([user('x')]);
  assert.equal(coordinator.contextUsage({ tokens: 1000, contextWindow: 128000 }).tokens, 1321);
  coordinator.endTurn({ outcome: 'completed', message: { usage: { input: 1000 } } });
  assert.equal(coordinator.contextUsage({ tokens: 1000, contextWindow: 128000 }).tokens, 1000);
});
