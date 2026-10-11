import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdir, writeFile, readFile, symlink } from 'node:fs/promises';
import { join } from 'node:path';
import { randomUUID } from 'node:crypto';
import { tools, IMAGE } from './helpers.js';
import { ToolProvisioner, fetchWheel, treeManifest } from '../src/provisioning/service.js';
import { selectRecipes } from '../src/provisioning/catalog.js';
import { sha256 } from '../src/util.js';
import { readEvents } from '../src/evidence/store.js';

const data = Buffer.from('locked synthetic wheel bytes');
const lock = { version: 'fixture', filename: 'construct-fixture-py3-none-any.whl', url: 'https://files.pythonhosted.org/packages/construct-fixture-py3-none-any.whl', bytes: data.length, sha256: sha256(data) };
const result = (text = '') => ({ code: 0, stdout: Buffer.from(text), stderr: Buffer.alloc(0) });
async function setup(t, { present = false, failVerify = false, fetcher } = {}) {
  const context = await tools(t);
  let downloads = 0;
  const owned = [], closed = [], phases = [];
  const service = new ToolProvisioner({ ...context, image: IMAGE, runId: 'fixture', cacheRoot: join(context.root, 'cache'),
    runtime: { execute: async () => result(JSON.stringify({ ready: present, missing: present ? [] : ['construct'] })) },
    onOwnership: async name => owned.push(name), onEvent: event => phases.push(event.state),
    fetcher: fetcher ?? (async () => { downloads++; return new Response(data); }),
    runtimeFactory: config => ({
      name: `ctfbot-${randomUUID()}`, start: async () => {}, close: async function () { closed.push(this.name); },
      execute: async argv => {
        if (argv.includes('install')) {
          await mkdir(join(config.extension.root, 'site'));
          await writeFile(join(config.extension.root, 'site', 'module.py'), 'synthetic installed bytes');
          return result('Installed locked fixture');
        }
        if (argv.includes('check')) return result('No broken requirements');
        return result(JSON.stringify({ ready: !failVerify, missing: failVerify ? ['fixture verification failure'] : [] }));
      }
    })
  });
  service.locks = async () => ({ construct: lock });
  t.after(() => service.close());
  return { ...context, service, owned, closed, phases, downloads: () => downloads };
}
async function settled(service, request) {
  const job = [...service.jobs.values()].find(job => job.id === request.preparation_id);
  await job.promise;
  return service.status(job.id);
}

test('tool supply reuses base capabilities and never downloads unknown or arbitrary requests', async t => {
  const c = await setup(t, { present: true });
  assert.ok(selectRecipes(['constructor', 'https://evil.invalid/tool']).includes(undefined));
  assert.equal((await c.service.request(['https://evil.invalid/tool'])).state, 'unsupported');
  const ready = await settled(c.service, await c.service.request(['cv2', 'ocr']));
  assert.equal(ready.state, 'ready');
  assert.equal(ready.delivery, 'current-image');
  assert.equal(c.downloads(), 0);
  assert.equal(c.owned.length, 0);
});

test('extensions install offline, publish only after verification and reject changed cache contents', async t => {
  const c = await setup(t);
  const request = await c.service.request(['construct']);
  const duplicate = await c.service.request(['construct']);
  assert.equal(duplicate.preparation_id, request.preparation_id);
  const ready = await settled(c.service, request);
  assert.equal(ready.state, 'ready');
  assert.equal(c.downloads(), 1);
  assert.deepEqual(c.phases, ['queued', 'checking', 'downloading', 'installing', 'verifying', 'ready']);
  assert.equal(c.owned.length, 2);
  assert.deepEqual(c.closed, c.owned);
  const job = c.service.jobs.get(ready.environment_id);
  assert.equal(await c.service.validatedCache(job.overlay, job.key), true);
  const manifest = JSON.parse(await readFile(join(job.overlay, 'manifest.json')));
  assert.equal(manifest.image, IMAGE);
  await writeFile(join(job.overlay, 'site', 'module.py'), 'changed');
  await assert.rejects(c.service.execute(ready.environment_id, ['python3'], undefined, async () => {}), /integrity/);
});

test('verification failure is retained and cannot create a ready environment', async t => {
  const c = await setup(t, { failVerify: true });
  const request = await c.service.request(['construct']);
  const failed = await settled(c.service, request);
  assert.equal(failed.state, 'failed');
  assert.equal(failed.environment_id, undefined);
  assert.equal((await c.service.request(['construct'])).state, 'failed');
  assert.equal(c.downloads(), 1);
  assert.deepEqual(c.closed, c.owned);
});

test('cancelled downloads terminate preparation without publishing or restarting the main sandbox', async t => {
  const c = await setup(t, { fetcher: async (url, { signal }) => new Promise((resolve, reject) => {
    signal.addEventListener('abort', () => reject(signal.reason), { once: true });
    if (signal.aborted) reject(signal.reason);
  }) });
  const request = await c.service.request(['construct']);
  await c.service.status(request.preparation_id, true);
  assert.equal((await settled(c.service, request)).state, 'cancelled');
  assert.equal(c.owned.length, 0);
});

test('wheel fetch enforces exact HTTPS source, byte size and SHA-256', async () => {
  await assert.rejects(fetchWheel({ ...lock, url: 'https://evil.invalid/a.whl' }), /source/);
  await assert.rejects(fetchWheel(lock, undefined, async () => new Response(Buffer.from('corrupt'))), /integrity/);
  await assert.rejects(fetchWheel(lock, undefined, async () => new Response(Buffer.alloc(100))), /size/);
  assert.deepEqual(await fetchWheel(lock, undefined, async () => new Response(data)), data);
});

test('cache manifest rejects symlinks and concurrent state writes retain unique IDs', async t => {
  const c = await setup(t);
  const records = await Promise.all(Array.from({ length: 20 }, (_, i) => c.state.add('tool_environments', { n: i })));
  assert.equal(new Set(records.map(x => x.id)).size, 20);
  const events = await readEvents(c.evidence.root);
  assert.equal(new Set(events.filter(x => x.type === 'state_record' && x.section === 'tool_environments').map(x => x.id)).size, 20);
  const root = join(c.root, 'symlink-cache');
  await mkdir(root);
  await symlink(c.file, join(root, 'escape'));
  await assert.rejects(treeManifest(root), /symlink/);
});
