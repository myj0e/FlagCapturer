import test from 'node:test';
import assert from 'node:assert/strict';
import { tools } from './helpers.js';
import { memoryFixture } from './context-fixture.js';
import { ContextMemory } from '../src/context/memory.js';
import { publicMessages, textTokens, validateMemory, normalizeMemory } from '../src/context/format.js';
import { parseMemoryResponse } from '../src/context/response.js';
import { createProgressReader } from '../src/application/progress.js';
import { recoveryGoals } from '../src/evidence/provenance.js';
import { identifyPublicMessages } from '../src/pi/context-extension.js';

async function memory(t, complete, options = {}) {
  const data = await tools(t);
  const requests = [], events = [], charged = [];
  const coordinator = new ContextMemory({ ...data, model: { contextWindow: 128000, maxTokens: 4096 }, task: '恢复附件中的 Flag', guidance: '中文报告',
    complete: async (context, options) => { requests.push(context); const value = await (complete?.(context, options, data) ?? memoryFixture()); return { content: [{ type: 'text', text: typeof value === 'string' ? value : JSON.stringify(value) }], stopReason: 'stop', usage: { input: 10, output: 5, totalTokens: 15 } }; },
    notify: event => events.push(event), recordUsage: usage => charged.push(usage), ...options });
  return { ...data, coordinator, requests, events, charged };
}
const user = content => ({ role: 'user', content, timestamp: 1 });
async function refresh(coordinator, messages) {
  await coordinator.project(messages);
  await coordinator.background?.promise;
  return coordinator.project(messages);
}
const finishTurns = (coordinator, n) => { for (let i = 0; i < n; i++) coordinator.endTurn({ outcome: 'completed', message: { usage: { input: 0 } } }); };
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; };

test('a slow refresh never holds projection, freezes its cut and coalesces later turns', async t => {
  const gate = deferred(), entered = deferred(), secondGate = deferred();
  let calls = 0;
  const { coordinator, state, requests } = await memory(t, async () => {
    if (++calls === 1) { entered.resolve(); await gate.promise; }
    else await secondGate.promise;
    return memoryFixture({ goal: '旧时点目标', references: [{ asset_id: 'assets-1', description: '原始附件', evidence_ids: [] }] });
  });
  const messages = [user('最初要求')];
  finishTurns(coordinator, 10);
  const initial = await coordinator.project(messages);
  assert.equal(state.sections.context_snapshots.size, 0);
  assert.deepEqual(initial.slice(1), messages);
  await entered.promise;
  const sourceRevision = state.revision;
  const added = await state.add('assets', { origin: 'derived', bytes: 1, parents: ['assets-1'] });
  await state.add('summaries', { goal: '新的公开进展', facts: [], state_revision: state.revision + 1, observation_count: 0 });
  finishTurns(coordinator, 25);
  await coordinator.project([...messages, user('新的补充')]);
  assert.equal(requests.length, 1);
  const first = coordinator.background;
  gate.resolve(); await first.promise;
  assert.equal(state.sections.context_snapshots.size, 0, 'background never publishes on its own');
  const published = await coordinator.project([...messages, user('新的补充')]);
  const snapshot = [...state.sections.context_snapshots.values()][0];
  assert.equal(snapshot.source_revision, sourceRevision);
  assert.equal(snapshot.source_turn, 10);
  assert.equal(snapshot.asset_count, 1);
  assert.equal(snapshot.observation_count, 0);
  assert.equal(coordinator.turns, 35);
  assert.equal(coordinator.coveredTurn, 10);
  assert.equal(JSON.parse(published[0].content).current_state.goal, '新的公开进展');
  assert.equal(createProgressReader(state)().summary.goal, '新的公开进展');
  assert.ok(recoveryGoals(state).includes(added.id), 'new files remain recovery goals');
  const next = coordinator.background;
  assert.ok(next && next !== first);
  await coordinator.stop();
  secondGate.resolve(); await next.promise;
});

test('a background result cannot cite evidence created after its frozen input', async t => {
  const gate = deferred(), entered = deferred();
  const { coordinator, state, requests } = await memory(t, async () => {
    entered.resolve(); await gate.promise;
    return memoryFixture({ facts: [{ text: '不能引用未来记录', evidence_ids: ['assets-2'] }] });
  });
  finishTurns(coordinator, 10); await coordinator.project([user('start')]); await entered.promise;
  await state.add('assets', { origin: 'derived', bytes: 1, parents: ['assets-1'] });
  const job = coordinator.background;
  gate.resolve(); await job.promise;
  assert.equal(requests.length, 2, 'one bounded repair, both checked against the frozen universe');
  assert.equal(state.sections.context_snapshots.size, 0);
  assert.equal(coordinator.maintenanceStatus().state, 'failed');
});

