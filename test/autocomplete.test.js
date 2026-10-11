import test from 'node:test';
import assert from 'node:assert/strict';
import { join } from 'node:path';
import { homedir } from 'node:os';
import { writeFile, mkdir } from 'node:fs/promises';
import { CURSOR_MARKER, visibleWidth } from '@earendil-works/pi-tui';
import { fixture } from './helpers.js';
import { createWorkbenchAutocomplete, pathArgument } from '../src/ui/autocomplete.js';
import { WorkbenchView, clean } from '../src/ui/workbench-view.js';
import { Workbench, COMMANDS } from '../src/ui/workbench.js';
function workbench() {
  const terminal = {
    columns: 80,
    rows: 24
  };
  const tui = {
    terminal,
    requestRender: () => {},
    setFocus: component => {
      if (tui.focus) tui.focus.focused = false;
      tui.focus = component;
      if (component) component.focused = true;
    }
  };
  const view = new WorkbenchView(tui, terminal);
  const providers = [{
    id: 'fixture',
    name: 'Fixture',
    configured: true,
    methods: [{
      id: 'oauth',
      label: 'Sign in'
    }, {
      id: 'api_key',
      label: 'API key'
    }]
  }];
  const model = {
    id: 'reasoner',
    provider: 'fixture',
    name: 'Reasoner',
    reasoning: true
  };
  const catalog = {
    preferences: {
      provider: 'fixture',
      model: 'reasoner',
      thinkingLevel: 'medium'
    },
    providers: () => providers,
    models: provider => provider === 'fixture' ? [model] : [],
    runtime: {
      getModel: (provider, id) => provider === 'fixture' && id === 'reasoner' ? model : undefined
    },
    thinking: () => ['off', 'low', 'medium', 'high'],
    save: async () => {}
  };
  const app = new Workbench({
    view,
    catalog
  });
  tui.setFocus(view.editor);
  return {
    app,
    view,
    catalog,
    providers,
    terminal,
    tui
  };
}
async function suggestions(provider, text, force = false) {
  return provider.getSuggestions([text], 0, text.length, {
    signal: new AbortController().signal,
    force
  });
}
async function until(predicate) {
  for (let i = 0; i < 100; i++) {
    if (predicate()) return;
    await new Promise(resolve => setTimeout(resolve, 5));
  }
  assert.fail('Expected editor state was not reached');
}
test('Pi slash completion exposes only workbench commands and inserts through the native provider', async () => {
  const {
    app
  } = workbench();
  const result = await suggestions(app.autocomplete, '/thi');
  assert.equal(result.items[0].value, 'thinking');
  assert.equal(app.autocomplete.applyCompletion(['/thi'], 0, 4, result.items[0], result.prefix).lines[0], '/thinking ');
  const all = await suggestions(app.autocomplete, '/');
  assert.deepEqual(new Set(all.items.map(item => item.value)), new Set(COMMANDS.map(([name]) => name)));
  assert.ok(!all.items.some(item => item.value === 'bash' || item.value === 'share'));
});
test('live provider, model and supported thinking arguments complete on forced Tab without falling back to files', async () => {
  const {
    app,
    providers
  } = workbench();
  assert.equal((await suggestions(app.autocomplete, '/login fix', true)).items[0].value, 'fixture');
  const methods = await suggestions(app.autocomplete, '/login fixture ', true);
  assert.deepEqual(methods.items.map(item => item.value), ['fixture oauth', 'fixture api_key']);
  providers.push({
    id: 'added',
    name: 'New service',
    methods: []
  });
  assert.ok((await suggestions(app.autocomplete, '/login added', true)).items.some(item => item.value === 'added'));
  const model = await suggestions(app.autocomplete, '/model reason', true);
  assert.equal(model.items[0].value, 'fixture/reasoner');
  const thinking = await suggestions(app.autocomplete, '/thinking h', true);
  assert.equal(thinking.items[0].value, 'high');
  assert.equal(await suggestions(app.autocomplete, '/thinking xhigh', true), null);
  const cancelled = new AbortController();
  cancelled.abort();
  assert.equal(await app.autocomplete.getSuggestions(['/login '], 0, 7, {
    signal: cancelled.signal
  }), null);
});
test('native file completion handles Chinese, spaces, directories and @ references without fd or file content reads', async t => {
  const {
      root
    } = await fixture(t),
    {
      app
    } = workbench();
  await writeFile(join(root, '题目 数据.bin'), 'MUST_NOT_LOAD_CONTENT');
  await mkdir(join(root, 'nested folder'));
  await writeFile(join(root, 'nested folder', 'note.txt'), 'MUST_NOT_LOAD_CONTENT');
  const provider = createWorkbenchAutocomplete(app, COMMANDS, {
    basePath: root,
    fdPath: null
  });
  for (const text of ['/import 题', '/network 题', '/memory 题']) {
    const result = await suggestions(provider, text, true);
    const item = result.items.find(item => item.label === '题目 数据.bin');
    assert.ok(item);
    const completed = provider.applyCompletion([text], 0, text.length, item, result.prefix).lines[0];
    assert.equal(pathArgument(completed.slice(completed.indexOf(' ') + 1)), '题目 数据.bin');
    assert.ok(!JSON.stringify(result).includes('MUST_NOT_LOAD_CONTENT'));
  }
  const at = await suggestions(provider, '请参考 @题');
  assert.equal(at.items[0].value, '@"题目 数据.bin"');
  const reference = provider.applyCompletion(['请参考 @题'], 0, '请参考 @题'.length, at.items[0], at.prefix).lines[0];
  assert.equal(reference, '请参考 @"题目 数据.bin" ');
  const dir = await suggestions(provider, '/import nest', true);
  const item = dir.items.find(item => item.label === 'nested folder/');
  const applied = provider.applyCompletion(['/import nest'], 0, 12, item, dir.prefix);
  assert.equal(applied.lines[0], '/import "nested folder/"');
  assert.equal(applied.lines[0][applied.cursorCol], '"', 'Directory completion keeps the cursor inside quotes');
  assert.equal((await suggestions(provider, '/import "nested folder/n', true)).items[0].value, '"nested folder/note.txt"');
  assert.ok((await suggestions(provider, '/network o', true)).items.some(item => item.value === 'off'));
  assert.equal(pathArgument('@"~/题目 数据.bin"'), join(homedir(), '题目 数据.bin'));
});
test('unsafe file names cannot inject terminal controls into the native completion menu', async t => {
  const {
      root
    } = await fixture(t),
    {
      app
    } = workbench();
  await writeFile(join(root, 'unsafe\x1b[31m.bin'), 'fixture');
  const provider = createWorkbenchAutocomplete(app, COMMANDS, {
    basePath: root
  });
  assert.equal(await suggestions(provider, '/import unsafe', true), null);
});
test('real Pi Editor supports Tab acceptance, arrow selection, Escape and visible menu/cursor after resize', async () => {
  const {
    view,
    terminal
  } = workbench();
  for (const key of '/thi') view.editor.handleInput(key);
  await until(() => view.editor.isShowingAutocomplete());
  assert.ok(view.render(80).some(line => clean(line).includes('thinking')));
  view.editor.handleInput('\t');
  assert.equal(view.editor.getText(), '/thinking ');
  for (const key of 'h') view.editor.handleInput(key);
  await until(() => view.editor.isShowingAutocomplete());
  view.editor.handleInput('\t');
  assert.equal(view.editor.getText(), '/thinking high');
  view.editor.setText('');
  view.editor.handleInput('/');
  await until(() => view.editor.isShowingAutocomplete());
  for (let i = 0; i < 7; i++) view.editor.handleInput('\x1b[B');
  for (const [width, rows] of [[120, 30], [50, 12], [30, 8]]) {
    terminal.rows = rows;
    const lines = view.render(width);
    assert.equal(lines.length, rows);
    assert.ok(lines.every(line => visibleWidth(line) <= width));
    assert.ok(lines.some(line => line.includes(CURSOR_MARKER)));
    assert.ok(lines.some(line => clean(line).trimStart().startsWith('→ ')));
  }
  view.editor.handleInput('\x1b');
  assert.equal(view.editor.isShowingAutocomplete(), false);
});
test('completed model, thinking and quoted-path arguments perform the intended workbench action', async () => {
  const {
    app,
    view
  } = workbench();
  view.choose = async () => 'high';
  await app.dispatch('model', 'fixture/reasoner');
  assert.equal(app.options.model, 'reasoner');
  assert.equal(app.options.thinkingLevel, 'high');
  await app.dispatch('thinking', 'low');
  assert.equal(app.options.thinkingLevel, 'low');
  await assert.rejects(app.dispatch('thinking', 'xhigh'), /不支持/);
  await assert.rejects(app.dispatch('model', 'fixture/missing'), /没有模型/);
  let opened;
  app.previewChallenge = async source => {
    opened = source;
    return {
      root: source,
      authorized: true,
      manifest: {
        files: []
      }
    };
  };
  await app.dispatch('import', '@"some folder/题目.bin"');
  assert.equal(opened, 'some folder/题目.bin');
  await app.dispatch('network', '"some folder/grant.json"');
  assert.equal(app.options.networkGrant, 'some folder/grant.json');
  await app.dispatch('network', 'off');
  assert.equal(app.options.networkGrant, undefined);
  app.submit('请参考 @"题目 数据.bin"');
  assert.equal(app.guidance, '请参考 @"题目 数据.bin"');
  await app.close();
  assert.equal(view.editor.isShowingAutocomplete(), false);
});
