import test from 'node:test';
import assert from 'node:assert/strict';
import { memoryFixture } from '../context-fixture.js';
import { tools } from '../helpers.js';
import { scriptedPi } from './provider.js';
import { audit, readEvents } from '../../src/evidence/store.js';
test('real Pi automatic compaction preserves external business state and recoverable raw evidence', async t => {
  const {
    root,
    registry,
    state,
    evidence,
    call
  } = await tools(t);
  const candidate = await call('candidate_submit', {
    candidate: 'flag{synthetic_fixture}'
  });
  const observation = await call('challenge_read_text', {
    path: 'attachment.txt'
  });
  const pi = await scriptedPi(t, root, [{
    name: 'state_read',
    arguments: {
      section: 'overview'
    }
  }, {
    name: 'run_complete',
    arguments: {
      outcome: 'completed',
      candidate_ids: [candidate.id],
      unresolved: [],
      summary: 'Recovered authored fixture after compaction',
      writeup: '# Synthetic compaction test\nflag{synthetic_fixture} remains available in business state. Raw input is retained in evidence.'
    }
  }], (context, index) => {
    if (index === 0) assert.ok(JSON.stringify(context.messages).includes('Synthetic compressed history'), 'Compacted history reaches the next model request');
    if (index === 1) {
      const result = context.messages.findLast(x => x.role === 'toolResult');
      assert.equal(JSON.parse(result.content[0].text).counts.candidates, 1);
    }
  }, {
    summarize: context => {
      const input = JSON.parse(context.messages.findLast(m => m.role === 'user').content);
      assert.equal(input.kind, 'compaction');
      assert.ok(input.interaction.every(m => ['user', 'assistant', 'toolResult'].includes(m.role)));
      assert.doesNotMatch(JSON.stringify(input.interaction), /ctf-current-context|You are a single CTF solving agent/);
      return JSON.stringify(memoryFixture({ route: 'Synthetic compressed history. Recover exact business state with state_read.', facts: [{ text: 'A candidate was already submitted', evidence_ids: [candidate.id] }] }));
    },
    seedHistory: (manager, provider) => {
      const usage = {
        input: 200000,
        output: 0,
        cacheRead: 0,
        cacheWrite: 0,
        totalTokens: 200000,
        cost: {
          input: 0,
          output: 0,
          cacheRead: 0,
          cacheWrite: 0,
          total: 0
        }
      };
      for (let i = 0; i < 4; i++) {
        manager.appendMessage({
          role: 'user',
          content: 'Authored old fixture history. '.repeat(12000),
          timestamp: Date.now()
        });
        manager.appendMessage({
          role: 'assistant',
          content: [{
            type: 'text',
            text: 'Authored public progress. '.repeat(12000)
          }],
          api: 'openai-completions',
          provider,
          model: 'fixture',
          usage,
          stopReason: 'stop',
          timestamp: Date.now()
        });
      }
    }
  });
  const prepared = await pi.prepare({
    provider: pi.provider,
    model: 'fixture',
    thinkingLevel: 'off'
  });
  const events = [];
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 10000);
  t.after(() => clearTimeout(timer));
  const agent = await prepared.create({
    registry,
    state,
    evidence,
    signal: controller.signal,
    onEvent: event => events.push(event)
  });
  t.after(() => agent.close());
  const manager = pi.session().sessionManager;
  await agent.prompt('Recover the existing candidate and finish this synthetic task.');
  assert.ok(events.some(x => x.type === 'compaction_end' && x.result && !x.errorMessage), JSON.stringify(events.filter(x => x.type.includes('compaction')).map(x => ({
    type: x.type,
    reason: x.reason,
    error: x.errorMessage?.slice(0, 200)
  }))));
  const compacted = manager.getEntries().find(x => x.type === 'compaction');
  assert.equal(compacted.fromHook, true);
  assert.equal(compacted.details.ctf_context_version, 1);
  assert.ok(state.sections.context_snapshots.size > 0);
  assert.equal([...state.sections.context_snapshots.values()].at(-1).compaction_entry_id, compacted.id);
  assert.equal(pi.requests(), 2);
  assert.ok(pi.summaries() > 0);
  assert.equal(state.completion.outcome, 'completed');
  assert.equal((await evidence.read(observation.artifact.artifact)).toString(), 'flag{synthetic_fixture}');
  assert.equal((await audit(evidence.root)).ok, true);
  assert.ok((await readEvents(evidence.root)).some(x => x.type === 'pi_event' && x.event.type === 'compaction_end'));
});
