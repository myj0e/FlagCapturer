import { join } from 'node:path';
import { EvidenceStore, readEvents, audit } from '../evidence/store.js';
import { invariant, readRegular, confined, json, atomicWrite } from '../util.js';
import { basename } from 'node:path';
import { snapshot } from '../challenge/workspace.js';
import { RunState } from '../evidence/state.js';
import { ToolRegistry } from '../tools/registry.js';
import { installCore } from '../tools/core.js';
import { Sessions } from '../runtime/sessions.js';
import { DockerRuntime, resolveImage } from '../runtime/docker.js';
import { solve } from './solve.js';
import { ToolProvisioner } from '../provisioning/service.js';
import { installProvisioning } from '../tools/provisioning.js';
export async function diagnose(root) {
  const events = await readEvents(root);
  return {
    integrity: await audit(root),
    status: events.findLast(x => x.type === 'run_finished')?.status ?? 'interrupted',
    tools: events.filter(x => x.type === 'tool_finished').length,
    failures: events.filter(x => ['tool_failed', 'run_failed', 'cleanup_failed'].includes(x.type)),
    prompts: events.filter(x => x.type === 'prompt').map(x => x.artifact),
    completion: events.findLast(x => x.type === 'run_completed'),
    metrics: events.filter(x => x.type === 'assistant_message').map(x => x.message.usage).filter(Boolean)
  };
}
export async function report(root) {
  const result = await diagnose(root),
    events = await readEvents(root),
    store = new EvidenceStore(root);
  const candidates = events.filter(x => x.type === 'state_record' && x.section === 'candidates').map(x => x.id);
  const text = `# CTF run report\n\nStatus: ${result.status}\n\nTool calls: ${result.tools}\n\nEvidence integrity: ${result.integrity.ok ? 'passed' : 'failed'}\n\nCandidate IDs: ${candidates.join(', ') || 'none'}\n\nCandidates are model-submitted; platform correctness is not measured. Exact flags remain in private evidence and writeup.md.\n\n## Failures\n\n${result.failures.map(x => `- ${x.type}: ${x.error ?? json(x.errors)}`).join('\n') || 'None recorded.'}\n`;
  await store.document('report.md', text);
  return {
    path: join(root, 'report.md'),
    ...result
  };
}
/** Reproduction uses tool request artifacts and saved scripts in recorded order; never host execution. */
export async function replay(root, {
  runtimeFactory = config => new DockerRuntime(config),
  resolveImage: resolve = resolveImage,
  image,
  signal,
  toolCacheRoot,
  runsRoot = 'runs'
}) {
  invariant((await audit(root)).ok, 'Cannot replay corrupt evidence');
  const source = new EvidenceStore(root),
    events = await readEvents(root);
  const recordedImage = events.find(event => event.type === 'run_started')?.identity?.image;
  const pinned = await resolve(image ?? recordedImage);
  const evidence = await EvidenceStore.create(runsRoot);
  const workspace = await snapshot(join(root, 'challenge'), join(evidence.root, 'challenge'));
  const runtime = runtimeFactory({
    input: workspace.input,
    image: pinned,
    runId: basename(evidence.root)
  });
  const state = new RunState(evidence, {
    run_id: basename(evidence.root),
    image: pinned,
    input_files: workspace.manifest.files
  });
  const sessions = new Sessions(runtime, evidence),
    registry = new ToolRegistry(state, evidence);
  const allowed = new Set(['command_run', 'script_save', 'script_run', 'script_start', 'session_start', 'session_send', 'session_read', 'session_close', 'workflow_run', 'workflow_environment', 'artifact_export', 'asset_capture', 'tool_exec']);
  const sessionIds = new Map(),
    scriptIds = new Map(),
    assetIds = new Map(),
    outcomes = [];
  const receipt = {
    schema_version: 1,
    run_id: basename(evidence.root),
    container: runtime.name,
    pending: true,
    tool_containers: []
  };
  await evidence.document('recovery.json', receipt);
  const provisioner = new ToolProvisioner({ runtime, evidence, state, image: pinned, runId: basename(evidence.root), signal, cacheRoot: toolCacheRoot,
    runtimeFactory, allowDownload: false,
    onOwnership: async name => { receipt.tool_containers.push(name); await evidence.document('recovery.json', receipt); },
    onRelease: async name => { receipt.tool_containers = receipt.tool_containers.filter(x => x !== name); await evidence.document('recovery.json', receipt); }
  });
  await evidence.append('run_started', {
    identity: state.identity,
    mode: 'replay',
    source_run: root
  });
  let failure;
  try {
    await runtime.start(signal);
    await installCore(registry, {
      workspace,
      runtime,
      sessions
    });
    installProvisioning(registry, { provisioner, workspace });
    for (let index = 0; index < events.length; index++) {
      const event = events[index];
      if (event.type !== 'tool_started') continue;
      const request = JSON.parse((await source.read(event.request.artifact)).toString());
      if (!allowed.has(request.name)) continue;
      const args = {
        ...request.args
      };
      if (request.name === 'tool_exec') {
        const original = events.find(e => e.type === 'tool_provisioning' && e.state === 'ready' && e.environment_id === args.environment_id);
        invariant(original, 'Recorded tool environment identity is missing');
        const requested = await provisioner.request(original.capabilities);
        const job = [...provisioner.jobs.values()].find(x => x.id === requested.preparation_id);
        invariant(job, 'Cannot prepare recorded tool environment');
        await job.promise;
        const ready = await provisioner.status(job.id);
        invariant(ready.state === 'ready', ready.error?.message ?? 'Recorded tool environment is unavailable');
        args.environment_id = ready.environment_id;
        if (args.inputs) args.inputs = args.inputs.map(input => ({ ...input, asset_id: assetIds.get(input.asset_id) ?? input.asset_id }));
      }
      for (const field of ['asset_id']) if (args[field]) args[field] = assetIds.get(args[field]) ?? args[field];
      for (const field of ['asset_ids', 'parent_asset_ids']) if (args[field]) args[field] = args[field].map(id => assetIds.get(id) ?? id);
      if (args.session_id) {
        invariant(sessionIds.has(args.session_id), 'Cannot map recorded session');
        args.session_id = sessionIds.get(args.session_id);
      }
      if (args.script_id) {
        invariant(scriptIds.has(args.script_id), 'Cannot map recorded script');
        args.script_id = scriptIds.get(args.script_id);
      }
      const result = await registry.invoke(request.name, args, signal);
      const payload = JSON.parse((await evidence.read(result.details.result.artifact)).toString());
      const original = events.slice(index + 1).find(next => ['tool_finished', 'tool_failed'].includes(next.type) && next.request.artifact === event.request.artifact);
      if (original?.type === 'tool_finished') {
        const previous = JSON.parse((await source.read(original.result.artifact)).toString());
        if (previous.session_id && payload.session_id) sessionIds.set(previous.session_id, payload.session_id);
        if (request.name === 'script_save') scriptIds.set(previous.id, payload.id);
        if (request.name === 'asset_capture') assetIds.set(previous.id, payload.id);
        if (previous.asset_id && payload.asset_id) assetIds.set(previous.asset_id, payload.asset_id);
        for (let i = 0; i < (previous.output_assets?.length ?? 0); i++) {
          invariant(payload.output_assets?.[i], 'Replay did not reproduce a derived asset');
          assetIds.set(previous.output_assets[i].id, payload.output_assets[i].id);
        }
      }
      outcomes.push({
        sequence: event.sequence,
        tool: request.name,
        result: result.details.result,
        is_error: result.isError === true
      });
    }
  } catch (error) {
    failure = error;
    await evidence.append('run_failed', {
      error: error.message
    });
  } finally {
    const errors = [];
    for (const close of [() => provisioner.close(), () => sessions.closeAll(), () => runtime.close()]) {
      try {
        await close();
      } catch (error) {
        errors.push(error);
      }
    }
    if (errors.length) {
      await evidence.append('cleanup_failed', {
        errors: errors.map(error => error.message)
      });
      failure = new AggregateError([...(failure ? [failure] : []), ...errors], 'Replay cleanup unconfirmed');
    } else {
      await evidence.document('recovery.json', { ...receipt, pending: false });
    }
    await evidence.append('run_finished', {
      status: errors.length ? 'cleanup_failed' : signal?.aborted ? 'cancelled' : failure || outcomes.some(x => x.is_error) ? 'failed' : 'replayed'
    });
  }
  if (failure) throw failure;
  await evidence.append('replay_finished', {
    source_run: root,
    operations: outcomes.length
  });
  return {
    run_dir: evidence.root,
    ok: !outcomes.some(x => x.is_error),
    replayed: outcomes,
    limitation: 'Re-execution is not equivalence proof. Network exchanges, model decisions and candidate submissions are not replayed.'
  };
}
export async function evaluate(datasetPath, options, dependencies) {
  const dataset = JSON.parse((await readRegular(datasetPath, 1024 * 1024)).toString());
  invariant(dataset.schema_version === 1 && Array.isArray(dataset.challenges) && dataset.challenges.length > 0 && dataset.challenges.length <= 1000, 'Expected answer-free v1 dataset');
  invariant(!JSON.stringify(dataset).match(/"(?:answer|expected_flag|flag|solution)"\s*:/i), 'Dataset must not contain answer fields');
  for (const row of dataset.challenges) invariant(typeof row.workspace === 'string' && Object.keys(row).every(k => ['workspace', 'id'].includes(k)), 'Invalid dataset row');
  const results = [];
  for (const row of dataset.challenges) {
    options.signal?.throwIfAborted();
    const result = await solve({
      ...options,
      workspace: row.workspace
    }, dependencies);
    results.push({
      id: row.id ?? row.workspace,
      ...result
    });
    if (options.output) await atomicWrite(options.output, json({
      schema_version: 1,
      results,
      correctness_measured: false
    }));
    if (result.cleanup_error) break;
  }
  return {
    schema_version: 1,
    results,
    completed: results.filter(x => x.status === 'completed').length,
    correctness_measured: false
  };
}
