import { join, resolve } from 'node:path';
import { getSupportedThinkingLevels } from '@earendil-works/pi-ai/models';
import { loadSdk } from './sdk.js';
import { readRegular, atomicWrite, invariant, json } from '../util.js';
import { s, validate } from '../tools/schema.js';
const endpointSchema = s.object({
  id: s.text(80),
  name: s.text(120),
  baseUrl: s.text(2048),
  api: s.enum(['openai-completions', 'openai-responses', 'anthropic-messages']),
  model: s.text(200),
  reasoning: s.bool(),
  images: s.bool(),
  contextWindow: s.int(1024, 10000000),
  maxTokens: s.int(1, 1000000)
});
const preferencesSchema = s.object({
  provider: s.text(100),
  model: s.text(200),
  thinkingLevel: s.text(20),
  image: s.text(512)
}, []);
async function readJson(path, fallback) {
  try {
    return JSON.parse((await readRegular(path, 256 * 1024)).toString());
  } catch (error) {
    if (error.code === 'ENOENT') return fallback;
    throw error;
  }
}
function endpointConfig(entry) {
  validate(entry, endpointSchema);
  invariant(/^ctf-custom-[a-z0-9-]+$/.test(entry.id), 'Custom provider ID must start with ctf-custom-');
  const url = new URL(entry.baseUrl);
  invariant(['https:', 'http:'].includes(url.protocol) && !url.username && !url.password && !url.search && !url.hash, 'Expected an HTTP(S) base URL without credentials, query or fragment');
  invariant(entry.model.trim() && entry.name.trim() && entry.maxTokens <= entry.contextWindow, 'Invalid custom model limits');
  return {
    name: entry.name,
    baseUrl: entry.baseUrl,
    api: entry.api,
    models: [{
      id: entry.model,
      name: entry.model,
      reasoning: entry.reasoning,
      input: entry.images ? ['text', 'image'] : ['text'],
      contextWindow: entry.contextWindow,
      maxTokens: entry.maxTokens,
      cost: {
        input: 0,
        output: 0,
        cacheRead: 0,
        cacheWrite: 0
      }
    }]
  };
}

/** UI-owned selections; authentication and request preparation remain owned by Pi. */
export class ModelCatalog {
  static async create({
    sdk,
    root = process.env.CTFBOT_SETTINGS_DIR ?? 'data/settings',
    runtime,
    settings
  } = {}) {
    sdk ??= await loadSdk();
    runtime ??= await sdk.ModelRuntime.create();
    settings ??= sdk.SettingsManager.create(process.cwd(), undefined, {
      projectTrusted: false
    });
    const catalog = new ModelCatalog(runtime, resolve(root), settings);
    const preferences = await readJson(join(catalog.root, 'preferences.json'), {});
    validate(preferences, preferencesSchema);
    catalog.preferences = preferences;
    catalog.endpoints = await installCustomProviders(runtime, catalog.root);
    return catalog;
  }
  constructor(runtime, root, settings) {
    this.runtime = runtime;
    this.root = root;
    this.settings = settings;
    this.preferences = {};
    this.endpoints = new Map();
  }
  providers() {
    return this.runtime.getProviders().map(provider => ({
      id: provider.id,
      name: provider.name,
      configured: this.runtime.getProviderAuthStatus(provider.id).configured,
      methods: [...(provider.auth.apiKey?.login ? [{
        id: 'api_key',
        label: provider.auth.apiKey.name || 'API key'
      }] : []), ...(provider.auth.oauth ? [{
        id: 'oauth',
        label: provider.auth.oauth.loginLabel || provider.auth.oauth.name || '登录服务'
      }] : [])],
      ambient: !!provider.auth.apiKey && !provider.auth.apiKey.login
    })).sort((a, b) => Number(b.configured) - Number(a.configured) || a.name.localeCompare(b.name));
  }
  models(provider) {
    return this.runtime.getModels(provider);
  }
  thinking(model) {
    return getSupportedThinkingLevels(model);
  }
  async save(preferences) {
    validate(preferences, preferencesSchema);
    await atomicWrite(join(this.root, 'preferences.json'), json(preferences));
    this.preferences = {
      ...preferences
    };
  }
  async addEndpoint(entry) {
    const config = endpointConfig(entry);
    invariant(this.endpoints.has(entry.id) || this.endpoints.size < 32, 'At most 32 custom providers');
    const entries = new Map(this.endpoints);
    entries.set(entry.id, {
      ...entry
    });
    // Let Pi validate first; restore the live catalog if persistence fails.
    this.runtime.registerProvider(entry.id, config);
    try {
      await atomicWrite(join(this.root, 'providers.json'), json([...entries.values()]));
    } catch (error) {
      const previous = this.endpoints.get(entry.id);
      if (previous) this.runtime.registerProvider(entry.id, endpointConfig(previous));else this.runtime.unregisterProvider(entry.id);
      throw error;
    }
    this.endpoints = entries;
  }
  async login(provider, method, interaction) {
    invariant(this.providers().find(x => x.id === provider)?.methods.some(x => x.id === method), 'Authentication method unavailable');
    let deviceId;
    if (method === 'oauth') {
      // Match Pi's native login: the installation ID belongs to global Pi settings.
      deviceId = this.settings.getOrCreateDeviceId();
      await this.settings.flush();
      const errors = this.settings.drainErrors();
      if (errors.length) {
        const error = new Error('Cannot persist Pi authentication settings', {
          cause: errors[0].error
        });
        error.code = 'CTFBOT_AUTH_SETTINGS';
        throw error;
      }
      invariant(/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(deviceId), 'Pi installation device ID is not a UUID');
    }
    // Do not return credentials to presentation code or transcript observers.
    await this.runtime.login(provider, method, interaction, {
      agentName: 'FlagCapturer',
      getDeviceId: () => deviceId
    });
  }
  async logout(provider, signal) {
    await this.runtime.logout(provider, {
      signal
    });
  }
}
export async function installCustomProviders(runtime, root = process.env.CTFBOT_SETTINGS_DIR ?? 'data/settings') {
  const entries = await readJson(join(root, 'providers.json'), []);
  invariant(Array.isArray(entries) && entries.length <= 32, 'Invalid custom provider configuration');
  const providers = new Map();
  for (const entry of entries) {
    invariant(!providers.has(entry.id), 'Duplicate custom provider ID');
    const config = endpointConfig(entry);
    providers.set(entry.id, entry);
    runtime.registerProvider(entry.id, config);
  }
  return providers;
}