test('compaction preempts refresh and a cancelled request holds its slot until it exits', async t => {
  const gate = deferred(), entered = deferred();
  let refreshCalls = 0;
  const { coordinator, state } = await memory(t, async context => {
    const input = JSON.parse(context.messages[0].content);
    if (input.kind === 'refresh' && ++refreshCalls === 1) { entered.resolve(); await gate.promise; }
    return memoryFixture({ goal: input.kind === 'compaction' ? '压缩后的状态' : '旧后台状态' });
  });
  finishTurns(coordinator, 10); await coordinator.project([user('old')]); await entered.promise;
  const old = coordinator.background;
  coordinator.beginCompaction();
  assert.equal(old.controller.signal.aborted, true);
  const draft = await coordinator.summarize({ kind: 'compaction', messages: [user('compact')] });
  await coordinator.commit(draft, 'pi-cut');
  finishTurns(coordinator, 10); await coordinator.project([user('new')]);
  assert.equal(coordinator.background, old);
  assert.equal(refreshCalls, 1);
  gate.resolve(); await old.promise;
  assert.equal(coordinator.current.memory.goal, '压缩后的状态');
  assert.equal(state.sections.context_snapshots.size, 1);
  assert.equal(coordinator.background, null);
  await coordinator.stop();
});

test('a queued commit checks cancellation at actual write time', async t => {
  const { coordinator, state } = await memory(t);
  const draft = await coordinator.summarize({ kind: 'refresh', messages: [user('cut')] });
  const gate = deferred();
  state.recordQueue = gate.promise;
  const pending = coordinator.commit(draft);
  await Promise.resolve();
  const stopping = coordinator.stop();
  gate.resolve(); await stopping;
  assert.equal(await pending, null);
  assert.equal(state.sections.context_snapshots.size, 0);
});

test('message coverage distinguishes repeated user occurrences and uses public Pi entry identities', () => {
  const first = user('同一句纠正'), second = { ...first, timestamp: 2 };
  const records = publicMessages([first, second]);
  assert.notEqual(records[0].message_id, records[1].message_id);
  const manager = { buildSessionProjection: () => ({ entries: [
    { sourceEntry: { id: 'one' }, messages: [first] }, { sourceEntry: { id: 'two' }, messages: [second] }
  ] }) };
  const identified = identifyPublicMessages([first, second], manager);
  assert.match(identified[0].message_id, /^pi:one:/);
  assert.match(identified[1].message_id, /^pi:two:/);
  assert.equal(identifyPublicMessages([second], manager)[0].message_id, identified[1].message_id);
});

test('live layer stays separate, preserves recent protocol messages and lists new files immediately', async t => {
  const { coordinator, state } = await memory(t);
  const messages = [user('用户补充'), { role: 'assistant', content: [{ type: 'thinking', thinking: 'PRIVATE' }, { type: 'toolCall', id: 'call', name: 'asset_read', arguments: {} }] }, { role: 'toolResult', toolCallId: 'call', content: [{ type: 'text', text: 'result' }] }];
  const projected = await refresh(coordinator, messages);
  assert.deepEqual(projected.slice(1), messages);
  assert.equal(projected[0].role, 'custom');
  assert.match(projected[0].content, /attachment.txt/);
  await state.add('assets', { origin: 'derived', bytes: 7, parents: ['assets-1'], transformation: { display_name: '恢复的密钥' } });
  const claim = await state.add('claims', { statement: '新观察反驳了原假设', state: 'contradicted', observation_ids: [], supersedes: [] });
  const again = await refresh(coordinator, projected);
  assert.equal(again.filter(m => m.customType === 'ctf-current-context').length, 1);
  assert.match(again[0].content, /恢复的密钥/);
  assert.match(again[0].content, /新观察反驳了原假设/);
  assert.match(again[0].content, new RegExp(claim.id));
  assert.equal(state.sections.context_snapshots.size, 0);
});

