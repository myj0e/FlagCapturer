import test from 'node:test';
import assert from 'node:assert/strict';
import { join } from 'node:path';
import { readFile } from 'node:fs/promises';
import { fixture } from './helpers.js';
import { loadSdk } from '../src/pi/sdk.js';
import { ModelCatalog, installCustomProviders } from '../src/pi/catalog.js';
const endpoint = {
  id: 'ctf-custom-fixture',
  name: 'Fixture API',
  baseUrl: 'https://synthetic.invalid/v1',
  api: 'openai-completions',
  model: 'fixture',
  reasoning: true,
  images: true,
  contextWindow: 128000,
  maxTokens: 8192
};
async function catalog(t) {
  const {
    root
  } = await fixture(t);
  const sdk = await loadSdk();
  const runtime = await sdk.ModelRuntime.create({
    authPath: join(root, 'auth.json'),
    modelsPath: join(root, 'models.json')
  });
  const settings = join(root, 'settings');
  return {
    root,
    runtime,
    sdk,
    settings,
    catalog: await ModelCatalog.create({
      sdk,
      runtime,
      settings: sdk.SettingsManager.create(root, join(root, 'pi-settings'), {
        projectTrusted: false
      }),
      root: settings
    })
  };
}
test('catalog configures a real Pi custom API, persists preferences and delegates credential storage to Pi', async t => {
  const data = await catalog(t),
    c = data.catalog;
  await c.addEndpoint(endpoint);
  assert.deepEqual(c.providers().find(x => x.id === endpoint.id).methods.map(x => x.id), ['api_key']);
  assert.ok(c.models(endpoint.id).some(x => x.id === 'fixture'));
  const result = await c.login(endpoint.id, 'api_key', {
    prompt: async request => {
      assert.equal(request.type, 'secret');
      return 'synthetic-key-not-a-credential';
    },
    notify: () => {}
  });
  assert.equal(result, undefined);
  assert.equal(data.runtime.getProviderAuthStatus(endpoint.id).configured, true);
  await c.save({
    provider: endpoint.id,
    model: 'fixture',
    thinkingLevel: 'high',
    image: 'ctfbot-tools:candidate'
  });
  const config = await readFile(join(data.settings, 'providers.json'), 'utf8');
  assert.ok(!config.includes('synthetic-key-not-a-credential'));
  assert.ok((await readFile(join(data.root, 'auth.json'), 'utf8')).includes('synthetic-key-not-a-credential'));
  const restored = await ModelCatalog.create({
    sdk: data.sdk,
    runtime: data.runtime,
    settings: data.catalog.settings,
    root: data.settings
  });
  assert.equal(restored.preferences.thinkingLevel, 'high');
  await c.logout(endpoint.id);
  assert.equal((await data.runtime.listCredentials()).some(x => x.providerId === endpoint.id), false);
});
test('catalog uses model-specific thinking levels including max and rejects secret/config injection', async t => {
  const {
    catalog: c
  } = await catalog(t);
  assert.deepEqual(c.thinking({
    reasoning: false
  }), ['off']);
  assert.deepEqual(c.thinking({
    reasoning: true,
    thinkingLevelMap: {
      minimal: null,
      xhigh: 'xhigh',
      max: 'max'
    }
  }), ['off', 'low', 'medium', 'high', 'xhigh', 'max']);
  for (const change of [{
    id: 'anthropic'
  }, {
    baseUrl: 'https://key:secret@example.com'
  }, {
    maxTokens: 999999
  }, {
    apiKey: 'not-allowed'
  }]) await assert.rejects(c.addEndpoint({
    ...endpoint,
    ...change
  }));
  await assert.rejects(c.save({
    provider: 'fixture',
    apiKey: 'not-allowed'
  }));
});
test('canonical OAuth notifications, manual code and credential persistence work through a real Pi runtime', async t => {
  const data = await catalog(t),
    base = data.runtime.getProvider('anthropic');
  data.runtime.registerNativeProvider({
    ...base,
    id: 'ctf-faux-oauth',
    name: 'Faux OAuth',
    auth: {
      oauth: {
        name: 'Faux service login',
        login: async interaction => {
          interaction.notify({
            type: 'auth_url',
            url: 'https://synthetic.invalid/login'
          });
          assert.equal(await interaction.prompt({
            type: 'manual_code',
            message: 'Authored test code'
          }), 'synthetic-code');
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
  const events = [];
  await data.catalog.login('ctf-faux-oauth', 'oauth', {
    prompt: async () => 'synthetic-code',
    notify: event => events.push(event)
  });
  assert.equal(events[0].type, 'auth_url');
  assert.ok((await data.runtime.listCredentials()).some(x => x.providerId === 'ctf-faux-oauth' && x.type === 'oauth'));
});
test('custom provider failures leave persisted and live catalogs consistent', async t => {
  const data = await catalog(t),
    c = data.catalog;
  const register = c.runtime.registerProvider.bind(c.runtime);
  c.runtime.registerProvider = () => {
    throw new Error('provider validation failure');
  };
  await assert.rejects(c.addEndpoint(endpoint), /validation/);
  await assert.rejects(readFile(join(data.settings, 'providers.json')), /ENOENT/);
  assert.equal(c.endpoints.size, 0);
  c.runtime.registerProvider = register;
  // A regular file cannot serve as the settings directory; rollback the successful registration.
  const {
    writeFile
  } = await import('node:fs/promises');
  c.root = join(data.root, 'settings-file');
  await writeFile(c.root, 'fixture');
  await assert.rejects(c.addEndpoint(endpoint));
  assert.equal(c.runtime.getModel(endpoint.id, endpoint.model), undefined);
  assert.equal(c.endpoints.size, 0);
});
test('OAuth login supplies a persistent Pi installation device ID and API key login does not create one', async t => {
  const data = await catalog(t),
    c = data.catalog;
  const native = data.runtime.getProvider('openai');
  const captured = [];
  data.runtime.login = async (provider, method, _interaction, options) => {
    assert.equal(provider, 'openai');
    captured.push({
      method,
      id: options.getDeviceId(),
      agentName: options.agentName
    });
  };
  await c.login('openai', 'api_key', {});
  assert.equal(captured[0].id, undefined);
  await assert.rejects(readFile(join(data.root, 'pi-settings', 'settings.json')), /ENOENT/);
  await c.login('openai', 'oauth', {});
  const id = captured[1].id;
  assert.match(id, /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i);
  assert.equal(JSON.parse(await readFile(join(data.root, 'pi-settings', 'settings.json'), 'utf8')).deviceId, id);
  const restored = await ModelCatalog.create({
    sdk: data.sdk,
    runtime: data.runtime,
    root: data.settings,
    settings: data.sdk.SettingsManager.create(data.root, join(data.root, 'pi-settings'), {
      projectTrusted: false
    })
  });
  await restored.login('openai', 'oauth', {});
  assert.equal(captured[2].id, id);
  // The real, unchanged native flow fails before opening a socket if this callback is omitted.
  await assert.rejects(native.auth.oauth.login({
    signal: new AbortController().signal,
    prompt: async () => '',
    notify: () => {}
  }), /requires a device ID/);
});
test('OAuth refuses invalid or unpersisted installation settings before calling the provider', async t => {
  const {
    catalog: c
  } = await catalog(t);
  let calls = 0;
  c.runtime.login = async () => calls++;
  c.settings = {
    getOrCreateDeviceId: () => 'invalid',
    flush: async () => {},
    drainErrors: () => []
  };
  await assert.rejects(c.login('openai', 'oauth', {}), /UUID/);
  c.settings = {
    getOrCreateDeviceId: () => '00000000-0000-0000-0000-000000000000',
    flush: async () => {},
    drainErrors: () => [{
      error: Object.assign(new Error('denied'), {
        code: 'EACCES'
      })
    }]
  };
  await assert.rejects(c.login('openai', 'oauth', {}), {
    code: 'CTFBOT_AUTH_SETTINGS'
  });
  assert.equal(calls, 0);
});
