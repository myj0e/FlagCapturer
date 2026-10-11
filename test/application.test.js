import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { fixture, IMAGE } from './helpers.js';
import { solve } from '../src/application/solve.js';
import { readEvents, audit } from '../src/evidence/store.js';
import { report } from '../src/application/report.js';
function dependencies({
  complete = true,
  cleanupFails = false,
  promptFails = false
} = {}) {
  const counters = {
    started: 0,
    closed: 0,
    disposed: 0,
    prompted: 0
  };
  return {
    counters,
    resolveImage: async () => IMAGE,
    runtimeFactory: () => ({
      name: 'ctfbot-11111111-1111-4111-8111-111111111111',
      start: async () => {
        counters.started++;
      },
      close: async () => {
        counters.closed++;
        if (cleanupFails) throw new Error('daemon unavailable');
      }
    }),
    preparePi: async () => ({
      supportsImages: false,
      create: async ({
        registry
      }) => ({
        prompt: async () => {
          counters.prompted++;
          if (promptFails) throw new Error('provider unavailable');
          if (complete) {
            const candidate = JSON.parse((await registry.invoke('candidate_submit', {
              candidate: 'flag{synthetic_fixture}'
            })).content[0].text);
            await registry.invoke('run_complete', {
              outcome: 'completed',
              candidate_ids: [candidate.id],
              summary: 'Found synthetic flag',
              unresolved: [],
              writeup: '# Synthetic solution\nFixture answer submitted.'
            });
          }
        },
        stats: () => ({
          usage: {
            input: 10,
            output: 5
          },
          context: {
            tokens: 15
          }
        }),
        close: async () => {
          counters.disposed++;
        }
      })
    })
  };
}
test('application saves writeup, evidence and metrics and closes runtime on successful solve', async t => {
  const {
      root,
      workspace
    } = await fixture(t),
    deps = dependencies();
  const result = await solve({
    workspace: workspace.root,
    runsRoot: join(root, 'runs'),
    confirmModelUsage: true
  }, deps);
  assert.equal(result.status, 'completed');
  assert.equal(result.ok, true);
  assert.equal(deps.counters.closed, 1);
  assert.equal(deps.counters.disposed, 1);
  assert.match(await readFile(join(result.run_dir, 'writeup.md'), 'utf8'), /Synthetic solution/);
  assert.equal((await audit(result.run_dir)).ok, true);
  const generated = await report(result.run_dir);
  assert.match(await readFile(generated.path, 'utf8'), /Candidate IDs: candidates-1/);
  assert.doesNotMatch(await readFile(generated.path, 'utf8'), /flag\{synthetic_fixture\}/);
});
test('plain model response is not inferred as task completion', async t => {
  const {
      root,
      workspace
    } = await fixture(t),
    deps = dependencies({
      complete: false
    });
  const result = await solve({
    workspace: workspace.root,
    runsRoot: join(root, 'runs'),
    confirmModelUsage: true
  }, deps);
  assert.equal(result.status, 'failed');
  assert.match(result.error, /without run_complete/);
  assert.equal(deps.counters.closed, 1);
});
test('provider failure still disposes agent and sandbox', async t => {
  const {
      root,
      workspace
    } = await fixture(t),
    deps = dependencies({
      promptFails: true
    });
  const result = await solve({
    workspace: workspace.root,
    runsRoot: join(root, 'runs'),
    confirmModelUsage: true
  }, deps);
  assert.equal(result.status, 'failed');
  assert.equal(deps.counters.closed, 1);
  assert.equal(deps.counters.disposed, 1);
});
test('cleanup failure overrides success and preserves recovery receipt', async t => {
  const {
      root,
      workspace
    } = await fixture(t),
    deps = dependencies({
      cleanupFails: true
    });
  const result = await solve({
    workspace: workspace.root,
    runsRoot: join(root, 'runs'),
    confirmModelUsage: true
  }, deps);
  assert.equal(result.status, 'cleanup_failed');
  assert.equal(result.ok, false);
  assert.equal(JSON.parse(await readFile(join(result.run_dir, 'recovery.json'), 'utf8')).pending, true);
  assert.ok((await readEvents(result.run_dir)).some(x => x.type === 'cleanup_failed'));
});
test('model quota and workspace consent gate before runtime or provider preparation', async t => {
  const {
      workspace
    } = await fixture(t, {
      authorized: false
    }),
    deps = dependencies();
  await assert.rejects(solve({
    workspace: workspace.root
  }, deps), /confirm-model-usage/);
  await assert.rejects(solve({
    workspace: workspace.root,
    confirmModelUsage: true
  }, deps), /authorize/);
  assert.equal(deps.counters.started, 0);
  assert.equal(deps.counters.prompted, 0);
});
test('pre-cancelled run makes no model calls or Docker requests', async t => {
  const {
      workspace
    } = await fixture(t),
    deps = dependencies(),
    controller = new AbortController();
  controller.abort();
  await assert.rejects(solve({
    workspace: workspace.root,
    confirmModelUsage: true,
    signal: controller.signal
  }, deps), /abort/i);
  assert.equal(deps.counters.started, 0);
});
