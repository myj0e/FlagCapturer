import { s } from './schema.js';
import { invariant, sha256 } from '../util.js';
import { generation } from '../evidence/provenance.js';
export const MAX_ASSET_BYTES = 16 * 1024 * 1024;
export function confinedWorkPath(path) {
  invariant(typeof path === 'string' && /^\/work\/[a-zA-Z0-9._/-]+$/.test(path) && !path.split('/').includes('..') && !path.startsWith('/work/.control'), 'Expected confined /work path');
  return path;
}
export async function installAssets(registry, { workspace, runtime, inputBytes }) {
  const { state, evidence } = registry;
  for (const asset of state.sections.assets.values()) if (asset.origin === 'input') invariant(workspace.manifest.files.some(file => file.path === asset.path && file.sha256 === asset.sha256 && file.bytes === asset.bytes), 'Restored input asset differs from the authorized manifest');
  async function add(bytes, metadata) {
    invariant(bytes.length <= MAX_ASSET_BYTES, 'Asset exceeds 16 MiB');
    const record = await state.add('assets', { ...metadata, bytes: bytes.length, sha256: sha256(bytes),
      artifact: await evidence.artifact(bytes), completeness: 'exact captured bytes; semantic completeness is not established' });
    return record;
  }
  // Original attachment records keep their manifest hash and are checked on every use.
  for (const file of workspace.manifest.files) if (![...state.sections.assets.values()].some(a => a.origin === 'input' && a.path === file.path && a.sha256 === file.sha256)) await state.add('assets', {
    origin: 'input', path: file.path, sha256: file.sha256, bytes: file.bytes,
    parents: [], transformation: null, completeness: 'authorized original attachment'
  });
  async function read(id) {
    const record = state.select('assets', [id])[0];
    const bytes = record.origin === 'input' ? await inputBytes(record.path) : await evidence.read(record.artifact.artifact);
    invariant(sha256(bytes) === record.sha256, 'Asset integrity failure');
    return { record, bytes };
  }
  async function materialize(id, signal) {
    const { record, bytes } = await read(id);
    if (record.origin === 'input') return { ...record, sandbox_path: `/challenge/${record.path}` };
    const path = `/work/.assets/${randomUUID()}`;
    await runtime.write(path, bytes, signal);
    await state.add('asset_locations', { asset_id: id, path });
    return { ...record, sandbox_path: path };
  }
  async function capture(path, parents, transformation, signal, expected) {
    confinedWorkPath(path);
    invariant(parents.length > 0, 'Asset lineage is required');
    state.select('assets', parents);
    const bytes = await runtime.export(path, signal, { maxBytes: MAX_ASSET_BYTES });
    invariant(!expected || expected.sha256 === sha256(bytes) && expected.bytes === bytes.length, 'Output asset integrity failure');
    return add(bytes, { origin: 'derived', captured_path: path, parents, transformation,
      generation: generation(registry, parents, transformation.execution_id, transformation.producer_binding) });
  }
  registry.define('asset_read', 'Read a verified asset byte range. Full files remain outside model context.', s.object({
    asset_id: s.text(100), offset: s.int(0, 32 * 1024 * 1024), length: s.int(1, 4096), encoding: s.enum(['utf8', 'hex'])
  }), async ({ asset_id, offset, length, encoding }) => {
    const { record, bytes } = await read(asset_id);
    invariant(offset <= bytes.length, 'Offset exceeds asset');
    const part = bytes.subarray(offset, offset + length);
    const source = await state.add('sources', { artifact: record.artifact ?? await evidence.artifact(bytes),
      offset, length: part.length, sha256: sha256(part), encoding });
    return { asset_id, data: part.toString(encoding), source_ref: source.id,
      coverage: { offset, length: part.length, source_bytes: bytes.length, truncated: offset > 0 || offset + part.length < bytes.length } };
  }, { observe: true });
  registry.define('asset_materialize', 'Restore verified saved asset bytes to a generated sandbox path (originals remain at /challenge). Use this for older files not selected by the resume plan. Does not rerun its producer.', s.object({ asset_id: s.text(100) }),
    ({ asset_id }, signal) => materialize(asset_id, signal));
  registry.define('asset_capture', 'Capture a script-produced /work file as an immutable asset with declared lineage. Declared transformations are model reports.', s.object({
    path: s.text(512), parent_asset_ids: s.array(s.text(100), 32), transformation: s.text(2048, { minLength: 1 }), display_name: s.text(120, { minLength: 1 }), execution_id: s.text(100)
  }, ['path', 'parent_asset_ids', 'transformation']), ({ path, parent_asset_ids, transformation, display_name, execution_id }, signal) => capture(path, parent_asset_ids, { description: transformation, display_name, execution_id, producer_binding: 'model_declared', controller_proven: false }, signal), { observe: true });
  return { read, materialize, capture, addSkill: (bytes, resource) => add(bytes, { origin: 'skill', parents: [], transformation: { description: '已登记 Skill 脚本', source_name: resource.key }, skill_resource: resource.key, generation: { kind: 'captured_skill_resource', sha256: resource.artifact.sha256 } }) };
}
import { randomUUID } from 'node:crypto';
