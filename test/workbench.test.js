import test from 'node:test';
import assert from 'node:assert/strict';
import { visibleWidth, TuiAltScreen, getKeybindings } from '@earendil-works/pi-tui';
import { WorkbenchView, clean } from '../src/ui/workbench-view.js';
import { Workbench, startWorkbench } from '../src/ui/workbench.js';
function view() {
  const terminal = {
    columns: 120,
    rows: 30
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
  return {
    view,
    tui,
    terminal
  };
}
function models() {
  return {
    preferences: {
      provider: 'fixture',
      model: 'fixture',
      thinkingLevel: 'medium'
    },
    runtime: {
      getModel: () => ({
        id: 'fixture',
        provider: 'fixture',
        reasoning: true,
        baseUrl: 'https://synthetic.invalid'
      }),
      getProviderAuthStatus: () => ({
        configured: true
      })
    },
    thinking: () => ['off', 'low', 'medium', 'high'],
    save: async () => {}
  };
}
test('workbench renders responsive Chinese frames and never displays secret prompt values', async () => {
  const data = view();
  data.view.log('Agent', '中文进展\n\x1b]52;c;EVIL\x07正常文本');
  assert.ok(data.view.render(120).some(line => clean(line).includes('解题进展')));
  assert.ok(!data.view.render(80).some(line => clean(line).includes('解题进展')));
  const pending = data.view.ask('API key', {
    secret: true
  });
  data.view.modal.input.setValue('synthetic-secret-key');
  const rendered = data.view.render(80).join('\n');
  assert.ok(!rendered.includes('synthetic-secret-key'));
  assert.ok(rendered.includes('***'));
  data.view.modal.handleInput('\r');
  assert.equal(await pending, 'synthetic-secret-key');
  assert.ok(!JSON.stringify(data.view.entries).includes('synthetic-secret-key'));
  for (const [width, rows] of [[120, 30], [80, 24], [50, 12], [10, 3], [1, 1]]) {
    data.terminal.rows = rows;
    const lines = data.view.render(width);
    assert.equal(lines.length, rows);
    assert.ok(lines.every(line => visibleWidth(line) <= width));
  }
});
test('searchable selector returns IDs rather than duplicated labels and abort clears pending input', async () => {
  const {
    view: v
  } = view();
  const selected = v.choose('模型', [{
    id: 'one',
    label: 'Same model'
  }, {
    id: 'two',
    label: 'Same model'
  }]);
  v.modal.handleInput('\x1b[B');
  v.modal.handleInput('\r');
  assert.equal(await selected, 'two');
  const controller = new AbortController();
  const pending = v.ask('Manual code', {
    secret: true,
    signal: controller.signal
  });
  const rejected = assert.rejects(pending, /abort/i);
  controller.abort();
  await rejected;
  assert.equal(v.modal, null);
});
test('workbench gates runs and supports steering, thinking changes and awaited Stop cleanup', async () => {
  const {
    view: v
  } = view();
  v.choose = async () => 'yes';
  const inputs = [],
    levels = [];
  let release;
  const app = new Workbench({
    view: v,
    catalog: models(),
    solveRun: options => new Promise(resolve => {
      assert.equal(options.modelRuntime, app.catalog.runtime);
      options.onReady({
        state: () => ({}),
        candidates: () => [],
        stats: () => ({
          usage: {
            tokens: {
              total: 5
            },
            cost: 0
          },
          context: { tokens: 32000, contextWindow: 128000, percent: 25 }
        }),
        steer: text => inputs.push(text),
        setThinkingLevel: level => levels.push(level)
      });
      release = () => resolve({
        status: 'cancelled',
        run_dir: '/synthetic/run'
      });
      options.signal.addEventListener('abort', () => setTimeout(release, 10), {
        once: true
      });
    })
  });
  await assert.rejects(app.run(), /import/);
  app.workspace = {
    root: '/synthetic',
    authorized: false
  };
  await assert.rejects(app.run(), /授权/);
  app.workspace.authorized = true;
  await app.run();
  await Promise.resolve();
  app.poll();
  assert.match(v.render(120).map(clean).join('\n'), /上下文 25% \(32K\/128K\)/);
  app.submit('新增线索');
  await Promise.resolve();
  assert.deepEqual(inputs, ['新增线索']);
  v.choose = async () => 'high';
  await app.selectThinking();
  assert.deepEqual(levels, ['high']);
  app.stop();
  await app.active;
  assert.equal(v.state.status, 'cancelled');
  assert.equal(app.active, null);
  app.options.model = 'another-model';
  app.refreshSelection();
  app.poll();
  assert.equal(v.state.context, undefined);
});
test('workbench public transcript excludes thinking and authentication data', () => {
  const {
      view: v
    } = view(),
    app = new Workbench({
      view: v,
      catalog: models()
    });
  app.event({
    type: 'message_update',
    assistantMessageEvent: {
      type: 'thinking_delta',
      delta: 'private reasoning'
    }
  });
  app.event({
    type: 'message_end',
    message: {
      role: 'assistant',
      content: [{
        type: 'thinking',
        thinking: 'private reasoning'
      }, {
        type: 'text',
        text: 'public progress'
      }]
    }
  });
  assert.ok(!JSON.stringify(v.entries).includes('private reasoning'));
  assert.ok(JSON.stringify(v.entries).includes('public progress'));
});
async function until(predicate) {
  for (let i = 0; i < 100; i++) {
    if (predicate()) return;
    await new Promise(resolve => setTimeout(resolve, 5));
  }
  assert.fail('Expected UI state was not reached');
}
test('OAuth browser waiting cancels, clears ephemeral notes and allows the next command', async () => {
  const {
      view: v
    } = view(),
    catalog = models();
  catalog.providers = () => [{
    id: 'fixture',
    name: 'Fixture',
    configured: false,
    methods: [{
      id: 'oauth',
      label: 'OAuth'
    }]
  }];
  catalog.login = async (_provider, _method, interaction) => {
    interaction.notify({
      type: 'auth_url',
      url: 'https://synthetic.invalid/?device=synthetic-code'
    });
    await new Promise((_, reject) => interaction.signal.addEventListener('abort', () => reject(new Error('aborted')), {
      once: true
    }));
  };
  const app = new Workbench({
    view: v,
    catalog,
    openUrl: async () => {}
  });
  v.choose = async () => 'fixture';
  const pending = app.login();
  const rejected = assert.rejects(pending, /已取消/);
  await until(() => v.modal?.authWait);
  assert.ok(v.render(120).join('\n').includes('synthetic-code'));
  app.interrupt();
  await rejected;
  assert.equal(v.modal, null);
  assert.deepEqual(v.authNotes, []);
  assert.ok(!JSON.stringify(v.entries).includes('synthetic-code'));
  app.submit('/state');
  await until(() => v.modal?.document);
  v.modal.finish();
  await app.queue;
});
test('OAuth wait is replaced by a masked manual prompt and secrets never enter the transcript', async () => {
  const {
      view: v
    } = view(),
    catalog = models();
  catalog.providers = () => [{
    id: 'fixture',
    name: 'Fixture',
    configured: false,
    methods: [{
      id: 'oauth',
      label: 'OAuth'
    }]
  }];
  catalog.login = async (_provider, _method, interaction) => {
    interaction.notify({
      type: 'auth_url',
      url: 'https://synthetic.invalid/login'
    });
    assert.equal(await interaction.prompt({
      type: 'manual_code',
      message: '登录码'
    }), 'synthetic-code');
  };
  const app = new Workbench({
    view: v,
    catalog,
    openUrl: async () => {}
  });
  v.choose = async () => 'fixture';
  app.selectModel = async () => {};
  const pending = app.login();
  await until(() => v.modal?.title === '登录码');
  v.modal.input.setValue('synthetic-code');
  assert.ok(!v.render(120).join('\n').includes('synthetic-code'));
  v.modal.handleInput('\r');
  await pending;
  assert.equal(v.modal, null);
  assert.deepEqual(v.authNotes, []);
  assert.ok(!JSON.stringify(v.entries).includes('synthetic-code'));
});
test('real Pi TUI dispatches searchable menu, resizes and quits from menu without hanging', async () => {
  let input,
    resize,
    stopped = false;
  const output = [];
  const terminal = {
    columns: 120,
    rows: 30,
    kittyProtocolActive: false,
    start: (onInput, onResize) => {
      input = onInput;
      resize = onResize;
    },
    stop: () => {
      stopped = true;
    },
    write: data => output.push(data),
    drainInput: async () => {},
    moveBy: () => {},
    hideCursor: () => {},
    showCursor: () => {},
    clearLine: () => {},
    clearFromCursor: () => {},
    clearScreen: () => {},
    setTitle: () => {},
    setProgress: () => {},
    setProgramStatus: () => {}
  };
  const tui = new TuiAltScreen(terminal, true, undefined, { mouse: false, copyOnSelect: false });
  let mountedView;
  const mount = tui.setLayoutRoot.bind(tui);
  tui.setLayoutRoot = root => { mountedView = root; mount(root); };
  const previousPageKeys = getKeybindings().getKeys('tui.altScreen.pageDown');
  const before = process.listenerCount('SIGTERM');
  const running = startWorkbench({}, {
    terminal,
    tui,
    fdPath: null,
    catalog: models()
  });
  await until(() => input);
  for (let i = 0; i < 50; i++) mountedView.log('测试', `日志记录 ${i}`);
  const latestLog = mountedView.render(120).map(clean).join('\n');
  input('\x1b[5~');
  assert.equal(mountedView.scroll, 10);
  assert.notEqual(mountedView.render(120).map(clean).join('\n'), latestLog);
  input('\x1b[6~');
  assert.equal(mountedView.scroll, 0);
  input('\x1bOR');
  await until(() => output.join('').includes('浏览中'));
  input('\x1b[57422u');
  input('\x1b[57422;1:3u');
  assert.equal(mountedView.summaryScroll, 10);
  assert.equal(mountedView.scroll, 0);
  input('\x1b[5~');
  assert.equal(mountedView.summaryScroll, 0);
  input('\x1bOR');
  terminal.columns = 80;
  resize();
  const beforeSummary = output.length;
  input('\x1bOR');
  await until(() => output.slice(beforeSummary).join('').includes('Enter / Esc 返回'));
  input('\x1b[6~');
  assert.equal(mountedView.modal.offset, 10);
  input('\x1b[5~');
  assert.equal(mountedView.modal.offset, 0);
  input('\x1b');
  await until(() => output.join('').includes('/help 命令'));
  input('\x10');
  await until(() => output.join('').includes('操作菜单'));
  terminal.columns = 50;
  terminal.rows = 12;
  resize();
  for (const key of 'quit') input(key);
  input('\r');
  const result = await running;
  assert.equal(result.status, 'closed');
  assert.equal(stopped, true);
  assert.equal(process.listenerCount('SIGTERM'), before);
  assert.deepEqual(getKeybindings().getKeys('tui.altScreen.pageDown'), previousPageKeys);
  assert.ok(output.join('').includes('\x1b[?1049l'));
});
test('multiline input keeps its cursor visible after shrinking the terminal', () => {
  const {
    view: v,
    terminal,
    tui
  } = view();
  tui.setFocus(v.editor);
  v.editor.setText(Array.from({
    length: 25
  }, (_, i) => `线索 ${i}`).join('\n'));
  for (const rows of [30, 12, 8]) {
    terminal.rows = rows;
    const lines = v.render(50);
    assert.equal(lines.length, rows);
    // Pi's zero-width cursor marker must survive clipping of the editor viewport.
    assert.ok(lines.some(line => line.includes('\x1b_pi:c\x07')));
  }
});
test('/login OpenAI resolves Pi provider, defaults to OAuth and automatically opens the complete URL once', async () => {
  const {
      view: v
    } = view(),
    catalog = models(),
    opened = [];
  catalog.providers = () => [{
    id: 'openai',
    name: 'OpenAI',
    methods: [{
      id: 'api_key',
      label: 'OpenAI API key'
    }, {
      id: 'oauth',
      label: 'Sign in with ChatGPT'
    }]
  }];
  const url = 'https://synthetic.invalid/authorize?state=' + 's'.repeat(1600);
  catalog.login = async (provider, method, interaction) => {
    assert.equal(provider, 'openai');
    assert.equal(method, 'oauth');
    interaction.notify({
      type: 'auth_url',
      url
    });
    interaction.notify({
      type: 'auth_url',
      url
    });
    for (let i = 0; i < 20; i++) interaction.notify({
      type: 'progress',
      message: `poll ${i}`
    });
    await new Promise((_, reject) => interaction.signal.addEventListener('abort', () => reject(new Error('aborted')), {
      once: true
    }));
  };
  v.choose = async (title, choices, options) => {
    assert.equal(title, '选择认证方式');
    assert.equal(choices[0].id, 'oauth');
    assert.equal(options.selected, 'oauth');
    return 'oauth';
  };
  const app = new Workbench({
    view: v,
    catalog,
    openUrl: async url => opened.push(url)
  });
  app.submit('/login OpenAI');
  await until(() => opened.length === 1);
  assert.deepEqual(opened, [url]);
  assert.ok(v.authNotes.includes(url));
  assert.ok(v.render(50).join('\n').includes('https://synthetic.invalid'));
  const first = v.render(50).join('\n');
  v.modal.handleInput('\x1b[6~');
  assert.notEqual(v.render(50).join('\n'), first);
  v.modal.handleInput('\r');
  await until(() => opened.length === 2);
  assert.equal(v.modal.authWait, true, 'Enter must reopen the browser while keeping callback waiting alive');
  v.modal.handleInput('\x1b');
  await app.queue;
  assert.equal(v.modal, null);
  assert.equal(app.operation, null);
  assert.deepEqual(v.authNotes, []);
  assert.ok(!JSON.stringify(v.entries).includes('authorize?state='));
});
test('explicit API key bypasses browser auth and unavailable methods never reach Pi login', async () => {
  const {
      view: v
    } = view(),
    catalog = models();
  catalog.providers = () => [{
    id: 'fixture',
    name: 'Fixture',
    methods: [{
      id: 'api_key',
      label: 'API key'
    }]
  }];
  let calls = 0;
  catalog.login = async (provider, method, interaction) => {
    calls++;
    assert.equal(provider, 'fixture');
    assert.equal(method, 'api_key');
    assert.equal(await interaction.prompt({
      type: 'secret',
      message: 'API key'
    }), 'fake-key');
  };
  const app = new Workbench({
    view: v,
    catalog,
    openUrl: () => assert.fail('API key must not launch browser')
  });
  app.selectModel = async () => {};
  const pending = app.login('fixture api_key');
  await until(() => v.modal?.secret);
  v.modal.input.setValue('fake-key');
  v.modal.handleInput('\r');
  await pending;
  await assert.rejects(app.login('fixture oauth'), /不支持/);
  await assert.rejects(app.login('unknown'), /未知服务/);
  assert.equal(calls, 1);
});
test('browser launch failure preserves manual URL fallback and authentication cancellation', async () => {
  const {
      view: v
    } = view(),
    catalog = models();
  catalog.providers = () => [{
    id: 'fixture',
    name: 'Fixture',
    methods: [{
      id: 'oauth',
      label: 'OAuth'
    }]
  }];
  const url = 'https://synthetic.invalid/login';
  catalog.login = async (_provider, _method, interaction) => {
    interaction.notify({
      type: 'auth_url',
      url
    });
    await new Promise((_, reject) => interaction.signal.addEventListener('abort', () => reject(new Error('aborted')), {
      once: true
    }));
  };
  const app = new Workbench({
    view: v,
    catalog,
    openUrl: async () => {
      throw new Error('no display');
    }
  });
  const pending = app.login('fixture');
  const rejected = assert.rejects(pending, /已取消/);
  await until(() => v.authNotes.some(x => x.includes('无法自动打开')));
  assert.ok(v.authNotes.includes(url));
  app.interrupt();
  await rejected;
  assert.equal(v.modal, null);
});
test('OpenAI login runs through a real Pi ModelRuntime and callback cancellation does not cancel authentication', async t => {
  const {
    fixture
  } = await import('./helpers.js');
  const {
    loadSdk
  } = await import('../src/pi/sdk.js');
  const {
    ModelCatalog
  } = await import('../src/pi/catalog.js');
  const {
    join
  } = await import('node:path');
  const {
      root
    } = await fixture(t),
    sdk = await loadSdk();
  const runtime = await sdk.ModelRuntime.create({
    authPath: join(root, 'auth.json'),
    modelsPath: join(root, 'models.json')
  });
  const native = runtime.getProvider('openai');
  assert.ok(native.auth.oauth, 'Pinned Pi OpenAI provider exposes subscription login');
  const url = 'https://synthetic.invalid/openai-authorize';
  // Replace only the network/browser authorization boundary, retaining Pi's real login and storage.
  runtime.registerNativeProvider({
    ...native,
    auth: {
      ...native.auth,
      oauth: {
        ...native.auth.oauth,
        login: async interaction => {
          interaction.notify({
            type: 'auth_url',
            url
          });
          const callback = new AbortController();
          const prompt = interaction.prompt({
            type: 'manual_code',
            message: '粘贴回调 URL',
            signal: callback.signal
          });
          const dismissed = assert.rejects(prompt, /abort/i);
          callback.abort();
          await dismissed;
          assert.equal(interaction.signal.aborted, false);
          return {
            type: 'oauth',
            access: 'synthetic-access',
            refresh: 'synthetic-refresh',
            expires: Date.now() + 3600000
          };
        },
        refresh: async credential => credential,
        toAuth: async credential => ({
          apiKey: credential.access
        })
      }
    }
  });
  const catalog = await ModelCatalog.create({
    sdk,
    runtime,
    settings: sdk.SettingsManager.create(root, join(root, 'pi-settings'), {
      projectTrusted: false
    }),
    root: join(root, 'settings')
  });
  const {
      view: v
    } = view(),
    opened = [];
  const app = new Workbench({
    view: v,
    catalog,
    openUrl: async url => opened.push(url)
  });
  app.selectModel = async provider => assert.equal(provider, 'openai');
  await app.login('openai oauth');
  assert.deepEqual(opened, [url]);
  assert.ok((await runtime.listCredentials()).some(x => x.providerId === 'openai' && x.type === 'oauth'));
  assert.equal(v.modal, null);
  assert.equal(app.operation, null);
  assert.ok(!JSON.stringify(v.entries).includes('synthetic-access'));
  runtime.unregisterProvider('openai');
});
test('manual callback input can scroll the complete login URL without changing the secret input', async () => {
  const {
    view: v,
    terminal
  } = view();
  terminal.rows = 12;
  v.authNotes = ['https://synthetic.invalid/login?state=' + 's'.repeat(1500) + 'URL_END'];
  const pending = v.ask('粘贴回调 URL', {
    secret: true
  });
  v.modal.input.setValue('synthetic-callback');
  assert.ok(v.render(50).join('\n').includes('https://synthetic.invalid'));
  for (let i = 0; i < 10; i++) {
    v.modal.handleInput('\x1b[6~');
    v.render(50);
  }
  assert.ok(v.render(50).join('\n').includes('URL_END'));
  assert.equal(v.modal.input.getValue(), 'synthetic-callback');
  assert.ok(!v.render(50).join('\n').includes('synthetic-callback'));
  v.modal.handleInput('\r');
  assert.equal(await pending, 'synthetic-callback');
});

test('workflow progress remains readable and state details are accessible from the workbench', async () => {
  const { view: v, terminal } = view();
  const app = new Workbench({ view: v, catalog: models() });
  const reads = [];
  app.controls = { state: args => { reads.push(args); return { records: [{ id: 'assets-2' }] }; } };
  let document;
  v.showDocument = async (title, text) => { document = JSON.parse(text); };
  await app.dispatch('state', 'assets assets-2');
  assert.deepEqual(reads[0], { section: 'assets', id: 'assets-2', limit: 20 });
  assert.equal(document.records[0].id, 'assets-2');
  v.state.progress = { summary: { approach: ['验证文件结构'], blockers: ['缺少解析能力'] }, workflow: { status: 'partial' }, files: [{ name: 'Base64 解码结果', bytes: 42, parents: ['attachment.txt'] }] };
  const frame = v.render(120).map(clean).join('\n');
  assert.match(frame, /验证文件结构/);
  v.summaryFocused = true;
  v.scrollBy(-1000);
  const bottom = v.render(120).map(clean).join('\n');
  assert.match(bottom, /缺少解析能力/);
  assert.match(bottom, /Base64 解码结果/);
  assert.doesNotMatch(bottom, /assets-2/);
  assert.equal(v.scroll, 0);
  let summary;
  v.showDocument = async (title, text) => { summary = text; };
  await app.dispatch('summary');
  assert.match(summary, /验证文件结构/);
  assert.match(summary, /缺少解析能力/);
  assert.match(summary, /Base64 解码结果/);
  assert.match(v.render(110).map(clean).join('\n'), /\/summary/);
  for (const width of [120, 110, 80, 30]) assert.ok(v.render(width).every(line => visibleWidth(line) <= width));
  assert.equal(v.render(80).length, terminal.rows);
});

test('tool calls update one bounded card with argv, result and duration without round headers', () => {
  const { view: v } = view();
  const app = new Workbench({ view: v, catalog: models() });
  const initialEntries = v.entries.length;
  app.event({ type: 'turn_start' });
  app.event({ type: 'tool_execution_start', toolCallId: 'a', toolName: 'command_run', args: { argv: ['python3', '-c', 'print("format probe")'] } });
  const entry = v.entries.find(x => x.presentation === 'tool');
  assert.match(entry.text, /执行中/);
  assert.match(entry.text, /用途：运行 Python/);
  app.event({ type: 'tool_execution_end', toolCallId: 'a', toolName: 'command_run', durationMs: 70, isError: false, result: { content: [{ type: 'text', text: JSON.stringify({ exit_code: 0, stdout: 'PNG image data', observation_id: 'observations-1' }) }] } });
  assert.equal(v.entries.filter(x => x.presentation === 'tool').length, 1);
  assert.equal(v.entries.find(x => x.presentation === 'tool'), entry);
  assert.ok(!entry.text.includes('执行中'));
  assert.match(entry.text, /0.07s/);
  assert.match(entry.text, /stdout：PNG image data/);
  app.event({ type: 'turn_end' });
  assert.equal(v.entries.length, initialEntries + 1);
  assert.ok(v.entries.every(x => x.kind !== '轮次'));
  const card = v.renderEntry(entry, 60).map(clean).join('\n');
  assert.match(card, /指令：python3/);
  assert.ok(card.split('\n').filter(line => line.includes('print')).every(line => line.startsWith('│ ')));
  for (const width of [120, 80, 30, 10, 2, 1]) assert.ok(v.renderEntry(entry, width).every(line => visibleWidth(line) <= width));
  app.event({ type: 'tool_execution_start', toolCallId: 'pending', toolName: 'session_read', args: { session_id: 's1' } });
  app.event({ type: 'run_finished', status: 'cancelled' });
  assert.match(v.entries.filter(x => x.presentation === 'tool').at(-1).text, /已取消/);
});
