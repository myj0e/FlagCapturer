import test from 'node:test';
import assert from 'node:assert/strict';
import { tools } from '../helpers.js';
import { scriptedPi } from './provider.js';
import { memoryFixture } from '../context-fixture.js';
import { audit } from '../../src/evidence/store.js';

test('real Pi refreshes at ten completed turns without compacting history or changing system/tools', async t => {
  const { root, registry, state, evidence } = await tools(t);
  let firstSystem;
  const calls = [
    ...Array.from({ length: 10 }, () => ({ name: 'state_read', arguments: { section: 'overview' } })),
    { name: 'candidate_submit', arguments: { candidate: 'flag{synthetic_fixture}' } },
    { name: 'run_complete', arguments: { outcome: 'completed', candidate_ids: ['candidates-1'], unresolved: [], summary: '完成', writeup: '# 完成\nflag{synthetic_fixture}' } }
  ];
  const pi = await scriptedPi(t, root, calls, (context, index) => {
    const systems = context.messages.filter(m => m.role === 'system').map(m => ({ content: m.content, sections: m.sections, toolsAdded: m.toolsAdded }));
    if (!firstSystem) firstSystem = systems;
    assert.deepEqual(systems, firstSystem, 'maintenance cannot modify system prompt/tool declarations');
    const live = context.messages.filter(m => m.role === 'user' && JSON.stringify(m.content).includes('ctf-current-context'));
    assert.equal(live.length, 1);
    if (index >= 10) assert.match(JSON.stringify(live), /编码输入，保留原始字节/);
  }, {
    summarize: context => {
      const input = JSON.parse(context.messages.findLast(m => m.role === 'user').content);
      assert.equal(input.kind, 'refresh');
      assert.ok(context.messages.every(m => m.role !== 'system' || !m.toolsAdded?.length));
      assert.doesNotMatch(JSON.stringify(input), /PRIVATE_REASONING/);
      return JSON.stringify(memoryFixture({ references: [{ asset_id: 'assets-1', description: '编码输入，保留原始字节', evidence_ids: ['assets-1'] }] }));
    },
    summaryUsage: { input: 20, output: 10, totalTokens: 30, cacheRead: 0, cacheWrite: 0, cost: { input: 0.001, output: 0.001, cacheRead: 0, cacheWrite: 0, total: 0.002 } },
    seedHistory: (manager, provider) => {
      manager.appendMessage({ role: 'user', content: '原始要求：中文报告', timestamp: Date.now() });
      manager.appendMessage({ role: 'assistant', content: [{ type: 'thinking', thinking: 'PRIVATE_REASONING' }, { type: 'text', text: '准备检查附件' }], api: 'openai-completions', provider, model: 'fixture', usage: { input: 0, output: 0, totalTokens: 0, cacheRead: 0, cacheWrite: 0, cost: { total: 0 } }, stopReason: 'stop', timestamp: Date.now() });
    }
  });
  const prepared = await pi.prepare({ provider: pi.provider, model: 'fixture', thinkingLevel: 'off' });
  const agent = await prepared.create({ registry, state, evidence, task: '提取 Flag', guidance: '中文报告' });
  t.after(() => agent.close());
  await agent.prompt('开始解题');
  assert.equal(pi.requests(), 12);
  assert.equal(pi.summaries(), 1);
  assert.equal(state.sections.context_snapshots.size, 1);
  const snapshot = [...state.sections.context_snapshots.values()][0];
  assert.equal(snapshot.kind, 'refresh');
  assert.equal(snapshot.compaction_entry_id, null);
  assert.doesNotMatch((await evidence.read(snapshot.public_transcript_artifact.artifact)).toString(), /PRIVATE_REASONING/);
  assert.equal(pi.session().sessionManager.getEntries().filter(e => e.type === 'compaction').length, 0);
  assert.equal(pi.session().sessionManager.getEntries().filter(e => e.type === 'usage' && e.kind === 'ctf-context-maintenance').length, 1);
  assert.equal(agent.stats().usage.tokens.total, 30, 'maintenance billing is included exactly once');
  assert.equal((await audit(evidence.root)).ok, true);
});

const seedUsage = { input: 0, output: 0, totalTokens: 0, cacheRead: 0, cacheWrite: 0, cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } };
function seedPublic(manager, provider, label) {
  for (let i = 0; i < 2; i++) {
    manager.appendMessage({ role: 'user', content: `${label} public user `.repeat(4000), timestamp: Date.now() });
    manager.appendMessage({ role: 'assistant', content: [{ type: 'text', text: `${label} public analysis `.repeat(4000) }], api: 'openai-completions', provider, model: 'fixture', usage: seedUsage, stopReason: 'stop', timestamp: Date.now() });
  }
}

