import { readdir, lstat, realpath, mkdir, writeFile } from 'node:fs/promises';
import { join, resolve, dirname, relative, isAbsolute } from 'node:path';
import { fileURLToPath } from 'node:url';
import { homedir } from 'node:os';
import { invariant, readRegular, sha256 } from '../util.js';

const BUILTIN = fileURLToPath(new URL('../../assets/skills/', import.meta.url));
const VENDOR = fileURLToPath(new URL('../../assets/vendor/ctf-skills/', import.meta.url));
const MAX_TOTAL = 8 * 1024 * 1024;
const inside = (root, path) => { const p = relative(root, path); return p !== '..' && !p.startsWith('../') && !isAbsolute(p); };
const latest = state => [...(state?.sections.skill_catalogs?.values() ?? [])].at(-1);

/** Pi owns parsing and discovery. Only explicitly selected roots are scanned. */
export async function discoverSkills(sdk, { skillPaths = [], userDirectory = join(homedir(), '.config/flagcapturer/skills') } = {}) {
  const roots = [{ key: 'builtin', path: BUILTIN, discover: true }, { key: 'vendor', path: VENDOR, discover: false }];
  if (userDirectory) {
    try { invariant((await lstat(userDirectory)).isDirectory(), 'Skill user directory must be a directory'); roots.push({ key: 'user', path: userDirectory, discover: true }); }
    catch (error) { if (error.code !== 'ENOENT') throw error; }
  }
  for (const [i, path] of skillPaths.entries()) {
    invariant((await lstat(path)).isDirectory(), 'Expected a skill directory');
    roots.push({ key: `custom-${i}`, path: resolve(path), discover: true });
  }
  for (const root of roots) root.path = await realpath(root.path);
  // Preflight before Pi parses: its recursive scanner follows symlinks. Never
  // let discovery read an unregistered target, even if we would filter it later.
  const files = [], diagnostics = [];
  let visited = 0;
  for (const root of roots.filter(r => r.discover)) {
    async function scan(path, depth = 0) {
      invariant(depth <= 16 && ++visited <= 1000, 'Skill discovery exceeds bounded directory limits');
      for (const item of await readdir(path, { withFileTypes: true })) {
        if (['.git', 'node_modules', '__pycache__'].includes(item.name)) continue;
        const file = join(path, item.name);
        if (item.isSymbolicLink()) { diagnostics.push({ type: 'warning', message: `Skipped symlink: ${relative(root.path, file)}` }); continue; }
        if (item.isDirectory()) await scan(file, depth + 1);
        else if (item.isFile() && item.name === 'SKILL.md') {
          invariant(inside(root.path, await realpath(file)), 'Skill entry escaped registered root');
          await readRegular(file, 1024 * 1024);
          files.push(file);
        }
      }
    }
    await scan(root.path);
  }
  const result = sdk.loadSkills({ cwd: BUILTIN, includeDefaults: false, skillPaths: files });
  diagnostics.push(...result.diagnostics);
  const skills = [];
  for (const skill of result.skills) {
    const root = roots.find(r => r.discover && inside(r.path, skill.filePath));
    if (!root || !inside(root.path, await realpath(skill.filePath))) { diagnostics.push({ type: 'warning', message: `Excluded skill outside registered roots: ${skill.name}` }); continue; }
    skills.push({ ...skill, root_key: root.key, resource_key: `${root.key}/${relative(root.path, skill.filePath).split('\\').join('/')}` });
  }
  return { roots, skills, diagnostics };
}