test('ten completed solving turns refresh state without creating compressed history or another solving turn', async t => {
  const { coordinator, state, requests, events, charged, evidence } = await memory(t, () => memoryFixture({ facts: [{ text: '文本中包含候选', evidence_ids: ['assets-1'] }], references: [{ asset_id: 'assets-1', description: '待分析的编码文本', evidence_ids: ['assets-1'] }] }));
  const messages = [user('报告使用中文'), { role: 'assistant', content: [{ type: 'thinking', thinking: 'DO_NOT_EXPORT' }, { type: 'text', text: '检查文件结构。' }] }];
  finishTurns(coordinator, 9); await refresh(coordinator, messages);
  assert.equal(requests.length, 0);
  coordinator.endTurn({ outcome: 'error' }); await refresh(coordinator, messages);
  assert.equal(requests.length, 0);
  finishTurns(coordinator, 1);
  const projected = await refresh(coordinator, messages);
  assert.equal(requests.length, 1);
  assert.equal(charged.length, 1);
  assert.equal(state.sections.context_snapshots.size, 1);
  assert.equal(coordinator.history, '');
  assert.ok(projected.every(m => m.role !== 'compactionSummary'));
  assert.match(projected[0].content, /待分析的编码文本/);
  assert.doesNotMatch(JSON.stringify(requests), /DO_NOT_EXPORT/);
  const snapshot = [...state.sections.context_snapshots.values()][0];
  assert.deepEqual(snapshot.recovery_plan.goal_asset_ids, ['assets-1']);
  assert.equal(snapshot.recovery_plan.steps[0].kind, 'original_input');
  assert.doesNotMatch((await evidence.read(snapshot.public_transcript_artifact.artifact)).toString(), /DO_NOT_EXPORT/);
  assert.equal(createProgressReader(state)().summary.facts[0], '文本中包含候选');
  assert.deepEqual(events.map(e => e.type), ['context_refresh_start', 'context_refresh_end']);
  finishTurns(coordinator, 10); await refresh(coordinator, messages);
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
  assert.equal([...state.sections.context_snapshots.values()][0].recovery_plan.reason, 'compaction');
  assert.equal(state.sections.context_snapshots.size, 1);
  assert.equal(coordinator.turns, 0);
  const recent = { role: 'assistant', content: [{ type: 'text', text: '保留的近期原文' }] };
  const projected = await refresh(coordinator, [{ role: 'compactionSummary', summary: draft.history }, recent]);
  assert.equal(projected[0].role, 'compactionSummary');
  assert.equal(projected[1].customType, 'ctf-current-context');
  assert.equal(projected[2], recent);
  assert.equal(requests.length, 1, 'compaction satisfies a pending periodic refresh');
});

test('invalid maintenance output gets one repair and retains state until its shorter retry interval', async t => {
  const { coordinator, state, requests, events } = await memory(t, () => memoryFixture({ references: [{ asset_id: 'assets-999', description: 'invented', evidence_ids: [] }] }));
  finishTurns(coordinator, 10); await refresh(coordinator, [user('update')]);
  assert.equal(state.sections.context_snapshots.size, 0);
  assert.equal(events.at(-1).updated, false);
  assert.equal(events.at(-1).retryInTurns, 2);
  finishTurns(coordinator, 1); await refresh(coordinator, [user('update')]);
  assert.equal(requests.length, 2);
  finishTurns(coordinator, 1); await refresh(coordinator, [user('update')]);
  assert.equal(requests.length, 4);
  assert.equal(events.at(-1).retryInTurns, 4);
  await assert.rejects(coordinator.summarize({ kind: 'compaction', messages: [user('update')] }), /Unknown memory asset/);
  assert.equal(state.sections.context_snapshots.size, 0);
});

test('complete JSON wrappers are accepted but prose and multiple JSON documents are rejected', () => {
  assert.deepEqual(parseMemoryResponse('  {"a":1}  '), { a: 1 });
  assert.deepEqual(parseMemoryResponse('```json\n{"a":1}\n```'), { a: 1 });
  assert.throws(() => parseMemoryResponse('Here is the result: {"a":1}'), SyntaxError);
  assert.throws(() => parseMemoryResponse('{"a":1}\n{"b":2}'), SyntaxError);
  assert.throws(() => parseMemoryResponse('```json\n{"a":1}\n```\ntrailing prose'), SyntaxError);
});

test('unsupported rejections become explicit unverified attempts without losing valid facts or accepting invented IDs', async t => {
  const { state } = await tools(t);
  const input = memoryFixture({ facts: [{ text: '有效观察', evidence_ids: ['assets-1'] }],
    rejected_routes: [{ attempt: 'OCR', result: '未成功', scope: '一次有限尝试', evidence_ids: ['assets-1'] }] });
  const { memory: normalized, warnings } = normalizeMemory(input, state);
  assert.deepEqual(normalized.rejected_routes, []);
  assert.equal(normalized.unverified_attempts[0].attempt, 'OCR');
  assert.deepEqual(normalized.facts, input.facts);
  assert.equal(input.rejected_routes.length, 1, 'normalization must not mutate the response');
  assert.equal(warnings[0].kind, 'unsupported_rejection');
  assert.throws(() => normalizeMemory(memoryFixture({ rejected_routes: [{ ...input.rejected_routes[0], evidence_ids: ['observations-999'] }] }), state), /Unknown memory evidence/);
});

