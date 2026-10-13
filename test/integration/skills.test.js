import test from 'node:test';
import assert from 'node:assert/strict';
import { tools } from '../helpers.js';
import { scriptedPi } from './provider.js';
import { readEvents, audit } from '../../src/evidence/store.js';

test('real Pi expands an explicit skill, exposes metadata and loads references with sandbox allowlist intact', async t => {
  const data = await tools(t); const events = [];
  const pi = await scriptedPi(t, data.root, [
    { name: 'skill_read', arguments: { name: 'ctf-forensics' } },
    { name: 'skill_read', arguments: { path: 'vendor/ctf-forensics/stego-image.md', limit: 2000 } },
    { name: 'candidate_submit', arguments: { candidate: 'flag{synthetic_fixture}' } },
    { name: 'run_complete', arguments: { outcome: 'completed', candidate_ids: ['candidates-1'], unresolved: [], summary: '合成验收', writeup: '# 合成验收\nflag{synthetic_fixture}' } }
  ], (context, index) => {
    const serialized = JSON.stringify(context.messages);
    assert.match(serialized, /available_skills/);
    assert.match(serialized, /ctf-route-review/);
    if (index === 0) {
      assert.match(serialized, /<skill name=\\?"ctf-forensics/);
      assert.doesNotMatch(serialized, /# CTF Forensics - Image Steganography/);
    }
    if (index >= 1) assert.match(serialized, /Execution in this workbench/);
  });
  const prepared = await pi.prepare({ provider: pi.provider, model: 'fixture', thinkingLevel: 'off', initialSkill: 'ctf-forensics', skillUserDirectory: null });
  const agent = await prepared.create({ ...data, task: '合成测试', onEvent: e => events.push(e) });
  t.after(() => agent.close());
  await agent.prompt('从合成附件中取得结果');
  assert.equal(pi.requests(), 4);
  assert.equal(agent.skills().skills.length, 12);
  assert.ok(!pi.session().getActiveToolNames().some(name => ['read', 'bash', 'write', 'edit'].includes(name)));
  assert.equal(data.state.sections.skill_uses.size, 3);
  assert.equal((await audit(data.evidence.root)).ok, true);
  assert.ok((await readEvents(data.evidence.root)).some(e => e.type === 'skills_ready'));
  assert.ok(events.some(e => e.type === 'skill_loaded'));
});

test('real Pi delivers an explicitly queued skill after tool completion and reports delivery separately', async t => {
  const data = await tools(t); const events = [];
  let release, started;
  const waiting = new Promise(resolve => { started = resolve; });
  const gate = new Promise(resolve => { release = resolve; });
  data.registry.define('fixture_wait', 'Synthetic acceptance synchronization only.', { type: 'object', properties: {}, required: [] }, async () => { started(); await gate; return { complete: true }; });
  const pi = await scriptedPi(t, data.root, [
    { name: 'fixture_wait', arguments: {} },
    { name: 'run_complete', arguments: { outcome: 'unsolved', candidate_ids: [], unresolved: [], summary: '合成队列验收', writeup: '# 合成队列验收\n未执行真实解题。' } }
  ], (context, index) => {
    if (index === 1) {
      assert.match(JSON.stringify(context.messages), /<skill name=\\?"ctf-route-review/);
      assert.match(JSON.stringify(context.messages), /检查原始证据覆盖/);
    }
  });
  const prepared = await pi.prepare({ provider: pi.provider, model: 'fixture', thinkingLevel: 'off', skillUserDirectory: null });
  const agent = await prepared.create({ ...data, task: '合成队列验收', onEvent: e => events.push(e) });
  t.after(() => agent.close());
  const prompt = agent.prompt('开始合成验收');
  try {
    await waiting;
    await agent.steer('/skill:ctf-route-review 检查原始证据覆盖');
    assert.ok(events.some(e => e.type === 'user_input_queued'));
    assert.ok(!events.some(e => e.type === 'user_input_delivered'));
  } finally { release(); }
  await prompt;
  assert.equal(events.filter(e => e.type === 'user_input_delivered').length, 1);
  const persisted = await readEvents(data.evidence.root);
  assert.equal(persisted.filter(e => e.type === 'user_input_queued').length, 1);
  assert.equal(persisted.filter(e => e.type === 'user_input_delivered').length, 1);
});
