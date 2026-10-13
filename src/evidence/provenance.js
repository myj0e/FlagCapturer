import { invariant } from '../util.js';

/** Execution identity is recorded by the controller, never reconstructed by a summarizer. */
export async function recordExecution(registry, argv, result, extra = {}) {
  const invocation = registry.invocations.getStore();
  const script = invocation?.args.script_id ? registry.state.select('scripts', [invocation.args.script_id])[0] : [...registry.state.sections.scripts.values()].find(item => argv.includes(item.path));
  const args = invocation?.args ?? {};
  const workflowInputs = invocation?.name === 'workflow_run' ? args.asset_ids ?? (args.asset_id ? [args.asset_id] : [...registry.state.sections.assets.values()].filter(a => a.origin === 'input' && a.path === args.path).map(a => a.id)) : [];
  const inputs = extra.input_asset_ids ?? args.input_asset_ids ?? workflowInputs;
  registry.state.select('assets', inputs);
  return registry.state.add('executions', {
    ...extra, argv: [...argv], image: registry.state.identity.image,
    request: invocation?.request ?? null, tool: invocation?.name ?? null,
    script_id: script?.id ?? null, script_source: script?.source_artifact ?? null,
    input_asset_ids: inputs, input_binding: 'declared inputs; not proof of complete filesystem dependencies',
    exit_code: result.exit_code, timed_out: !!result.timed_out, cancelled: !!result.cancelled,
    stdout_artifact: result.stdout_artifact, stderr_artifact: result.stderr_artifact,
    succeeded: result.exit_code === 0 && !result.timed_out && !result.cancelled
  });
}

export function generation(registry, parents, executionId, binding = 'declared') {
  registry.state.select('assets', parents);
  if (executionId) registry.state.select('executions', [executionId]);
  return {
    schema_version: 1, input_asset_ids: [...parents], execution_id: executionId ?? null,
    capture_request: registry.invocations.getStore()?.request ?? null,
    producer_binding: executionId ? binding : 'unknown',
    limitation: 'Recorded generation provenance is not proof of semantic correctness or safe replay.'
  };
}

/** Select goals with the LLM; compute and validate the dependency graph deterministically. */
export function recoveryPlan(state, assetIds, { reason = 'context_summary', sourceRevision = state.revision } = {}) {
  const roots = [...new Set(assetIds)];
  state.select('assets', roots);
  const visited = new Set(), visiting = new Set(), steps = [], unresolved = [];
  function visit(id) {
    if (visited.has(id)) return;
    invariant(!visiting.has(id), 'Cyclic asset generation graph');
    visiting.add(id);
    const asset = state.select('assets', [id])[0];
    const parents = asset.generation?.input_asset_ids ?? asset.parents ?? [];
    for (const parent of parents) visit(parent);
    const executionId = asset.generation?.execution_id;
    if (executionId) state.select('executions', [executionId]);
    const available = asset.origin === 'input' || !!asset.artifact;
    steps.push({ asset_id: id, kind: asset.origin === 'input' ? 'original_input' : available ? 'restore_artifact' : 'agent_required',
      depends_on: [...parents], sha256: asset.sha256, bytes: asset.bytes,
      artifact: asset.artifact ?? null, path: asset.path ?? asset.captured_path ?? null,
      execution_id: executionId ?? null, producer_binding: asset.generation?.producer_binding ?? 'unknown',
      automatic_replay: false });
    if (!available) unresolved.push({ asset_id: id, reason: 'No saved bytes; agent must inspect the generation record before rebuilding.' });
    visiting.delete(id);
    visited.add(id);
  }
  roots.forEach(visit);
  return { schema_version: 1, source_revision: sourceRevision, reason, goal_asset_ids: roots, steps, unresolved,
    policy: 'Restore verified saved bytes first. Recorded commands are references, never automatically replayed. Processes, sessions and tool environments must be recreated.' };
}

export function recoveryGoals(state) {
  const snapshot = [...state.sections.context_snapshots.values()].at(-1);
  // Include work produced after the last summary, including a stop before the next ten-turn refresh.
  const selected = [...(snapshot?.memory.references.map(ref => ref.asset_id) ?? []), ...(snapshot?.memory.recovery_steps ?? []).flatMap(step => step.asset_ids)];
  const assets = [...state.sections.assets.values()];
  const recent = !snapshot ? assets : Number.isInteger(snapshot.asset_count) ? assets.slice(snapshot.asset_count)
    : assets.filter(asset => asset.time >= snapshot.time); // Conservative compatibility with older snapshots.
  return [...new Set([...selected, ...assets.filter(asset => asset.origin === 'input').map(asset => asset.id), ...recent.map(asset => asset.id)])];
}
