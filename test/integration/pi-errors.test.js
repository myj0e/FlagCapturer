import test from 'node:test';
import assert from 'node:assert/strict';
import { tools } from '../helpers.js';
import { scriptedPi } from './provider.js';
import { readEvents } from '../../src/evidence/store.js';

test('real Pi propagates exhausted OAuth errors and clears failures after a successful retry', async t => {
  const data = await tools(t);
  const failure = { content: [], stopReason: 'error', errorMessage: 'OAuth refresh failed for openai: fetch failed' };
  const pi = await scriptedPi(t, data.root, [failure, failure, failure, failure,
    { name: 'run_complete', arguments: { outcome: 'unsolved', candidate_ids: [], unresolved: ['合成附件未求解'], summary: '合成恢复验收', writeup: '# 合成验收\n仅验证错误传播。' } }
  ]);
  const prepared = await pi.prepare({ provider: pi.provider, model: 'fixture', thinkingLevel: 'off', skillUserDirectory: null });
  const agent = await prepared.create({ ...data, task: '仅验证请求生命周期' });
  t.after(() => agent.close());
  await assert.rejects(agent.prompt('第一请求'), error => {
    assert.equal(error.code, 'CTFBOT_MODEL_AUTH');
    assert.match(error.message, /OAuth refresh failed.*fetch failed/);
    assert.doesNotMatch(error.message, /Pi settled/);
    return true;
  });
  assert.equal(pi.requests(), 3);
  await agent.prompt('第二请求');
  assert.equal(pi.requests(), 5);
  assert.equal(data.state.completion.outcome, 'unsolved');
  const events = await readEvents(data.evidence.root);
  assert.ok(events.some(e => e.type === 'pi_event' && e.event.type === 'auto_retry_end' && e.event.success === false));
  assert.ok(events.some(e => e.type === 'pi_event' && e.event.type === 'auto_retry_end' && e.event.success === true));
});

test('the completion reminder is bounded and asks for honest reporting rather than endless exploration', async t => {
  const data = await tools(t);
  const pi = await scriptedPi(t, data.root, [
    { content: [{ type: 'text', text: '没有可靠下一步。' }] },
    { content: [{ type: 'text', text: '仍未保存报告。' }] }
  ], (context, index) => {
    if (index === 1) {
      assert.match(JSON.stringify(context.messages), /outcome=unsolved/);
      assert.match(JSON.stringify(context.messages), /do not invent more attempts/);
    }
  });
  const prepared = await pi.prepare({ provider: pi.provider, model: 'fixture', thinkingLevel: 'off', skillUserDirectory: null });
  const agent = await prepared.create({ ...data, task: '结束协议合成验收' });
  t.after(() => agent.close());
  await agent.prompt('不要执行解题');
  assert.equal(pi.requests(), 2);
  assert.equal(data.state.completion, null, 'plain text never fabricates a completion');
});

test('user cancellation takes precedence over a simultaneous provider error', async t => {
  const data = await tools(t);
  const controller = new AbortController();
  const pi = await scriptedPi(t, data.root, [
    { content: [], stopReason: 'error', errorMessage: 'OAuth refresh failed: fetch failed' }
  ], () => controller.abort(new Error('用户主动取消合成任务')));
  const prepared = await pi.prepare({ provider: pi.provider, model: 'fixture', thinkingLevel: 'off', skillUserDirectory: null });
  const agent = await prepared.create({ ...data, task: '取消优先级', signal: controller.signal });
  t.after(() => agent.close());
  await assert.rejects(agent.prompt('开始'), /用户主动取消合成任务/);
});
