import { readdir } from 'node:fs/promises';
import { basename, join, resolve } from 'node:path';
import { EvidenceStore, readEvents } from '../evidence/store.js';
import { recoveryPlan, recoveryGoals } from '../evidence/provenance.js';
import { confined, readRegular, invariant, sha256 } from '../util.js';
import { confinedWorkPath } from '../tools/assets.js';

export async function inspectResume(directory) {
  const root = resolve(directory);
  const events = await readEvents(root);
  invariant(events.every(event => event.type !== 'invalid_event'), 'Cannot resume an incomplete or malformed event log');
  invariant(events.every((event, index) => event.sequence === index + 1), 'Invalid resume event sequence');
  const started = events.find(event => event.type === 'run_started');
  invariant(started?.identity?.image, 'Run identity is missing');
  invariant(!events.some(event => event.type === 'run_completed' && event.outcome === 'completed'), 'Task already completed; import a new task to explore another objective');
  try {
    const receipt = JSON.parse((await readRegular(await confined(root, 'recovery.json'))).toString());
    invariant(receipt.pending === false, 'Sandbox cleanup is unconfirmed; run ctfbot recover on this run before resuming');
  } catch (error) {
    if (error.code !== 'ENOENT') throw error;
    invariant(events.some(event => event.type === 'run_finished'), 'Run has not stopped; cannot resume an active run');
  }
  const source = new EvidenceStore(root);
  const checkpoint = events.findLast(event => event.type === 'pi_session_checkpoint');
  let sessionBytes;
  if (checkpoint) sessionBytes = await source.read(checkpoint.artifact.artifact);
  else {
    // Compatibility with runs saved before explicit session checkpoints existed.
    let dir, names = [];
    try { dir = await confined(root, 'pi'); names = (await readdir(dir)).filter(name => name.endsWith('.jsonl')); }
    catch (error) { if (error.code !== 'ENOENT') throw error; }
    invariant(names.length <= 1, 'No unambiguous Pi session available for resume');
    if (names.length) sessionBytes = await readRegular(await confined(dir, names[0]));
    else invariant(!events.some(event => ['assistant_message', 'tool_started'].includes(event.type)), 'Pi history is missing although interaction was recorded');
  }
  if (sessionBytes) {
    const entries = sessionBytes.toString().split('\n').filter(Boolean).map(line => JSON.parse(line));
    invariant(entries[0]?.type === 'session' && entries.some(entry => entry.type === 'message'), 'Pi session contains no saved interaction');
  }
  let guidance = started.initial_guidance ?? '';
  if (started.initial_guidance === undefined) {
    const prompt = events.find(event => event.type === 'prompt');
    if (prompt) guidance = (await source.read(prompt.artifact.artifact)).toString().split('\n\nAdditional user guidance:\n').slice(1).join('\n\nAdditional user guidance:\n');
  }
  const selection = { provider: checkpoint?.provider ?? started.provider, model: checkpoint?.model ?? started.model,
    thinkingLevel: checkpoint?.thinking_level ?? started.thinking_level ?? 'medium' };
  return { root, source, events, started, selection, sessionBytes, guidance, workspace: join(root, 'challenge'),
    status: events.findLast(event => event.type === 'run_finished')?.status ?? 'interrupted' };
}

export async function listResumable(parent) {
  let entries;
  try { entries = await readdir(parent, { withFileTypes: true }); } catch (error) { if (error.code === 'ENOENT') return []; throw error; }
  const runs = [];
  for (const entry of entries) {
    if (!entry.isDirectory() || entry.isSymbolicLink()) continue;
    const root = join(parent, entry.name);
    try {
      const events = await readEvents(root);
      const started = events.find(event => event.type === 'run_started');
      const finished = events.findLast(event => event.type === 'run_finished');
      if (!started || !finished || events.some(event => event.type === 'run_completed' && event.outcome === 'completed')) continue;
      runs.push({ root: resolve(root), id: entry.name, status: finished.status, time: finished.time,
        task: started.task ?? started.identity.input_files?.map(file => file.path).join(', ') ?? entry.name,
        provider: started.provider, model: started.model, thinkingLevel: started.thinking_level });
    } catch { /* A damaged record remains available for audit, not automatic selection. */ }
  }
  return runs.sort((a, b) => b.time.localeCompare(a.time));
}