test('a malformed response is repaired once, both calls are charged and public diagnostics omit reasoning', async t => {
  const { coordinator, requests, events, charged, evidence, state } = await memory(t, context => {
    const input = JSON.parse(context.messages[0].content);
    return input.repair ? memoryFixture({ facts: [{ text: '修复后有效', evidence_ids: ['assets-1'] }] }) : '0\n{"bad":"JSON"}';
  });
  finishTurns(coordinator, 10);
  await refresh(coordinator, [user('保留事实'), { role: 'assistant', content: [{ type: 'thinking', thinking: 'DO_NOT_SAVE' }] }]);
  assert.equal(requests.length, 2);
  assert.equal(charged.length, 2);
  assert.match(JSON.parse(requests[1].messages[0].content).repair.previous_response, /bad/);
  assert.ok(events.some(event => event.type === 'context_summary_repair'));
  assert.equal(coordinator.maintenanceStatus().state, 'success');
  assert.equal([...state.sections.context_snapshots.values()][0].usage.totalTokens, 30);
  const { readEvents } = await import('../src/evidence/store.js');
  const responses = (await readEvents(evidence.root)).filter(event => event.type === 'context_summary_response');
  assert.deepEqual(responses.map(event => event.valid), [false, true]);
  for (const event of responses) assert.doesNotMatch((await evidence.read(event.reply.artifact)).toString(), /DO_NOT_SAVE/);
});

test('repeated failures back off by 2, 4 and 10 turns, never loop on the same context, and reset after success', async t => {
  let valid = true;
  const { coordinator, requests, state } = await memory(t, () => valid ? memoryFixture() : 'invalid JSON');
  finishTurns(coordinator, 10); await refresh(coordinator, [user('first')]);
  const previous = coordinator.current;
  valid = false;
  finishTurns(coordinator, 10); await refresh(coordinator, [user('new')]);
  assert.equal(coordinator.current, previous);
  assert.equal(coordinator.maintenanceStatus().retryInTurns, 2);
  const calls = requests.length;
  await refresh(coordinator, [user('new')]); await refresh(coordinator, [user('new')]);
  assert.equal(requests.length, calls);
  finishTurns(coordinator, 2); await refresh(coordinator, [user('new')]);
  assert.equal(coordinator.maintenanceStatus().retryInTurns, 4);
  finishTurns(coordinator, 4); await refresh(coordinator, [user('new')]);
  assert.equal(coordinator.maintenanceStatus().retryInTurns, 10);
  valid = true;
  finishTurns(coordinator, 10); await refresh(coordinator, [user('new')]);
  assert.equal(state.sections.context_snapshots.size, 2);
  assert.equal(coordinator.turns, 36);
  assert.equal(coordinator.coveredTurn, 36);
  assert.equal(coordinator.nextRefreshTurn, 46);
  assert.equal(coordinator.refreshFailures, 0);
  assert.equal(coordinator.maintenanceStatus().state, 'success');
});

test('repair budget applies to the whole batched summary and cannot fabricate recovery assets', async t => {
  let count = 0;
  const { coordinator, requests, state } = await memory(t, context => {
    count++;
    const input = JSON.parse(context.messages[0].content);
    if (count === 1) return memoryFixture({ references: [{ asset_id: 'assets-999', description: 'invented', evidence_ids: [] }] });
    if (count === 2) {
      assert.match(input.repair.validation_error, /Unknown memory asset/);
      return memoryFixture({ references: [{ asset_id: 'assets-1', description: '实际附件', evidence_ids: [] }] });
    }
    assert.ok(!input.repair, 'the next history batch starts without a fabricated repair');
    return 'invalid JSON';
  });
  await assert.rejects(coordinator.summarize({ kind: 'compaction', messages: Array.from({ length: 35 }, (_, i) => user(`batch ${i}: ` + '记录'.repeat(4000))) }), SyntaxError);
  assert.equal(requests.length, 3, 'only one repair across all batches');
  assert.equal(state.sections.context_snapshots.size, 0);
  assert.equal(state.sections.assets.size, 1);
});