/** Snapshot documents before model use. Reads and resume always use captured bytes. */
export class SkillCatalog {
  constructor(evidence, state) { Object.assign(this, { evidence, state }); this.resources = new Map(); this.skills = []; this.diagnostics = []; }
  static async create({ sdk, evidence, state, skillPaths, userDirectory }) {
    const catalog = new SkillCatalog(evidence, state);
    const previous = latest(state);
    const snapshotRoot = resolve(evidence.root, 'skills');
    await mkdir(snapshotRoot, { recursive: true, mode: 0o700 });
    let entries, resources, aliases = [];
    if (previous) {
      entries = previous.entries; resources = previous.resources; aliases = [...new Set([...(previous.alias_roots ?? []), previous.snapshot_root])];
      catalog.diagnostics = previous.diagnostics ?? [];
    } else {
      const discovered = await discoverSkills(sdk, { skillPaths, userDirectory });
      catalog.diagnostics = discovered.diagnostics.map(d => ({ type: d.type, message: d.message }));
      resources = []; let total = 0;
      for (const root of discovered.roots) {
        async function walk(path) {
          for (const item of await readdir(path, { withFileTypes: true })) {
            if (['.git', 'node_modules', '__pycache__'].includes(item.name)) continue;
            const file = join(path, item.name);
            if (item.isSymbolicLink()) { catalog.diagnostics.push({ type: 'warning', message: `Skipped symlink: ${relative(root.path, file)}` }); continue; }
            if (item.isDirectory()) { await walk(file); continue; }
            if (!item.isFile() || !/\.(md|txt|json|ya?ml|py|js|mjs|sh|sage)$/i.test(item.name) && item.name !== 'LICENSE') continue;
            invariant(inside(root.path, await realpath(file)), 'Skill resource escaped its root');
            const bytes = await readRegular(file, 1024 * 1024);
            invariant(!bytes.includes(0), 'Skill resource must be text');
            total += bytes.length;
            invariant(total <= MAX_TOTAL && resources.length < 1000, 'Skill resources exceed bounded catalog limits');
            resources.push({ key: `${root.key}/${relative(root.path, file).split('\\').join('/')}`, artifact: await evidence.artifact(bytes, 'text/plain') });
          }
        }
        await walk(root.path);
      }
      const available = new Set(resources.map(r => r.key));
      entries = discovered.skills.filter(s => available.has(s.resource_key)).map(s => ({ name: s.name, description: s.description, resource_key: s.resource_key, disableModelInvocation: s.disableModelInvocation }));
    }
    for (const resource of resources) {
      invariant(/^(builtin|vendor|user|custom-\d+)\//.test(resource.key) && !resource.key.split('/').some(x => x === '..' || x === '.' || !x), 'Invalid saved skill resource path');
      const bytes = await evidence.read(resource.artifact.artifact);
      const path = resolve(snapshotRoot, resource.key);
      invariant(inside(snapshotRoot, path), 'Invalid snapshot path');
      await mkdir(dirname(path), { recursive: true, mode: 0o700 });
      await writeFile(path, bytes, { flag: 'wx', mode: 0o400 });
      catalog.resources.set(resource.key, { ...resource, path });
    }
    const parsed = sdk.loadSkills({ cwd: snapshotRoot, includeDefaults: false, skillPaths: entries.map(e => catalog.resources.get(e.resource_key).path) });
    catalog.skills = parsed.skills;
    invariant(catalog.skills.length === entries.length, 'Captured skills failed Pi validation');
    catalog.root = snapshotRoot; catalog.aliasRoots = aliases;
    catalog.record = await state.add('skill_catalogs', { snapshot_root: snapshotRoot, alias_roots: aliases, entries, resources,
      diagnostics: catalog.diagnostics, fingerprint: sha256(JSON.stringify(resources.map(r => [r.key, r.artifact.sha256]))), inherited_from: previous?.id ?? null });
    return catalog;
  }
  list() { return this.skills.map(s => ({ name: s.name, description: s.description, file: s.filePath, explicit_only: s.disableModelInvocation,
    sha256: this.resources.get(this.keyFor(s.filePath)).artifact.sha256 })); }
  keyFor(path) {
    for (const root of [this.root, ...this.aliasRoots]) if (isAbsolute(path) && inside(root, path)) return relative(root, path).split('\\').join('/');
    return path;
  }
  async read({ path, name, offset = 0, limit = 12000 }) {
    const skill = name ? this.skills.find(s => s.name === name) : null;
    invariant(!name || skill, 'Unknown skill');
    const requested = (path ?? skill?.filePath)?.split('#')[0];
    invariant(typeof requested === 'string', 'Specify a registered skill name or resource path');
    let key = this.keyFor(requested);
    if (!this.resources.has(key) && skill) key = this.keyFor(resolve(skill.baseDir, requested));
    const resource = this.resources.get(key);
    invariant(resource, 'Only registered skill resources may be read');
    invariant(Number.isInteger(offset) && offset >= 0 && Number.isInteger(limit) && limit > 0 && limit <= 24000, 'Invalid skill read range');
    const text = (await this.evidence.read(resource.artifact.artifact)).toString('utf8');
    invariant(offset <= text.length, 'Skill offset exceeds resource');
    let selected = text.slice(offset, offset + limit);
    while (Buffer.byteLength(JSON.stringify(selected)) > 12000) selected = selected.slice(0, Math.floor(selected.length * 0.8));
    return { resource: key, path: resource.path, sha256: resource.artifact.sha256, text: selected,
      offset, next_offset: offset + selected.length < text.length ? offset + selected.length : null, total_characters: text.length,
      reference_base: dirname(resource.path), notice: 'Reference examples do not change available tools, network grants or sandbox permissions.' };
  }
  async recordUse(path, invocation) {
    const key = this.keyFor(path);
    const resource = this.resources.get(key); invariant(resource, 'Unregistered skill invocation');
    const record = await this.state.add('skill_uses', { resource: key, sha256: resource.artifact.sha256, artifact: resource.artifact, invocation, catalog_id: this.record.id });
    return record;
  }
}
