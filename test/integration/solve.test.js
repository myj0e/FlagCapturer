import test from 'node:test';
import assert from 'node:assert/strict';
import { join } from 'node:path';
import { readFile } from 'node:fs/promises';
import { fixture } from '../helpers.js';
import { scriptedPi } from './provider.js';
import { solve } from '../../src/application/solve.js';
import { replay, diagnose } from '../../src/application/report.js';
import { audit } from '../../src/evidence/store.js';
const image = process.env.CTFBOT_DOCKER_TEST_IMAGE ?? 'ctfbot-tools:candidate';
function cancellation(t) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 30000);
  t.after(() => clearTimeout(timer));
  return controller;
}
test('real Pi and Docker complete an authored fixture and replay its commands with audited evidence', async t => {
  const {
    root,
    workspace
  } = await fixture(t);
  const pi = await scriptedPi(t, root, [{
    name: 'command_run',
    arguments: {
      argv: ['python3', '-c', `from pathlib import Path; print(Path('/challenge/${workspace.manifest.files[0].path}').read_text())`]
    }
  }, {
    name: 'candidate_submit',
    arguments: {
      candidate: 'flag{synthetic_fixture}'
    }
  }, {
    name: 'run_complete',
    arguments: {
      outcome: 'completed',
      candidate_ids: ['candidates-1'],
      summary: '合成题验收完成',
      unresolved: [],
      writeup: '# 合成题验收\n使用 Python Path.read_text 读取只读附件，command_run 的 stdout 保存为证据。\n候选：flag{synthetic_fixture}。未验证平台接受情况。'
    }
  }], (context, index) => {
    if (index === 1) {
      const result = context.messages.findLast(x => x.role === 'toolResult');
      assert.ok(result && !result.isError, 'Actual container command succeeded');
      assert.match(JSON.stringify(result.content), /flag\{synthetic_fixture\}/);
    }
  });
  const signal = cancellation(t).signal;
  const result = await solve({
    workspace: workspace.root,
    provider: pi.provider,
    model: 'fixture',
    thinkingLevel: 'off',
    confirmModelUsage: true,
    runsRoot: join(root, 'runs'),
    image,
    signal
  }, {
    preparePi: pi.prepare
  });
  assert.equal(result.ok, true, JSON.stringify(result));
  assert.equal(pi.requests(), 3);
  assert.equal((await audit(result.run_dir)).ok, true);
  assert.match(await readFile(join(result.run_dir, 'writeup.md'), 'utf8'), /flag\{synthetic_fixture\}/);
  assert.equal(JSON.parse(await readFile(join(result.run_dir, 'recovery.json'), 'utf8')).pending, false);
  const replayed = await replay(result.run_dir, {
    runsRoot: join(root, 'replays'),
    signal
  });
  assert.equal(replayed.ok, true);
  assert.equal(replayed.replayed.length, 1);
  assert.equal((await diagnose(replayed.run_dir)).status, 'replayed');
  assert.equal((await audit(replayed.run_dir)).ok, true);
});
test('Stop aborts a real Pi tool operation and cleans up its actual Docker container', async t => {
  const {
    root,
    workspace
  } = await fixture(t);
  const pi = await scriptedPi(t, root, [{
    name: 'command_run',
    arguments: {
      argv: ['python3', '-u', '-c', "import time; print('running',flush=True); time.sleep(60)"]
    }
  }]);
  const controller = cancellation(t);
  let invoked = false;
  let stopTimer;
  t.after(() => clearTimeout(stopTimer));
  const result = await solve({
    workspace: workspace.root,
    provider: pi.provider,
    model: 'fixture',
    thinkingLevel: 'off',
    confirmModelUsage: true,
    runsRoot: join(root, 'runs'),
    image,
    signal: controller.signal,
    onEvent: event => {
      if (event.type === 'tool_execution_start') {
        invoked = true;
        stopTimer = setTimeout(() => controller.abort(), 300);
      }
    }
  }, {
    preparePi: pi.prepare
  });
  assert.equal(invoked, true);
  assert.equal(pi.requests(), 1);
  assert.equal(result.status, 'cancelled', JSON.stringify(result));
  assert.equal(result.cleanup_error, undefined);
  assert.equal(JSON.parse(await readFile(join(result.run_dir, 'recovery.json'), 'utf8')).pending, false);
  assert.equal((await audit(result.run_dir)).ok, true);
});
test('real Pi resume forks history and restores generated files in the original Docker image without replay', async t => {
  const { root, workspace } = await fixture(t);
  const firstPi = await scriptedPi(t, root, [
    { name: 'script_save', arguments: { language: 'python', source: "from pathlib import Path\nPath('/work/resume-output.txt').write_text('flag{resumed_fixture}')" } },
    { name: 'script_run', arguments: { script_id: 'scripts-1', input_asset_ids: ['assets-1'], outputs: [{ path: '/work/resume-output.txt', display_name: '续接测试结果' }] } }
  ]);
  const controller = cancellation(t);
  const first = await solve({ workspace: workspace.root, provider: firstPi.provider, model: 'fixture', thinkingLevel: 'off',
    confirmModelUsage: true, image, runsRoot: join(root, 'runs'), signal: controller.signal,
    onEvent: event => { if (event.type === 'tool_execution_end' && event.toolName === 'script_run') controller.abort(); }
  }, { preparePi: firstPi.prepare });
  assert.equal(first.status, 'cancelled', JSON.stringify(first));
  assert.equal(firstPi.requests(), 2);
  const originalLog = await readFile(join(first.run_dir, 'events.jsonl'));
  const secondPi = await scriptedPi(t, root, [
    { name: 'command_run', arguments: { argv: ['python3', '-c', "from pathlib import Path; print(Path('/work/resume-output.txt').read_text())"] } },
    { name: 'candidate_submit', arguments: { candidate: 'flag{resumed_fixture}' } },
    { name: 'run_complete', arguments: { outcome: 'completed', candidate_ids: ['candidates-1'], unresolved: [], summary: '续接完成', writeup: '# 续接\n直接恢复已保存文件，没有重跑生成脚本。' } }
  ], (context, index) => {
    if (index === 0) {
      assert.match(JSON.stringify(context.messages), /Fresh sandbox/);
      assert.ok(context.messages.some(message => message.role === 'assistant' && message.content.some(block => block.type === 'toolCall' && block.name === 'script_save')));
    }
    if (index === 1) assert.match(JSON.stringify(context.messages.findLast(message => message.role === 'toolResult')), /flag\{resumed_fixture\}/);
  });
  const second = await solve({ resumeFrom: first.run_dir, provider: secondPi.provider, model: 'fixture', thinkingLevel: 'off',
    confirmModelUsage: true, runsRoot: join(root, 'runs'), signal: cancellation(t).signal
  }, { preparePi: secondPi.prepare });
  assert.equal(second.status, 'completed', JSON.stringify(second));
  assert.equal(secondPi.requests(), 3);
  assert.notEqual(first.run_dir, second.run_dir);
  assert.deepEqual(await readFile(join(first.run_dir, 'events.jsonl')), originalLog);
  assert.equal((await audit(first.run_dir)).ok, true);
  assert.equal((await audit(second.run_dir)).ok, true);
});
test('real Pi forwards rendered sandbox image blocks to a multimodal provider boundary', async t => {
  const {
    root,
    workspace
  } = await fixture(t);
  const pi = await scriptedPi(t, root, [{
    name: 'command_run',
    arguments: {
      argv: ['python3', '-c', "from PIL import Image; Image.new('RGB',(2,2),(255,0,0)).save('/work/fixture.png')"]
    }
  }, {
    name: 'image_view',
    arguments: {
      path: '/work/fixture.png'
    }
  }, {
    name: 'run_complete',
    arguments: {
      outcome: 'unsolved',
      candidate_ids: [],
      unresolved: [],
      summary: 'Synthetic image transfer verified',
      writeup: '# Synthetic image transport\nRendered a two by two red PNG inside the sandbox. This fixture has no submitted flag.'
    }
  }], (context, index) => {
    if (index === 2) {
      const result = context.messages.findLast(x => x.role === 'toolResult');
      assert.ok(result && !result.isError);
      const image = result.content.find(x => x.type === 'image');
      assert.equal(image.mimeType, 'image/png');
      assert.equal(Buffer.from(image.data, 'base64').subarray(0, 8).toString('hex'), '89504e470d0a1a0a');
    }
  }, {
    input: ['text', 'image']
  });
  const result = await solve({
    workspace: workspace.root,
    provider: pi.provider,
    model: 'fixture',
    thinkingLevel: 'off',
    confirmModelUsage: true,
    runsRoot: join(root, 'runs'),
    image,
    signal: cancellation(t).signal
  }, {
    preparePi: pi.prepare
  });
  assert.equal(result.status, 'unsolved', JSON.stringify(result));
  assert.equal(pi.requests(), 3);
  assert.equal((await audit(result.run_dir)).ok, true);
});

