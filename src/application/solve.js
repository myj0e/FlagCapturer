import { basename, join } from 'node:path';
import { preview, snapshot } from '../challenge/workspace.js';
import { EvidenceStore } from '../evidence/store.js';
import { RunState } from '../evidence/state.js';
import { DockerRuntime, resolveImage } from '../runtime/docker.js';
import { Sessions } from '../runtime/sessions.js';
import { ToolRegistry } from '../tools/registry.js';
import { installCore } from '../tools/core.js';
import { preparePi } from '../pi/session.js';
import { loadGrant, NetworkBroker } from '../runtime/network.js';
import { installOptional } from '../tools/optional.js';
import { createProgressReader } from './progress.js';
import { invariant } from '../util.js';
import { ToolProvisioner } from '../provisioning/service.js';
import { installProvisioning } from '../tools/provisioning.js';

/** CLI and terminal UI share this application service. Dependencies allow offline contract tests. */
export async function solve(options, dependencies = {}) {
  invariant(options.confirmModelUsage === true, 'Pass --confirm-model-usage to authorize model requests');
  const admission = await preview(options.workspace, {
    requireAuthorization: true
  });
  const grant = options.networkGrant ? await loadGrant(options.networkGrant, admission) : null;
  options.signal?.throwIfAborted();
  const lostSandbox = new AbortController();
  const runSignal = options.signal ? AbortSignal.any([options.signal, lostSandbox.signal]) : lostSandbox.signal;
  const prepared = await (dependencies.preparePi ?? preparePi)(options);
  const image = await (dependencies.resolveImage ?? resolveImage)(options.image);
  const evidence = await EvidenceStore.create(options.runsRoot ?? 'runs');
  const runId = basename(evidence.root);
  const runtimeFactory = dependencies.runtimeFactory ?? (config => new DockerRuntime(config));
  let runtime, sessions, broker, agent, state, error, result, cleanupError, provisioner;
  const emit = event => options.onEvent?.(event);
  const receipt = { schema_version: 1, run_id: runId, pending: true, tool_containers: [] };
  let receiptQueue = Promise.resolve();
  const saveReceipt = update => {
    const pending = receiptQueue.then(() => { Object.assign(receipt, update); return evidence.document('recovery.json', receipt); });
    receiptQueue = pending.catch(() => {});
    return pending;
  };
  try {
    const workspace = await snapshot(options.workspace, join(evidence.root, 'challenge'));
    state = new RunState(evidence, {
      run_id: runId,
      image,
      input_files: workspace.manifest.files,
      workspace: workspace.root
    });
    await evidence.append('run_started', {
      identity: state.identity,
      provider: options.provider,
      model: options.model
    });
    emit({
      type: 'run_started',
      run_id: runId,
      run_dir: evidence.root
    });
    runtime = runtimeFactory({
      input: workspace.input,
      image,
      runId,
      onLost: error => lostSandbox.abort(error)
    });
    // Persist the ownership receipt BEFORE contacting Docker, covering uncertain creates and crashes.
    await saveReceipt({ container: runtime.name });
    await runtime.start(runSignal);
    sessions = new Sessions(runtime, evidence);
    if (grant) {
      broker = new NetworkBroker(grant, {
        evidence,
        onLost: error => lostSandbox.abort(error),
        onOwnership: async name => {
          broker.ownedName = name;
          await saveReceipt({ service_container: name });
        }
      });
      await broker.start(runSignal);
    }
    const registry = new ToolRegistry(state, evidence);
    provisioner = new ToolProvisioner({ runtime, evidence, state, input: workspace.input, image, runId, signal: runSignal, onEvent: emit,
      cacheRoot: options.toolCacheRoot,
      onOwnership: name => {
        const pending = receiptQueue.then(() => { receipt.tool_containers.push(name); return evidence.document('recovery.json', receipt); });
        receiptQueue = pending.catch(() => {});
        return pending;
      },
      onRelease: name => {
        const pending = receiptQueue.then(() => { receipt.tool_containers = receipt.tool_containers.filter(x => x !== name); return evidence.document('recovery.json', receipt); });
        receiptQueue = pending.catch(() => {});
        return pending;
      }
    });
    await installCore(registry, {
      workspace,
      runtime,
      sessions,
      supportsImages: prepared.supportsImages
    });
    installProvisioning(registry, { provisioner, workspace });
    await installOptional(registry, {
      broker,
      memoryPath: options.memoryPath
    });
    agent = await prepared.create({
      registry,
      state,
      evidence,
      task: workspace.task,
      guidance: options.additionalPrompt ?? '',
      signal: runSignal,
      onEvent: emit
    });
    options.onReady?.({
      steer: text => agent.steer(text),
      progress: createProgressReader(state, { task: workspace.task, knowledge: registry.knowledge }),
      state: (args = { section: 'overview' }) => state.read(args, sessions.list()),
      candidates: () => [...state.sections.candidates.values()],
      stats: () => agent.stats(),
      setThinkingLevel: level => agent.setThinkingLevel(level)
    });
    const prompt = `Authorized task:\n${workspace.task}\n\nAttachments mounted read-only at /challenge:\n${workspace.manifest.files.map(f => `${f.path} (${f.bytes} bytes; sha256 ${f.sha256})`).join('\n')}\n\nAdditional user guidance:\n${options.additionalPrompt ?? ''}`;
    await evidence.append('prompt', {
      artifact: await evidence.artifact(prompt, 'text/plain')
    });
    await agent.prompt(prompt);
    runSignal.throwIfAborted();
    invariant(state.completion, 'Pi settled without run_complete; no success is inferred');
    result = state.completion;
    await evidence.document('metrics.json', agent.stats());
  } catch (caught) {
    error = caught;
    await evidence.append(options.signal?.aborted ? 'run_cancelled' : 'run_failed', {
      error: caught.message
    });
  } finally {
    const cleanup = [];
    for (const close of [() => agent?.close(), () => provisioner?.close(), () => sessions?.closeAll(), () => runtime?.close(), () => broker?.close()]) {
      try {
        await close();
      } catch (caught) {
        cleanup.push(caught);
      }
    }
    if (cleanup.length) {
      cleanupError = new AggregateError(cleanup, 'Run cleanup unconfirmed');
      await evidence.append('cleanup_failed', {
        errors: cleanup.map(x => x.message)
      });
    } else if (runtime) await saveReceipt({ pending: false });
  }
  const status = cleanupError ? 'cleanup_failed' : options.signal?.aborted ? 'cancelled' : error ? 'failed' : result.outcome;
  await evidence.append('run_finished', {
    status
  });
  emit({
    type: 'run_finished',
    status,
    run_dir: evidence.root
  });
  return {
    status,
    run_dir: evidence.root,
    result,
    error: error?.message,
    cleanup_error: cleanupError?.errors.map(x => x.message),
    ok: status === 'completed'
  };
}
