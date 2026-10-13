import { readFile, writeFile, mkdir, readdir, lstat, rename, rm, mkdtemp } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { recipes, selectRecipes, probeSource } from './catalog.js';
import { DockerRuntime } from '../runtime/docker.js';
import { privateDir, readRegular, sha256, invariant, json, relativePath } from '../util.js';

const MAX_CACHE_BYTES = 128 * 1024 * 1024;
const version = 1;
export async function treeManifest(root) {
  const files = [];
  let bytes = 0;
  async function walk(path = '') {
    for (const name of (await readdir(join(root, path))).sort()) {
      const relative = path ? `${path}/${name}` : name;
      if (relative === 'manifest.json') continue;
      relativePath(relative);
      const stat = await lstat(join(root, relative));
      invariant(!stat.isSymbolicLink(), 'Extension cache contains a symlink');
      if (stat.isDirectory()) await walk(relative);
      else {
        invariant(stat.isFile(), 'Extension cache contains a special file');
        bytes += stat.size;
        invariant(bytes <= MAX_CACHE_BYTES && files.length < 4096, 'Extension cache exceeds resource limit');
        files.push({ path: relative, bytes: stat.size, sha256: sha256(await readRegular(join(root, relative), MAX_CACHE_BYTES)) });
      }
    }
  }
  await walk();
  return files;
}

// Only fixed lockfile URLs are fetched. No model/task data enters network requests.
export async function fetchWheel(lock, signal, fetcher = fetch) {
  const url = new URL(lock.url);
  invariant(url.protocol === 'https:' && url.hostname === 'files.pythonhosted.org' && !url.username && !url.password && !url.port && url.pathname.endsWith('/' + lock.filename), 'Wheel source is not allowed');
  invariant(/^[a-f0-9]{64}$/.test(lock.sha256) && lock.bytes > 0 && lock.bytes <= 8 * 1024 * 1024 && /^[\w.-]+\.whl$/.test(lock.filename), 'Invalid wheel lock');
  const response = await fetcher(url, { redirect: 'error', signal: AbortSignal.any([signal ?? new AbortController().signal, AbortSignal.timeout(60000)]) });
  invariant(response.ok && response.body, `Wheel download failed: HTTP ${response.status}`);
  const chunks = [];
  let bytes = 0;
  try {
    for await (const chunk of response.body) {
      bytes += chunk.length;
      invariant(bytes <= lock.bytes, 'Wheel download exceeds locked size');
      chunks.push(Buffer.from(chunk));
    }
  } finally {
    if (bytes > lock.bytes) await response.body.cancel().catch(() => {});
  }
  const data = Buffer.concat(chunks);
  invariant(data.length === lock.bytes && sha256(data) === lock.sha256, 'Wheel integrity failure');
  return data;
}

