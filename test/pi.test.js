import test from 'node:test';
import assert from 'node:assert/strict';
import { tools } from './helpers.js';
import { preparePi } from '../src/pi/session.js';
test('Pi SDK adapter disables discovery, uses exact sandbox tool allowlist and persists sessions', async t => {
  const {
    registry,
    state,
    evidence
  } = await tools(t);
  let loaderOptions,
    sessionOptions,
    handler,
    closed = false;
  const sdk = {
    estimateTokens: message => Math.ceil(message.content.length / 4),
    ModelRuntime: {
      create: async () => ({
        getModel: () => ({
          input: ['text', 'image'], contextWindow: 128000, maxTokens: 4096
        })
      })
    },
    DefaultResourceLoader: class {
      constructor(options) {
        loaderOptions = options;
      }
      async reload() {
        for (const factory of loaderOptions.extensionFactories) factory({
          on: (name, fn) => {
            if (name === 'agent_before_settle') handler = fn;
          }
        });
      }
    },
    SessionManager: {
      create: (_cwd, path) => ({
        path
      })
    },
    SettingsManager: {
      inMemory: settings => settings
    },
    createAgentSession: async options => {
      sessionOptions = options;
      return {
        session: {
          subscribe: () => () => {},
          bindExtensions: async () => {},
          getActiveToolNames: () => options.customTools.map(x => x.name),
          prompt: async () => {},
          getSessionStats: () => ({}),
          getContextUsage: () => ({}),
          dispose: () => {
            closed = true;
          },
          abort: async () => {}
        }
      };
    }
  };
  const prepared = await preparePi({
    provider: 'fixture',
    model: 'fixture',
    sdk
  });
  assert.equal(prepared.supportsImages, true);
  const agent = await prepared.create({
    registry,
    state,
    evidence
  });
  assert.equal(loaderOptions.noExtensions, true);
  assert.equal(loaderOptions.noSkills, true);
  assert.equal(loaderOptions.noPromptTemplates, true);
  assert.deepEqual(loaderOptions.agentsFilesOverride(), {
    agentsFiles: []
  });
  assert.deepEqual(loaderOptions.appendSystemPromptOverride(), []);
  assert.deepEqual(sessionOptions.tools, [...registry.definitions.keys()]);
  assert.ok(!sessionOptions.tools.includes('bash'));
  assert.ok(sessionOptions.sessionManager.path.endsWith('/pi'));
  assert.equal(handler({
    outcome: 'completed',
    context: {
      canContinue: false
    }
  }).continue, true);
  assert.equal(handler({
    outcome: 'error',
    context: {
      canContinue: true
    }
  }), undefined);
  state.completion = {
    outcome: 'completed'
  };
  assert.equal(handler({
    outcome: 'completed',
    context: {
      canContinue: true
    }
  }), undefined);
  await agent.prompt('fixture');
  await agent.close();
  assert.equal(closed, true);
});
test('real Pi SDK creates isolated session and accepts business tool schemas without model requests', async t => {
  const {
    loadSdk
  } = await import('../src/pi/session.js');
  let sdk;
  try {
    sdk = await loadSdk();
  } catch (error) {
    t.skip(error.message);
    return;
  }
  const {
    join
  } = await import('node:path');
  const {
    root,
    registry,
    state,
    evidence
  } = await tools(t);
  const modelRuntime = await sdk.ModelRuntime.create({
    authPath: join(root, 'empty-auth.json'),
    modelsPath: join(root, 'empty-models.json')
  });
  const selected = modelRuntime.getModel('anthropic', 'claude-sonnet-4-5');
  assert.ok(selected, 'Reference SDK example model exists');
  const isolatedSdk = {
    ...sdk,
    ModelRuntime: {
      create: async () => modelRuntime
    }
  };
  const prepared = await preparePi({
    provider: 'anthropic',
    model: 'claude-sonnet-4-5',
    sdk: isolatedSdk
  });
  const agent = await prepared.create({
    registry,
    state,
    evidence
  });
  agent.setThinkingLevel('high');
  assert.throws(() => agent.setThinkingLevel('unsupported'), /thinking/i);
  assert.ok(agent.stats().usage);
  await agent.close();
});
test('real Pi runs a synthetic provider through continuation, candidate submission and explicit completion', async t => {
  const {
    loadSdk
  } = await import('../src/pi/session.js');
  let sdk;
  try {
    sdk = await loadSdk();
  } catch (error) {
    t.skip(error.message);
    return;
  }
  const {
    createRequire
  } = await import('node:module');
  const {
    dirname,
    resolve,
    join
  } = await import('node:path');
  const {
    pathToFileURL
  } = await import('node:url');
  let entry;
  try {
    entry = import.meta.resolve('@earendil-works/pi-coding-agent');
  } catch {
    entry = process.env.FLAGCAPTURER_PI_SDK ?? resolve(dirname(process.execPath), '../lib/node_modules/@earendil-works/pi-coding-agent/dist/index.js');
  }
  const require = createRequire(entry);
  const {
    access
  } = await import('node:fs/promises');
  let aiPath;
  for (const directory of require.resolve.paths('@earendil-works/pi-ai')) {
    const candidate = join(directory, '@earendil-works/pi-ai/dist/index.js');
    try {
      await access(candidate);
      aiPath = candidate;
      break;
    } catch {}
  }
  assert.ok(aiPath, 'Pi AI dependency is installed');
  const {
    createAssistantMessageEventStream
  } = await import(pathToFileURL(aiPath).href);
  const {
    root,
    registry,
    state,
    evidence
  } = await tools(t);
  const modelRuntime = await sdk.ModelRuntime.create({
    authPath: join(root, 'synthetic-auth.json'),
    modelsPath: join(root, 'synthetic-models.json')
  });
  let requests = 0;
  modelRuntime.registerProvider('ctf-synthetic', {
    api: 'openai-completions',
    baseUrl: 'https://synthetic.invalid',
    apiKey: 'synthetic-not-a-credential',
    models: [{
      id: 'fixture',
      name: 'Synthetic fixture',
      reasoning: false,
      input: ['text'],
      cost: {
        input: 0,
        output: 0,
        cacheRead: 0,
        cacheWrite: 0
      },
      contextWindow: 128000,
      maxTokens: 4096
    }],
    streamSimple: () => {
      requests++;
      assert.ok(requests <= 3, 'No endless continuation after completion');
      const content = requests === 1 ? [{
        type: 'text',
        text: 'Public progress, task still incomplete.'
      }] : [{
        type: 'toolCall',
        id: `call-${requests}`,
        name: requests === 2 ? 'candidate_submit' : 'run_complete',
        arguments: requests === 2 ? {
          candidate: 'flag{synthetic_fixture}'
        } : {
          outcome: 'completed',
          candidate_ids: ['candidates-1'],
          summary: 'Synthetic task complete',
          unresolved: [],
          writeup: '# Synthetic writeup\nSubmitted authored fixture flag.'
        }
      }];
      const message = {
        role: 'assistant',
        content,
        api: 'openai-completions',
        provider: 'ctf-synthetic',
        model: 'fixture',
        usage: {
          input: 0,
          output: 0,
          cacheRead: 0,
          cacheWrite: 0,
          totalTokens: 0,
          cost: {
            input: 0,
            output: 0,
            cacheRead: 0,
            cacheWrite: 0,
            total: 0
          }
        },
        stopReason: requests === 1 ? 'stop' : 'toolUse',
        timestamp: Date.now()
      };
      const stream = createAssistantMessageEventStream();
      stream.push({
        type: 'done',
        reason: message.stopReason,
        message
      });
      stream.end(message);
      return stream;
    }
  });
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 10000);
  t.after(() => clearTimeout(timer));
  const prepared = await preparePi({
    provider: 'ctf-synthetic',
    model: 'fixture',
    thinkingLevel: 'off',
    sdk: {
      ...sdk,
      ModelRuntime: {
        create: async () => modelRuntime
      }
    }
  });
  const events = [];
  const agent = await prepared.create({
    registry,
    state,
    evidence,
    signal: controller.signal,
    onEvent: event => events.push(event)
  });
  try {
    await agent.prompt('Synthetic fixture task.');
    assert.equal(requests, 3, JSON.stringify(events.filter(x => ['extension_error', 'agent_settled'].includes(x.type))));
    assert.equal(state.completion.outcome, 'completed');
  } finally {
    await agent.close();
    modelRuntime.unregisterProvider('ctf-synthetic');
  }
});
