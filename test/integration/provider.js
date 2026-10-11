import { randomUUID } from 'node:crypto';
import { join } from 'node:path';
import assert from 'node:assert/strict';
import { createAssistantMessageEventStream } from '@earendil-works/pi-ai';
import { loadSdk, preparePi } from '../../src/pi/session.js';

// Uses Pi's real provider boundary, but never opens a network connection.
export async function scriptedPi(t, root, calls, onRequest = () => {}, {
  input = ['text'],
  seedHistory = () => {},
  summarize,
  summaryUsage
} = {}) {
  const sdk = await loadSdk();
  const modelRuntime = await sdk.ModelRuntime.create({
    authPath: join(root, 'auth.json'),
    modelsPath: join(root, 'models.json')
  });
  const provider = `ctf-fixture-${randomUUID()}`;
  let requests = 0;
  let summaries = 0;
  let session;
  modelRuntime.registerProvider(provider, {
    api: 'openai-completions',
    baseUrl: 'https://synthetic.invalid',
    apiKey: 'synthetic-not-a-credential',
    models: [{
      id: 'fixture',
      name: 'Authored integration fixture',
      reasoning: false,
      input,
      contextWindow: 128000,
      maxTokens: 4096,
      cost: {
        input: 0,
        output: 0,
        cacheRead: 0,
        cacheWrite: 0
      }
    }],
    streamSimple: (_model, context) => {
      let call;
      const isSummary = context.messages.some(message => message.role === 'system' && typeof message.content === 'string' && message.content.includes('context summarization assistant'));
      if (summarize && isSummary) {
        summaries++;
        assert.ok(summaries <= 4, 'Unexpected repeated summarization');
        call = {
          content: [{
            type: 'text',
            text: summarize(context)
          }]
        };
      } else {
        assert.ok(requests < calls.length, 'No unexpected model continuation');
        onRequest(context, requests);
        call = calls[requests++];
      }
      const message = {
        role: 'assistant',
        content: call.content ?? [{
          type: 'toolCall',
          id: `integration-${requests}`,
          ...call
        }],
        api: 'openai-completions',
        provider,
        model: 'fixture',
        usage: isSummary && summaryUsage ? summaryUsage : {
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
        stopReason: call.content ? 'stop' : 'toolUse',
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
  t.after(() => modelRuntime.unregisterProvider(provider));
  return {
    modelRuntime,
    provider,
    requests: () => requests,
    summaries: () => summaries,
    session: () => session,
    prepare: options => preparePi({
      ...options,
      sdk: {
        ...sdk,
        ModelRuntime: {
          create: async () => modelRuntime
        },
        SessionManager: {
          create: (cwd, directory) => {
            const manager = sdk.SessionManager.create(cwd, directory);
            seedHistory(manager, provider);
            return manager;
          }
        },
        createAgentSession: async options => {
          const created = await sdk.createAgentSession(options);
          session = created.session;
          return created;
        }
      }
    })
  };
}