test('periodic snapshots tolerate later state writes while cancellation and completion skip refresh', async t => {
  const { coordinator, state } = await memory(t, async (_context, _options, data) => { await data.state.add('claims', { statement: 'new', state: 'hypothesis' }); return memoryFixture(); });
  const draft = await coordinator.summarize({ kind: 'refresh', messages: [user('x')] });
  assert.ok(draft.source_revision < state.revision);
  await coordinator.commit(draft);
  assert.equal(coordinator.current.source_revision, draft.source_revision);
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
  assert.deepEqual(covered.map(id => id.split(':').slice(2).join(':')), publicMessages(messages).map(m => m.message_id));
  for (const request of requests) assert.ok(textTokens(JSON.stringify(request)) < coordinator.budget.maintenanceInput);
  assert.equal(JSON.parse((await evidence.read(draft.public_transcript_artifact.artifact)).toString()).length, messages.length);
});

test('context usage includes live injection once and unsupported negative conclusions are rejected', async t => {
  const { coordinator, state } = await memory(t);
  await refresh(coordinator, [user('x')]);
  const base = { tokens: 100, contextWindow: 128000, percent: 0 };
  assert.ok(coordinator.contextUsage(base).tokens > 100);
  coordinator.endTurn({ outcome: 'completed', message: { usage: { input: 100 } } });
  assert.equal(coordinator.contextUsage(base).tokens, 100);
  assert.equal(coordinator.contextUsage({ ...base, tokens: null }).tokens, null);
  assert.throws(() => validateMemory(memoryFixture({ rejected_routes: [{ attempt: 'password', result: 'failed', scope: 'one password', evidence_ids: [] }] }), state), /require scoped evidence/);
});

test('stopping during periodic maintenance does not wait for the model or commit late output', async t => {
  let release, started;
  const ready = new Promise(resolve => { started = resolve; });
  const gate = new Promise(resolve => { release = resolve; });
  const { coordinator, state, evidence } = await memory(t, async () => { started(); await gate; return memoryFixture(); });
  finishTurns(coordinator, 10);
  await coordinator.project([user('开始实验')]);
  await ready;
  const pending = coordinator.background.promise;
  await coordinator.stop();
  const { readEvents } = await import('../src/evidence/store.js');
  const count = (await readEvents(evidence.root)).length;
  release();
  await pending;
  assert.equal(state.sections.context_snapshots.size, 0);
  assert.equal((await readEvents(evidence.root)).length, count, 'late response cannot append evidence after stop');
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
  await refresh(coordinator, [user('x')]);
  assert.equal(coordinator.contextUsage({ tokens: 1000, contextWindow: 128000 }).tokens, 1321);
  coordinator.endTurn({ outcome: 'completed', message: { usage: { input: 1000 } } });
  assert.equal(coordinator.contextUsage({ tokens: 1000, contextWindow: 128000 }).tokens, 1000);
});

test('budget reduction drops plans before scoped evidence without mutating the snapshot', async t => {
  const { coordinator } = await memory(t);
  const finding = { attempt: 'OCR', result: '乱码', scope: '只测试原图和一次缩放；未覆盖相位分离', evidence_ids: ['assets-1'] };
  coordinator.current = { id: 'context_snapshots-1', source_revision: 1, memory: memoryFixture({
    facts: [{ text: '尾随 PNG 为二值图，文字存在重叠', evidence_ids: ['assets-1'] }],
    rejected_routes: [finding], remaining_gap: '尚未解释重叠文字',
    next_steps: Array(8).fill('临时计划'.repeat(100)), approach: Array(8).fill('路线'.repeat(100))
  }) };
  coordinator.budget.dynamic = 1400;
  const projected = JSON.parse(coordinator.currentLayer());
  assert.equal(projected.current_state.next_steps, undefined);
  assert.deepEqual(projected.current_state.rejected_routes, [finding]);
  assert.match(projected.current_state.facts[0].text, /重叠/);
  assert.equal(projected.current_state.remaining_gap, '尚未解释重叠文字');
  assert.equal(coordinator.current.memory.next_steps.length, 8);
  assert.match(projected.scope.plans_are, /Replaceable/);
});

test('periodic maintenance reviews public attempts without claims or a new route ceremony', async t => {
  const { coordinator, state, requests, call } = await memory(t);
  for (let i = 0; i < 12; i++) await call('command_run', { argv: ['python3', '-c', `print(${i})`] });
  finishTurns(coordinator, 12);
  await refresh(coordinator, [user('检查实际输出；不要把普通执行当作假设已排除')]);
  assert.equal(requests.length, 1);
  assert.equal(state.sections.route_reviews.size, 0);
  assert.equal(state.sections.claims.size, 0);
  const input = JSON.parse(requests[0].messages[0].content);
  assert.equal(input.run_state.observations.length, 12);
  assert.match(requests[0].systemPrompt, /Do not choose a new route/);
});
