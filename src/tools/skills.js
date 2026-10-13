import { s } from './schema.js';
import { invariant } from '../util.js';

export function installSkills(registry, catalog, notify = () => {}) {
  registry.define('skill_list', 'List registered Pi skills. Read relevant instructions with skill_read; references load on demand.', s.object({}), () => ({ skills: catalog.list(), diagnostics: catalog.diagnostics }));
  registry.define('skill_read', 'Read only captured skill instructions or referenced resources. name selects SKILL.md; path may be an advertised absolute path, resource key, or a path relative to name. offset/limit are character ranges, not bytes.',
    s.object({ name: s.text(100), path: s.text(2048), offset: s.int(0, 1024 * 1024), limit: s.int(1, 24000) }, []), async args => {
      const result = await catalog.read(args);
      await catalog.recordUse(result.path, 'tool');
      notify({ type: 'skill_loaded', name: args.name ?? result.resource, resource: result.resource, truncated: result.next_offset !== null });
      return result;
    });
  registry.define('skill_materialize', 'Copy a captured skill helper script into a generated /work path. Does not execute it or install dependencies; use normal sandbox execution tools and declare input assets.',
    s.object({ path: s.text(2048) }), async ({ path }, signal) => {
      const resource = catalog.resources.get(catalog.keyFor(path));
      invariant(resource && /\.(py|js|mjs|sh|sage)$/.test(resource.key), 'Expected a registered helper script');
      const bytes = await catalog.evidence.read(resource.artifact.artifact);
      const asset = await registry.assets.addSkill(bytes, resource);
      const materialized = await registry.assets.materialize(asset.id, signal);
      await catalog.recordUse(resource.path, 'materialize');
      return { asset_id: asset.id, sandbox_path: materialized.sandbox_path, sha256: resource.artifact.sha256,
        execution: 'Use command_run or script tools after checking prerequisites; this operation has not executed the helper.' };
    });
}