export class ToolProvisioner {
  constructor({ runtime, evidence, state, input, image, runId, signal, cacheRoot = resolve('data/tool-cache'), onEvent = () => {}, onOwnership = async () => {}, onRelease = async () => {}, runtimeFactory = config => new DockerRuntime(config), fetcher = fetch, allowDownload = true }) {
    Object.assign(this, { runtime, evidence, state, input, image, runId, signal, cacheRoot, onEvent, onOwnership, onRelease, runtimeFactory, fetcher, allowDownload });
    this.jobs = new Map(); this.queue = Promise.resolve(); this.controller = new AbortController();
    this.signal = signal ? AbortSignal.any([signal, this.controller.signal]) : this.controller.signal;
    this.children = new Set(); this.serial = Math.max(0, ...[...state.sections.tool_environments.values()].map(item => Number(/^preparation-(\d+)$/.exec(item.preparation_id)?.[1] ?? 0)));
  }
  catalog() {
    return Object.entries(recipes).map(([id, recipe]) => ({ id, title: recipe.title, aliases: recipe.aliases ?? [], supplies: recipe.wheels ?? [], binaries: recipe.binaries, modules: recipe.modules }));
  }
  snapshot(job) {
    return { preparation_id: job.id, environment_id: job.state === 'ready' ? job.key : undefined, state: job.state, capabilities: job.selected, cache_hit: job.cacheHit ?? false, base_image: this.image, error: job.error, delivery: job.overlay ? 'readonly-python-overlay' : 'current-image', network_authority_changed: false };
  }
  async transition(job, state, error) {
    Object.assign(job, { state, ...(error ? { error: { kind: error.kind ?? 'preparation_failed', message: error.message } } : {}) });
    const value = this.snapshot(job);
    await this.evidence.append('tool_provisioning', value);
    await this.state.add('tool_environments', value);
    this.onEvent({ type: 'tool_provisioning', ...value });
  }
  async request(names) {
    this.signal.throwIfAborted();
    if (!names.length) return { catalog: this.catalog(), state: 'catalog' };
    const selected = selectRecipes(names).sort();
    if (selected.includes(undefined)) return { state: 'unsupported', error: { kind: 'unsupported_recipe', message: 'No approved recipe for one or more capabilities. Arbitrary sources, native builds and install commands are not accepted.' }, catalog: this.catalog() };
    const key = sha256(json({ version, image: this.image, selected, recipes: selected.map(id => recipes[id]), wheels: await this.locks() }));
    const existing = this.jobs.get(key);
    if (existing) return this.snapshot(existing);
    invariant(this.jobs.size < 8, 'At most eight distinct tool requests per run');
    const job = { id: `preparation-${++this.serial}`, key, selected, state: 'queued', controller: new AbortController() };
    this.jobs.set(key, job);
    await this.transition(job, 'queued');
    job.promise = this.queue.then(async () => {
      try { await this.prepare(job); }
      catch (error) { await this.transition(job, job.signal?.aborted || this.signal.aborted ? 'cancelled' : 'failed', error); }
    });
    this.queue = job.promise.catch(() => {});
    return this.snapshot(job);
  }
  async locks() {
    return JSON.parse(await readFile(new URL('../../assets/tool-wheels.lock.json', import.meta.url), 'utf8'));
  }
  async status(id, cancel = false, waitSeconds = 0) {
    const job = [...this.jobs.values()].find(job => job.id === id || job.key === id);
    invariant(job, 'Unknown tool preparation or environment');
    if (cancel && !['ready', 'failed', 'cancelled'].includes(job.state)) job.controller.abort(new Error('Tool preparation cancelled'));
    if (!cancel && waitSeconds && !['ready', 'failed', 'cancelled'].includes(job.state)) {
      await new Promise((resolve, reject) => {
        const finish = error => { clearTimeout(timer); this.signal.removeEventListener('abort', abort); error ? reject(error) : resolve(); };
        const abort = () => finish(this.signal.reason ?? new Error('Run cancelled'));
        const timer = setTimeout(finish, Math.min(10, waitSeconds) * 1000);
        this.signal.addEventListener('abort', abort, { once: true });
        if (this.signal.aborted) abort();
        job.promise.then(() => finish(), finish);
      });
    }
    return this.snapshot(job);
  }
  async child(input, extension, signal, use) {
    const runtime = this.runtimeFactory({ input, image: this.image, runId: this.runId, extension });
    this.children.add(runtime);
    await this.onOwnership(runtime.name); // Persist before Docker create, including uncertain creates.
    try { await runtime.start(signal); return await use(runtime); }
    finally { await runtime.close(); await this.onRelease(runtime.name); this.children.delete(runtime); }
  }
  async probe(runtime, selected, signal) {
    const result = await runtime.execute(['python3', '-c', probeSource(selected)], { signal, timeout: 60000, limit: 65536 });
    if (result.code !== 0 || result.truncated || result.cancelled) return { ready: false, missing: ['probe_failed'], stderr: result.stderr.toString().slice(-2048) };
    return JSON.parse(result.stdout.toString());
  }
  async validatedCache(path, key) {
    try {
      const manifest = JSON.parse((await readRegular(join(path, 'manifest.json'), 1024 * 1024)).toString());
      invariant(manifest.key === key && manifest.image === this.image && manifest.schema_version === version, 'Cache identity mismatch');
      invariant(json(manifest.files) === json(await treeManifest(path)), 'Cache integrity failure');
      return true;
    } catch (error) {
      if (error.code === 'ENOENT') return false;
      error.kind = 'cache_integrity'; throw error;
    }
  }
  async prepare(job) {
    job.signal = AbortSignal.any([this.signal, job.controller.signal, AbortSignal.timeout(5 * 60 * 1000)]);
    const signal = job.signal;
    signal.throwIfAborted();
    await this.transition(job, 'checking');
    const present = await this.probe(this.runtime, job.selected, signal);
    if (present.ready) { signal.throwIfAborted(); await this.transition(job, 'ready'); return; }
    const locks = await this.locks();
    const wheelIds = [...new Set(job.selected.flatMap(id => recipes[id].wheels ?? []))];
    if (!wheelIds.length) {
      const error = new Error(`Current image lacks ${present.missing.join(', ')}; no offline extension recipe supplies this native tool. Main sandbox retained.`);
      error.kind = 'missing_native_capability'; throw error;
    }
    await privateDir(this.cacheRoot);
    const destination = join(this.cacheRoot, job.key);
    if (await this.validatedCache(destination, job.key)) {
      job.overlay = destination; job.cacheHit = true;
      await this.transition(job, 'verifying');
      await this.verify(job, signal);
      signal.throwIfAborted(); await this.transition(job, 'ready'); return;
    }
    invariant(this.allowDownload, 'Verified extension is not cached; offline replay does not download tools');
    // Bound persistent cache growth. Never evict another run's active environment.
    invariant((await readdir(this.cacheRoot)).filter(x => !x.startsWith('.')).length < 16, 'Tool cache is full; remove unused environments before preparing another');
    const stage = await mkdtemp(join(this.cacheRoot, '.prepare-'));
    const empty = await privateDir(join(stage, 'input'));
    try {
      await privateDir(join(stage, 'wheels'));
      await this.transition(job, 'downloading');
      for (const id of wheelIds) {
        const lock = locks[id]; invariant(lock, 'Recipe lacks a locked wheel');
        const data = await fetchWheel(lock, signal, this.fetcher);
        await writeFile(join(stage, 'wheels', lock.filename), data, { mode: 0o600, flag: 'wx' });
      }
      signal.throwIfAborted();
      await this.transition(job, 'installing');
      await this.child(empty, { root: stage, writable: true }, signal, async runtime => {
        const argv = ['python3', '-m', 'pip', 'install', '--no-index', '--no-deps', '--no-compile', '--disable-pip-version-check', '--target', '/opt/ctf-extension/site', ...wheelIds.map(id => `/opt/ctf-extension/wheels/${locks[id].filename}`)];
        const result = await runtime.execute(argv, { signal, timeout: 120000 });
        const log = await this.evidence.artifact(Buffer.concat([result.stdout, result.stderr]), 'text/plain');
        await this.evidence.append('tool_installation', { preparation_id: job.id, image: this.image, wheels: wheelIds.map(id => locks[id]), argv, exit_code: result.code, log });
        invariant(result.code === 0 && !result.cancelled && !result.truncated, 'Offline wheel installation failed');
      });
      job.overlay = stage;
      await this.transition(job, 'verifying');
      await this.verify(job, signal);
      signal.throwIfAborted();
      const manifest = { schema_version: version, key: job.key, image: this.image, capabilities: job.selected, files: await treeManifest(stage) };
      await writeFile(join(stage, 'manifest.json'), json(manifest), { mode: 0o600, flag: 'wx' });
      try { await rename(stage, destination); }
      catch (error) { if (!['EEXIST', 'ENOTEMPTY'].includes(error.code) || !await this.validatedCache(destination, job.key)) throw error; }
      job.overlay = destination;
      signal.throwIfAborted();
      await this.transition(job, 'ready');
    } finally {
      await rm(stage, { recursive: true, force: true });
    }
  }
  async verify(job, signal) {
    const empty = join(job.overlay, 'input');
    await this.child(empty, { root: job.overlay }, signal, async runtime => {
      const result = await this.probe(runtime, job.selected, signal);
      invariant(result.ready, `Tool environment verification failed: ${result.missing.join(', ')}`);
      const check = await runtime.execute(['python3', '-m', 'pip', 'check'], { signal, timeout: 60000 });
      invariant(check.code === 0, `Dependency conflict: ${check.stdout.toString().slice(-2048)}`);
    });
  }
  async execute(id, argv, signal, use) {
    const job = this.jobs.get(id);
    invariant(job?.state === 'ready', 'Tool environment is not ready');
    const combined = signal ? AbortSignal.any([this.signal, signal]) : this.signal;
    combined.throwIfAborted();
    if (job.overlay) invariant(await this.validatedCache(job.overlay, job.key), 'Tool environment cache unavailable');
    const empty = job.overlay ? join(job.overlay, 'input') : await privateDir(join(this.evidence.root, 'tool-input'));
    return this.child(empty, job.overlay ? { root: job.overlay } : undefined, combined, runtime => use(runtime, true));
  }
  async close() {
    this.controller.abort(new Error('Run ended'));
    await Promise.allSettled([...this.jobs.values()].map(job => job.promise));
    const results = await Promise.allSettled([...this.children].map(async runtime => { await runtime.close(); await this.onRelease(runtime.name); this.children.delete(runtime); }));
    const errors = results.filter(x => x.status === 'rejected').map(x => x.reason);
    if (errors.length) throw new AggregateError(errors, 'Tool environment cleanup unconfirmed');
  }
}
