import { s } from './schema.js';
import { invariant, sha256, readRegular, confined } from '../util.js';
import { confinedWorkPath, MAX_ASSET_BYTES } from './assets.js';
import { generation, recordExecution } from '../evidence/provenance.js';

export function installProvisioning(registry, { provisioner, workspace }) {
  const { state, evidence } = registry;
  registry.define('tool_request', 'Request approved capability IDs (or aliases); empty list shows catalog. Reuses current image first. Only fixed small wheels may be fetched by controller; preparation is asynchronous. No network authority changes.', s.object({ capabilities: s.array(s.text(80, { minLength: 1 }), 8) }, []), ({ capabilities = [] }) => provisioner.request(capabilities), { observe: true });
  registry.define('tool_status', 'Read preparation state/environment ID; wait up to 10 seconds or optionally cancel pending preparation. Failed/cancelled requests are retained to prevent repeated downloads.', s.object({ preparation_id: s.text(100), cancel: s.bool(), wait_seconds: s.int(0, 10) }, ['preparation_id']), ({ preparation_id, cancel = false, wait_seconds = 2 }) => provisioner.status(preparation_id, cancel, wait_seconds));
  registry.define('tool_exec', 'Execute argv using a ready environment in a fresh offline sandbox with only declared assets at /work/inputs/<name>. Scripts can be provided by script_id, materialized as /work/tool-script.py (or .mjs/.sh). Declare /work outputs to preserve them as assets; sidecar files otherwise disappear. Main sandbox and its sessions remain running.', s.object({
    environment_id: s.text(100), argv: s.array(s.text(4096), 64),
    inputs: s.array(s.object({ asset_id: s.text(100), name: s.text(100, { pattern: '^[a-zA-Z0-9][a-zA-Z0-9._-]*$' }) }), 16),
    script_id: s.text(100), outputs: s.array(s.object({ path: s.text(512), display_name: s.text(120, { minLength: 1 }) }), 16)
  }, ['environment_id', 'argv']), async ({ environment_id, argv, inputs = [], script_id, outputs = [] }, signal) => {
    invariant(new Set(inputs.map(x => x.name)).size === inputs.length, 'Duplicate tool input name');
    const parents = [...new Set(inputs.map(x => x.asset_id))];
    const records = state.select('assets', parents);
    invariant(!outputs.length || parents.length, 'Declared outputs need input asset lineage');
    outputs.forEach(output => confinedWorkPath(output.path));
    const script = script_id ? state.select('scripts', [script_id])[0] : null;
    return provisioner.execute(environment_id, argv, signal, async (runtime, isolated) => {
      for (const input of inputs) {
        const record = records.find(x => x.id === input.asset_id);
        const bytes = record.origin === 'input' ? await readRegular(await confined(workspace.input, record.path), MAX_ASSET_BYTES) : await evidence.read(record.artifact.artifact);
        invariant(bytes.length <= MAX_ASSET_BYTES && sha256(bytes) === record.sha256, 'Tool input integrity failure');
        await runtime.write(`/work/inputs/${input.name}`, bytes, signal);
      }
      let scriptPath;
      if (script) {
        scriptPath = `/work/tool-script.${{ python: 'py', javascript: 'mjs', shell: 'sh' }[script.language]}`;
        await runtime.write(scriptPath, await evidence.read(script.source_artifact.artifact), signal);
      }
      const result = await runtime.execute(argv, { signal });
      const stdoutArtifact = await evidence.artifact(result.stdout), stderrArtifact = await evidence.artifact(result.stderr);
      const execution = await recordExecution(registry, argv, { exit_code: result.code, timed_out: result.timedOut, cancelled: result.cancelled,
        stdout_artifact: stdoutArtifact, stderr_artifact: stderrArtifact }, { input_asset_ids: parents, environment_id, isolated, inputs });
      await evidence.append('tool_environment_execution', { environment_id, base_image: provisioner.image, argv,
        inputs: inputs.map(input => ({ ...input, sha256: records.find(x => x.id === input.asset_id).sha256 })), script_id,
        exit_code: result.code, timed_out: result.timedOut, cancelled: result.cancelled, truncated: result.truncated,
        stdout: stdoutArtifact, stderr: stderrArtifact });
      const outputAssets = [];
      if (!result.cancelled && result.code === 0) for (const output of outputs) {
        const bytes = await runtime.export(output.path, signal, { maxBytes: MAX_ASSET_BYTES });
        outputAssets.push(await state.add('assets', { origin: 'derived', captured_path: output.path, bytes: bytes.length, sha256: sha256(bytes),
          parents, artifact: await evidence.artifact(bytes), generation: generation(registry, parents, execution.id, 'controller_observed'), sandbox_scope: 'isolated_tool',
          transformation: { operation: 'tool_exec', display_name: output.display_name, environment_id, controller_proven: false }, completeness: 'exact captured bytes; semantic completeness is not established' }));
      }
      return { environment_id, execution_id: execution.id, base_image: provisioner.image, isolated, script_path: scriptPath, exit_code: result.code, timed_out: result.timedOut, truncated: result.truncated, cancelled: result.cancelled,
        stdout: result.stdout.subarray(0, 4096).toString('utf8'), stderr: result.stderr.subarray(0, 4096).toString('utf8'),
        stdout_artifact: stdoutArtifact, stderr_artifact: stderrArtifact, output_assets: outputAssets,
        ...(result.stdout.length > 4096 ? { stdout_tail: { text: result.stdout.subarray(-1024).toString('utf8'), offset: result.stdout.length - 1024, source_bytes: result.stdout.length } } : {}),
        ...(result.stderr.length > 4096 ? { stderr_tail: { text: result.stderr.subarray(-1024).toString('utf8'), offset: result.stderr.length - 1024, source_bytes: result.stderr.length } } : {}),
        coverage: { stdout_bytes: result.stdout.length, stderr_bytes: result.stderr.length, truncated: result.truncated || result.stdout.length > 4096 || result.stderr.length > 4096 } };
    });
  }, { observe: true });
}