test('real Pi consecutive custom compactions carry the route forward and preserve the recent message boundary', async t => {
  const { root, registry, state, evidence } = await tools(t);
  const inputs = [];
  const pi = await scriptedPi(t, root, [{ name: 'run_complete', arguments: { outcome: 'unsolved', candidate_ids: [], unresolved: ['未获得 Flag'], summary: '保留复现线索', writeup: '# 复现\n本次未获得 Flag，原始实验记录可追溯。' } }], context => {
    const text = JSON.stringify(context.messages);
    assert.match(text, /已尝试 ZIP/);
    assert.match(text, /后检查 LSB/);
    assert.match(text, /RECENT_EXACT_MESSAGE/);
  }, {
    summarize: context => {
      const input = JSON.parse(context.messages.findLast(m => m.role === 'user').content);
      inputs.push(input);
      return JSON.stringify(memoryFixture({ route: input.previous_history.includes('已尝试 ZIP') ? '已尝试 ZIP，后检查 LSB。' : '已尝试 ZIP。' }));
    },
    seedHistory: (manager, provider) => seedPublic(manager, provider, 'FIRST')
  });
  const prepared = await pi.prepare({ provider: pi.provider, model: 'fixture', thinkingLevel: 'off' });
  const agent = await prepared.create({ registry, state, evidence, task: '恢复 Flag' });
  t.after(() => agent.close());
  await pi.session().compact();
  const first = pi.session().sessionManager.getEntries().find(e => e.type === 'compaction');
  assert.match(first.summary, /已尝试 ZIP/);
  seedPublic(pi.session().sessionManager, pi.provider, 'SECOND');
  pi.session().sessionManager.appendMessage({ role: 'user', content: 'RECENT_EXACT_MESSAGE', timestamp: Date.now() });
  await pi.session().compact();
  const compactions = pi.session().sessionManager.getEntries().filter(e => e.type === 'compaction');
  assert.equal(compactions.length, 2);
  assert.ok(inputs.some(input => input.previous_history === first.summary));
  assert.match(compactions[1].summary, /后检查 LSB/);
  assert.equal(state.sections.context_snapshots.size, 2);
  const projection = pi.session().sessionManager.buildSessionContext().messages;
  assert.ok(projection.some(m => m.role === 'user' && m.content === 'RECENT_EXACT_MESSAGE'));
  await agent.prompt('根据已有记录结束本次任务');
  assert.equal(state.completion.outcome, 'unsolved');
  assert.equal((await audit(evidence.root)).ok, true);
});

test('invalid custom compaction aborts safely without committing a summary or falling back to the default compressor', async t => {
  const { root, registry, state, evidence } = await tools(t);
  const pi = await scriptedPi(t, root, [], () => {}, { summarize: () => 'invalid JSON', seedHistory: (manager, provider) => seedPublic(manager, provider, 'INVALID') });
  const prepared = await pi.prepare({ provider: pi.provider, model: 'fixture', thinkingLevel: 'off' });
  const agent = await prepared.create({ registry, state, evidence, task: '恢复 Flag' });
  t.after(() => agent.close());
  await assert.rejects(pi.session().compact());
  assert.equal(state.sections.context_snapshots.size, 0);
  assert.equal(pi.session().sessionManager.getEntries().filter(e => e.type === 'compaction').length, 0);
  assert.ok(pi.session().sessionManager.getEntries().some(e => e.type === 'message' && JSON.stringify(e.message.content).includes('INVALID')));
  assert.equal(pi.summaries(), 1);
  assert.equal(pi.requests(), 0);
  await assert.rejects(agent.prompt('继续'), /JSON/);
  assert.equal(pi.requests(), 0);
});

test('real Pi ignores large image transport and reasoning signatures when deciding whether to compact', async t => {
  const { root, registry, state, evidence } = await tools(t);
  const imageData = 'AAAA'.repeat(90000);
  const signature = 'opaque-provider-signature'.repeat(15000);
  const pi = await scriptedPi(t, root, [{ name: 'run_complete', arguments: {
    outcome: 'unsolved', candidate_ids: [], unresolved: ['未获得 Flag'], summary: '保留证据', writeup: '# 结果\n本次未获得 Flag。'
  } }], context => {
    // The synthetic provider accepts opaque transport fixtures without decoding them.
    assert.ok(context.messages.some(m => Array.isArray(m.content) && m.content.some(b => b.type === 'image' && b.data === imageData)));
    assert.ok(JSON.stringify(context.messages).length > 128000 * 3, 'reproduces the former JSON-byte guard failure');
  }, {
    input: ['text', 'image'],
    seedHistory: (manager, provider) => {
      manager.appendMessage({ role: 'user', content: [{ type: 'text', text: '查看附件图片' }, { type: 'image', data: imageData, mimeType: 'image/png' }], timestamp: Date.now() });
      manager.appendMessage({ role: 'assistant', content: [{ type: 'thinking', thinking: '', thinkingSignature: signature }, { type: 'text', text: '已查看，保留公开观察。' }],
        api: 'openai-completions', provider, model: 'fixture', usage: { ...seedUsage, input: 60000, totalTokens: 60000 }, stopReason: 'stop', timestamp: Date.now() });
    }
  });
  const prepared = await pi.prepare({ provider: pi.provider, model: 'fixture', thinkingLevel: 'off' });
  const agent = await prepared.create({ registry, state, evidence, task: '提取 Flag' });
  t.after(() => agent.close());
  const base = pi.session().getContextUsage();
  assert.ok(base.tokens >= 60000 && base.tokens < 70000);
  assert.ok(agent.stats().context.tokens < 70000, 'statusline follows Pi accounting rather than serialized size');
  await agent.prompt('继续');
  assert.equal(pi.requests(), 1);
  assert.equal(state.completion.outcome, 'unsolved');
  assert.equal(pi.session().sessionManager.getEntries().filter(e => e.type === 'compaction').length, 0);
});
