import test from 'node:test';
import assert from 'node:assert/strict';
import { join } from 'node:path';
import { readFile } from 'node:fs/promises';
import { fixture } from '../helpers.js';
import { scriptedPi } from './provider.js';
import { terminalObserver } from '../../src/ui/terminal.js';
import { Workbench } from '../../src/ui/workbench.js';
import { WorkbenchView } from '../../src/ui/workbench-view.js';
import { ModelCatalog } from '../../src/pi/catalog.js';
import { solve } from '../../src/application/solve.js';
import { summaryDocument } from '../../src/ui/solving-summary.js';
import { audit } from '../../src/evidence/store.js';
test('workbench drives real Pi and Docker using its selected shared runtime and exposes completed artifacts', async t => {
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
    name: 'summary_update',
    arguments: { goal: '读取附件并提取 Flag', facts: ['附件包含明文候选'], hypotheses: [], approach: ['保存候选并编写报告'], next_steps: ['记录候选原文'], blockers: [], corrections: [] }
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
      summary: '工作台合成验收',
      unresolved: [],
      writeup: '# 工作台合成验收\n读取附件并记录 flag{synthetic_fixture}，未验证平台接受情况。'
    }
  }]);
  const catalog = await ModelCatalog.create({
    runtime: pi.modelRuntime,
    root: join(root, 'settings')
  });
  const terminal = {
    rows: 30,
    columns: 120
  };
  const tui = {
    terminal,
    requestRender: () => {},
    setFocus: () => {}
  };
  const view = new WorkbenchView(tui, terminal);
  view.choose = async () => 'yes';
  let transcript = '';
  const observe = terminalObserver({ write: text => { transcript += text; } });
  const app = new Workbench({
    view,
    catalog,
    options: {
      provider: pi.provider,
      model: 'fixture',
      thinkingLevel: 'off',
      runsRoot: join(root, 'runs'),
      image: process.env.CTFBOT_DOCKER_TEST_IMAGE ?? 'ctfbot-tools:candidate'
    },
    solveRun: options => solve({ ...options, onEvent: event => { options.onEvent(event); observe(event); } }, {
      preparePi: pi.prepare
    })
  });
  app.workspace = workspace;
  const timeout = setTimeout(() => app.stop(), 30000);
  t.after(async () => {
    clearTimeout(timeout);
    await app.close();
  });
  await app.run();
  await app.active;
  assert.equal(app.result.status, 'completed', JSON.stringify(app.result));
  assert.equal(view.state.candidates.length, 1);
  assert.equal(view.state.tools, 4);
  const cards = view.entries.filter(x => x.presentation === 'tool');
  assert.equal(cards.length, 4);
  assert.match(cards[0].text, /用途：运行 Python/);
  assert.match(cards[0].text, /代码预览：from pathlib/);
  assert.match(cards[0].text, /stdout：flag\{synthetic_fixture\}/);
  assert.match(cards[0].text, /退出码：0/);
  assert.ok(view.entries.every(x => x.kind !== '轮次'));
  assert.ok(cards.every(x => !x.text.includes('执行中')));
  assert.equal((await audit(app.result.run_dir)).ok, true);
  assert.equal(JSON.parse(await readFile(join(app.result.run_dir, 'recovery.json'), 'utf8')).pending, false);
  const text = view.render(120).join('\n');
  assert.match(text, /flag\{synthetic_fixture\}/);
  assert.match(text, /completed/);
  const dashboard = summaryDocument(view.state);
  assert.match(dashboard, /附件包含明文候选/);
  assert.match(dashboard, /记录候选原文/);
  assert.match(dashboard, /attachment.txt/);
  assert.doesNotMatch(dashboard, /assets-1/);
  assert.equal(view.state.progress.summary_pending, false);
  assert.equal(view.state.currentActivity, null);
  assert.ok(view.state.context.contextWindow > 0);
  assert.ok(view.state.context.tokens > 0);
  assert.match(view.render(160).join('\n'), /上下文 [\d.]+%/);
  assert.equal(pi.requests(), 4);
  assert.doesNotMatch(transcript, /第\s*\d+\s*轮/);
  assert.match(transcript, /工具 #1/);
  assert.match(transcript, /指令：python3/);
  assert.match(transcript, /stdout：flag\{synthetic_fixture\}/);
});