test('real Pi calls chained workflow assets and replay maps their references', async t => {
  const flag = 'flag{workflow_replay}';
  const { root, workspace } = await fixture(t, { content: Buffer.from(Buffer.from(flag).toString('base64')).toString('hex') });
  const pi = await scriptedPi(t, root, [
    { name: 'workflow_run', arguments: { pack: 'crypto', operation: 'decode', asset_id: 'assets-1', parameters: { encoding: 'hex' }, method_id: 'method/metadata-semantics' } },
    { name: 'workflow_run', arguments: { pack: 'crypto', operation: 'decode', asset_id: 'assets-2', parameters: { encoding: 'base64' } } },
    { name: 'candidate_submit', arguments: { candidate: flag } },
    { name: 'run_complete', arguments: { outcome: 'completed', candidate_ids: ['candidates-1'], summary: 'Workflow fixture complete', unresolved: [], writeup: '# Workflow fixture\nTwo sandbox decoding operations produced immutable assets. Candidate: ' + flag } }
  ], (context, index) => {
    if (index === 2) {
      const result = context.messages.findLast(x => x.role === 'toolResult');
      assert.ok(!result.isError);
      assert.match(JSON.stringify(result.content), /assets-3/);
    }
  });
  const signal = cancellation(t).signal;
  const result = await solve({ workspace: workspace.root, provider: pi.provider, model: 'fixture', thinkingLevel: 'off', confirmModelUsage: true, runsRoot: join(root, 'runs'), image, signal }, { preparePi: pi.prepare });
  assert.equal(result.ok, true, JSON.stringify(result));
  const reproduced = await replay(result.run_dir, { runsRoot: join(root, 'replays'), signal });
  assert.equal(reproduced.ok, true, JSON.stringify(reproduced));
  assert.equal(reproduced.replayed.length, 2);
  assert.equal((await audit(reproduced.run_dir)).ok, true);
});
