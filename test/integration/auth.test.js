import test from 'node:test';
import assert from 'node:assert/strict';
import { join } from 'node:path';
import { get } from 'node:http';
import { readFile } from 'node:fs/promises';
import { fixture } from '../helpers.js';
import { loadSdk } from '../../src/pi/sdk.js';
import { ModelCatalog } from '../../src/pi/catalog.js';
import { Workbench } from '../../src/ui/workbench.js';
import { WorkbenchView } from '../../src/ui/workbench-view.js';
function callback(url) {
  return new Promise((resolve, reject) => {
    const request = get(url, response => {
      response.resume();
      response.on('end', () => resolve(response.statusCode));
    });
    request.on('error', reject);
    request.setTimeout(5000, () => request.destroy(new Error('Callback timed out')));
  });
}
test('unchanged Pi ChatGPT OAuth completes browser callback and token exchange with a stable device ID', async t => {
  const {
      root
    } = await fixture(t),
    sdk = await loadSdk();
  const settingsDir = join(root, 'pi-settings');
  const runtime = await sdk.ModelRuntime.create({
    authPath: join(root, 'auth.json'),
    modelsPath: join(root, 'models.json')
  });
  const catalog = await ModelCatalog.create({
    sdk,
    runtime,
    root: join(root, 'settings'),
    settings: sdk.SettingsManager.create(root, settingsDir, {
      projectTrusted: false
    })
  });
  let tokenRequests = 0,
    callbackRequest;
  // Leave the native OAuth implementation untouched. Intercept only the external token HTTP call.
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    assert.equal(String(url), 'https://auth.openai.com/api/accounts/oauth/token');
    assert.equal(options.method, 'POST');
    tokenRequests++;
    const body = new URLSearchParams(options.body);
    assert.equal(body.get('client_id'), 'synthetic-issued-client');
    assert.equal(body.get('code'), 'synthetic-code');
    assert.ok(body.get('code_verifier'));
    return new Response(JSON.stringify({
      access_token: 'synthetic-access',
      refresh_token: 'synthetic-refresh',
      id_token: 'synthetic-id',
      expires_in: 3600,
      scope: 'openid chatgpt.tokens.use.direct'
    }), {
      status: 200,
      headers: {
        'content-type': 'application/json'
      }
    });
  });
  const terminal = {
    rows: 30,
    columns: 120
  };
  const view = new WorkbenchView({
    terminal,
    requestRender: () => {},
    setFocus: () => {}
  }, terminal);
  let browserCalls = 0;
  const app = new Workbench({
    view,
    catalog,
    openUrl: async target => {
      browserCalls++;
      const url = new URL(target);
      assert.equal(url.origin, 'https://auth.openai.com');
      assert.equal(url.searchParams.get('agent_name_hint'), 'FlagCapturer');
      const settings = JSON.parse(await readFile(join(settingsDir, 'settings.json'), 'utf8'));
      assert.equal(url.searchParams.get('ext_agent_host_id'), `urn:uuid:${settings.deviceId}`);
      const redirect = new URL(url.searchParams.get('redirect_uri'));
      assert.equal(redirect.origin, 'http://127.0.0.1:1455');
      redirect.search = new URLSearchParams({
        code: 'synthetic-code',
        client_id: 'synthetic-issued-client',
        state: url.searchParams.get('state')
      }).toString();
      callbackRequest = callback(redirect);
      assert.equal(await callbackRequest, 200);
    }
  });
  app.selectModel = async provider => assert.equal(provider, 'openai');
  const timeout = setTimeout(() => app.interrupt(), 15000);
  try {
    await app.login('openai oauth');
    await callbackRequest;
  } finally {
    clearTimeout(timeout);
    await app.close();
  }
  assert.equal(browserCalls, 1);
  assert.equal(tokenRequests, 1);
  assert.ok((await runtime.listCredentials()).some(x => x.providerId === 'openai' && x.type === 'oauth'));
  assert.equal(view.modal, null);
  assert.deepEqual(view.authNotes, []);
  assert.ok(!JSON.stringify(view.entries).includes('synthetic-access'));
  assert.ok(!JSON.stringify(view.entries).includes('synthetic-code'));
  const saved = JSON.parse(await readFile(join(root, 'auth.json'), 'utf8'));
  assert.equal(saved.openai.clientId, 'synthetic-issued-client');
  const restored = sdk.SettingsManager.create(root, settingsDir, {
    projectTrusted: false
  });
  assert.equal(restored.getOrCreateDeviceId(), catalog.settings.getOrCreateDeviceId());
});