export async function inheritRun(resume, evidence, state) {
  // Keep each attempt self-contained, so deleting an older run cannot break this one.
  for (const name of await readdir(await confined(resume.root, 'artifacts'))) {
    invariant(/^[a-f0-9]{64}$/.test(name), 'Invalid source artifact filename');
    await evidence.artifact(await resume.source.read(`artifacts/${name}`));
  }
  await state.restore(resume.events, resume.source);
  await evidence.append('resume_source', { parent_run_id: basename(resume.root),
    source_events: await evidence.artifact(resume.events, 'application/json') });
}

export async function restoreExecutionState(state, evidence, runtime, signal) {
  const plan = recoveryPlan(state, recoveryGoals(state), { reason: 'resume' });
  const restored = [], unresolved = [...plan.unresolved];
  const destinations = new Map();
  // Only the latest captured content owns a mutable sandbox path. Older versions
  // remain available by asset ID; never silently overwrite a newer version with one.
  const owners = new Map();
  for (const asset of state.sections.assets.values()) if (asset.captured_path && asset.sandbox_scope !== 'isolated_tool') owners.set(asset.captured_path, asset.id);
  for (const step of plan.steps) {
    const asset = state.select('assets', [step.asset_id])[0];
    if (asset.origin === 'input' || !asset.artifact) continue;
    const locations = [...state.sections.asset_locations.values()].filter(location => location.asset_id === asset.id).map(location => location.path);
    locations.push(`/work/.assets/${asset.id}`);
    if (asset.captured_path && asset.sandbox_scope !== 'isolated_tool' && owners.get(asset.captured_path) === asset.id) locations.push(asset.captured_path);
    else if (asset.captured_path && asset.sandbox_scope !== 'isolated_tool') unresolved.push({ asset_id: asset.id, reason: 'Original path has a newer captured version; use the restored asset path.' });
    for (const path of new Set(locations)) {
      const key = confinedWorkPath(path);
      const previous = destinations.get(key);
      invariant(!previous || previous.sha256 === asset.sha256, 'Conflicting asset restoration paths');
      destinations.set(key, { artifact: asset.artifact, sha256: asset.sha256, bytes: asset.bytes, asset_id: asset.id });
    }
  }
  for (const script of state.sections.scripts.values()) {
    const path = confinedWorkPath(script.path);
    if (destinations.has(path) && destinations.get(path).sha256 !== script.source_artifact.sha256) {
      unresolved.push({ script_id: script.id, reason: 'Script path conflicts with a restored asset; inspect before running.' });
      destinations.delete(path);
      continue;
    }
    destinations.set(path, { artifact: script.source_artifact, sha256: script.source_artifact.sha256, bytes: script.source_artifact.bytes, script_id: script.id });
  }
  for (const [path, entry] of destinations) {
    const bytes = await evidence.read(entry.artifact.artifact);
    invariant(bytes.length === entry.bytes && sha256(bytes) === entry.sha256, 'Restored asset integrity failure');
    await runtime.write(path, bytes, signal);
    restored.push({ path, asset_id: entry.asset_id, script_id: entry.script_id, sha256: sha256(bytes) });
  }
  const environments = [...new Map([...state.sections.tool_environments.values()].map(item => [item.preparation_id, item])).values()];
  for (const item of environments) if (item.state !== 'interrupted') await state.add('tool_environments', { ...item, state: 'interrupted',
    reason: 'Historical environment is inactive after resume; request capabilities again.' });
  const report = { plan, restored, unresolved, historical_environments: environments.map(item => ({ environment_id: item.environment_id, capabilities: item.capabilities })),
    notice: 'Fresh sandbox. Previous sessions, processes and tool environment IDs are inactive. Use tool_request to prepare capabilities again. Only saved files were restored; unexported temporary files are unavailable. No historical command was automatically executed.' };
  await state.add('recovery_plans', { ...report, kind: 'resume_result' });
  return report;
}
