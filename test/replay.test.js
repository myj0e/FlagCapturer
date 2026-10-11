import test from 'node:test';
import assert from 'node:assert/strict';
import { join } from 'node:path';
import { randomUUID } from 'node:crypto';
import { readFile, readdir } from 'node:fs/promises';
import { tools, IMAGE } from './helpers.js';
import { snapshot } from '../src/challenge/workspace.js';
import { replay, diagnose } from '../src/application/report.js';
import { readEvents } from '../src/evidence/store.js';
async function recorded(t) {
  const data = await tools(t);
  await snapshot(data.workspace.root, join(data.evidence.root, 'challenge'));
  await data.evidence.append('run_started', {
    identity: {
      image: IMAGE
    }
  });
  const script = await data.call('script_save', {
    language: 'javascript',
    source: 'console.log("authored");'
  });
  await data.call('script_run', {
    script_id: script.id
  });
  return data;
}
function runtime({
  failStart = false,
  failCleanup = false,
  exitCode = 0
} = {}) {
  const writes = [],
    commands = [];
  return {
    writes,
    commands,
    name: `ctfbot-${randomUUID()}`,
    start: async () => {
      if (failStart) throw new Error('start fixture failed');
    },
    write: async (path, bytes) => writes.push({
      path,
      bytes
    }),
    execute: async argv => {
      commands.push(argv);
      return {
        code: exitCode,
        stdout: Buffer.from('authored'),
        stderr: Buffer.alloc(0)
      };
    },
    close: async () => {
      if (failCleanup) throw new Error('cleanup fixture failed');
    }
  };
}
test('replay restores saved scripts, records lifecycle and reports actual command failures', async t => {
  const data = await recorded(t),
    sandbox = runtime({
      exitCode: 2
    });
  const result = await replay(data.evidence.root, {
    runsRoot: join(data.root, 'replays'),
    resolveImage: async () => IMAGE,
    runtimeFactory: () => sandbox
  });
  assert.equal(result.ok, false);
  assert.equal(result.replayed.length, 2);
  assert.equal(sandbox.writes[0].bytes.toString(), 'console.log("authored");');
  assert.deepEqual(sandbox.commands[0], ['node', '/work/scripts/script-1.mjs']);
  assert.equal((await diagnose(result.run_dir)).status, 'failed');
  assert.equal(JSON.parse(await readFile(join(result.run_dir, 'recovery.json'), 'utf8')).pending, false);
});
test('replay preserves operation and cleanup errors and leaves pending recovery receipt', async t => {
  const data = await recorded(t),
    runsRoot = join(data.root, 'replays');
  await assert.rejects(replay(data.evidence.root, {
    runsRoot,
    resolveImage: async () => IMAGE,
    runtimeFactory: () => runtime({
      failStart: true,
      failCleanup: true
    })
  }), error => {
    assert.ok(error instanceof AggregateError);
    assert.deepEqual(error.errors.map(x => x.message), ['start fixture failed', 'cleanup fixture failed']);
    return true;
  });
  const run = join(runsRoot, (await readdir(runsRoot))[0]);
  assert.equal(JSON.parse(await readFile(join(run, 'recovery.json'), 'utf8')).pending, true);
  assert.equal((await diagnose(run)).status, 'cleanup_failed');
  assert.ok((await readEvents(run)).some(x => x.type === 'run_failed'));
});
