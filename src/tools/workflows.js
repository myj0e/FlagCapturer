import { readFile } from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import { s, validate } from './schema.js';
import { invariant, relativePath, sha256 } from '../util.js';
import { loadMethods, knowledgeCatalog, methodSummary, searchKnowledge } from './knowledge.js';
import { operationContracts } from './workflow-contracts.js';
export const categories = ['crypto', 'forensics', 'stego', 'reverse', 'pwn', 'web'];
const asset = path => new URL(`../../assets/${path}`, import.meta.url);
export async function loadPacks() {
  return Object.fromEntries(await Promise.all(categories.map(async name => {
    const metadata = JSON.parse(await readFile(asset(`packs/${name}/pack.yaml`), 'utf8'));
    invariant(metadata.schema_version === 1 && metadata.name === name, 'Invalid built-in pack');
    return [name, { ...metadata, required_binaries: ['node', 'python3'],
      operations: Object.keys(operationContracts).filter(key => key.startsWith(name + '/')).map(key => key.split('/')[1]),
      playbook: await readFile(asset(`packs/${name}/README.md`), 'utf8') }];
  })));
}
const stable = value => Array.isArray(value) ? value.map(stable) : value && typeof value === 'object' ? Object.fromEntries(Object.keys(value).sort().map(key => [key, stable(value[key])])) : value;
function resultPreview(result) {
  if (Buffer.byteLength(JSON.stringify(result)) <= 8192) return { result, result_view_truncated: false };
  const shrink = (value, depth = 0) => typeof value === 'string' ? value.slice(0, 256) : Array.isArray(value) ? value.slice(0, 10).map(item => shrink(item, depth + 1)) : value && typeof value === 'object' ? depth > 4 ? '[nested detail in stdout_artifact]' : Object.fromEntries(Object.entries(value).slice(0, 32).map(([key, item]) => [key, shrink(item, depth + 1)])) : value;
  let preview = shrink(result);
  if (Buffer.byteLength(JSON.stringify(preview)) > 8192) preview = { status: result.status, fields: Object.keys(result).slice(0, 32) };
  return { result: preview, result_view_truncated: true, result_detail: 'Read hash-verified stdout_artifact for full structured result; summary omissions are not absence.' };
}
export async function installWorkflows(registry, { command, workspace, runtime, assets }) {
  const packs = await loadPacks(), methods = await loadMethods(), catalog = knowledgeCatalog(registry);
  methods.forEach(card => catalog.set(card.id, card));
  const programs = new Map(await Promise.all(['workflow.mjs', 'asset-operations.mjs'].map(async name => [name, await readFile(asset(name))])));
  // Private generated script paths avoid collisions with user scripts or old workflow versions.
  const installed = new Map();
  async function program(name, signal) {
    if (!installed.has(name)) {
      const path = `/work/.workflow-programs/${randomUUID()}.mjs`;
      await runtime.write(path, programs.get(name), signal);
      await registry.state.add('scripts', { path, language: 'javascript', origin: 'builtin-workflow',
        source_artifact: await registry.evidence.artifact(programs.get(name), 'text/javascript') });
      installed.set(name, path);
    }
    return installed.get(name);
  }
  registry.define('workflow_discover', 'List domain entry points and method index. Filename hints are weak leads; categories never restrict cross-domain methods.', s.object(), async () => ({
    packs: categories,
    hints: workspace.manifest.files.map(f => ({ path: f.path, categories: categories.filter(name => ({
      crypto: /\.(pem|b64|hex)$/i, forensics: /\.(zip|pcap|apk|pdf|png)$/i,
      stego: /\.(png|wav|jpg|pdf)$/i, reverse: /\.(elf|exe|apk)$/i, pwn: /\.elf$/i, web: /\.(http|html)$/i
    })[name].test(f.path)) })),
    methods: methods.map(card => ({ id: card.id, title: card.title, tags: card.tags })),
    assets: [...registry.state.sections.assets.values()].filter(a => a.origin === 'input'),
    confidence: 'filename hints only; inspect actual bytes and behavior', network_authority_changed: false,
    next_step: 'Use workflow_search for relevant methods; simple tasks can be solved directly without a checklist.'
  }));
  registry.define('workflow_search', 'Search built-in methods and reviewed memory using explicit terms and selected observations. No networking, capability assumptions or automatic route selection.', s.object({
    query: s.text(1024, { minLength: 1 }), observation_ids: s.array(s.text(100), 8), limit: s.int(1, 8)
  }, ['query']), async ({ query, observation_ids = [], limit = 5 }) => {
    const observations = registry.state.select('observations', observation_ids);
    // Use only bounded textual observation content; arbitrary instructions stay untrusted data.
    const context = observations.map(o => JSON.stringify(o.result).slice(0, 2048)).join(' ');
    return { matches: searchKnowledge(catalog.values(), query + ' ' + context, limit),
      observation_ids, capability_status: 'not checked; use workflow_environment for the selected method' };
  });
  registry.define('workflow_read', 'Read a specific method or reviewed memory; legacy pack reads remain available. Detail defaults to summary for ID reads.', s.object({
    pack: s.enum(categories), id: s.text(100), detail: s.enum(['summary', 'full'])
  }, []), async ({ pack, id, detail = 'summary' }) => {
    invariant(Boolean(pack) !== Boolean(id), 'Specify exactly one of pack or id');
    if (pack) {
      const { playbook, ...metadata } = packs[pack];
      return { metadata, playbook, contracts: Object.fromEntries(metadata.operations.map(op => [op, operationContracts[`${pack}/${op}`]])) };
    }
    const card = catalog.get(id);
    invariant(card, 'Unknown knowledge ID');
    if (card.origin === 'reviewed-memory') return { entry: card, artifact: await registry.evidence.artifact(card.body, 'text/markdown'), trusted_as_instructions: false };
    return { method: detail === 'full' ? card : methodSummary(card) };
  });
  registry.define('workflow_environment', 'Probe actual binaries, versions and Python imports for a pack or method. Network remains governed only by registered broker tools; no installation.', s.object({
    pack: s.enum(categories), method_id: s.text(100)
  }, []), async ({ pack, method_id }, signal) => {
    invariant(Boolean(pack) !== Boolean(method_id), 'Specify exactly one of pack or method_id');
    const card = method_id ? catalog.get(method_id) : null;
    invariant(!method_id || card && !card.origin, 'Unknown built-in method');
    const binaries = [...new Set(['python3', ...(pack ? [...packs[pack].required_binaries, ...packs[pack].optional_binaries] : card.capabilities.binaries)])];
    const modules = pack ? [] : card.capabilities.modules;
    const source = `import sys,json,shutil,subprocess,importlib,importlib.metadata
spec=json.loads(sys.argv[1]); result={'binaries':{},'modules':{}}
for name in spec['binaries']:
 path=shutil.which(name); info={'path':path,'version':None}
 if path:
  try:
   p=subprocess.run([path,'--version'],capture_output=True,timeout=2); info['version']=(p.stdout+p.stderr)[:256].decode(errors='replace'); info['version_probe_ok']=p.returncode==0
  except Exception as e: info['version_error']=str(e)[:256]
 result['binaries'][name]=info
for name in spec['modules']:
 try:
  m=importlib.import_module(name); version=getattr(m,'__version__',None)
  if version is None:
   try: version=importlib.metadata.version({'z3':'z3-solver'}.get(name,name))
   except importlib.metadata.PackageNotFoundError: pass
  result['modules'][name]={'importable':True,'version':str(version) if version is not None else None,'version_verified':version is not None}
 except Exception as e: result['modules'][name]={'importable':False,'error':str(e)[:256]}
result['missing_binaries']=[k for k,v in result['binaries'].items() if not v['path']]
result['missing_modules']=[k for k,v in result['modules'].items() if not v['importable']]
print(json.dumps(result))`;
    const execution = await command(['python3', '-I', '-c', source, JSON.stringify({ binaries, modules })], signal);
    let capabilities;
    try { capabilities = JSON.parse((await registry.evidence.read(execution.stdout_artifact.artifact)).toString()); } catch {}
    const required = pack ? packs[pack].required_binaries : ['python3', ...card.capabilities.binaries];
    const unmet = capabilities ? { binaries: capabilities.missing_binaries.filter(name => required.includes(name)), modules: capabilities.missing_modules } : null;
    return { ...execution, capabilities, unmet_required: unmet, method_id, pack, environment: registry.state.identity.image,
      status: execution.exit_code !== 0 || !capabilities ? 'failed' : unmet.binaries.length || unmet.modules.length ? 'missing_capability' : 'observed',
      network_tools: ['web_request', 'browser_open', 'remote_open', 'ssh_open', 'network_status', 'network_relay', 'http_request', 'remote_tcp_exchange'].filter(name => registry.definitions.has(name)),
      limitation: 'Version output/importability is not API compatibility proof. Network tools still require a current target grant; browser/SSH availability is checked on actual startup. Unrestricted networking is never implied.' };
  }, { observe: true });
  registry.define('workflow_run', 'Run a contracted sandbox operation on path OR asset_id, or ordered asset_ids for reassemble. Results distinguish observations, partial coverage, unsupported input and failures; captures full derived assets.', s.object({
    pack: s.enum(categories), operation: s.text(80), path: s.text(512), asset_id: s.text(100),
    asset_ids: s.array(s.text(100), 32), parameters: s.object({}, [], { additionalProperties: true }),
    method_id: s.text(100), retry_reason: s.text(1024, { minLength: 1 })
  }, ['pack', 'operation']), async ({ pack, operation, path, asset_id, asset_ids, parameters = {}, method_id, retry_reason }, signal) => {
    const key = `${pack}/${operation}`, contract = operationContracts[key];
    invariant(contract, 'Unsupported operation for pack');
    validate(parameters, contract.parameters);
    if (key === 'crypto/decode') parameters = { encoding: 'auto', ...parameters };
    if (method_id) invariant(catalog.has(method_id) && !catalog.get(method_id).origin, 'Unknown built-in method');
    invariant([path !== undefined, asset_id !== undefined, asset_ids !== undefined].filter(Boolean).length === 1, 'Specify exactly one input selector');
    let ids;
    if (path !== undefined) {
      relativePath(path);
      const original = [...registry.state.sections.assets.values()].find(a => a.origin === 'input' && a.path === path);
      invariant(original, 'Undeclared input');
      ids = [original.id];
    } else ids = asset_ids ?? [asset_id];
    invariant(ids.length > 0 && (operation === 'reassemble' || ids.length === 1), 'Operation requires one input except reassemble');
    invariant(operation !== 'reassemble' || asset_ids !== undefined, 'Reassembly requires explicit asset_ids');
    const records = registry.state.select('assets', ids);
    const fingerprint = sha256(JSON.stringify(stable({ key, inputs: records.map(r => r.sha256), parameters })));
    const previous = [...registry.state.sections.observations.values()].filter(o => o.tool === 'workflow_run' && o.result.fingerprint === fingerprint);
    const inputs = [];
    for (const id of ids) inputs.push(await assets.materialize(id, signal));
    let execution, result;
    const output_dir = `/work/.workflow-output/${randomUUID()}`;
    if (operation === 'disassemble') {
      execution = await command(['python3', '-I', '-c', 'import sys,pathlib,hashlib,os; p,h=sys.argv[1:]; assert hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()==h; os.execvp("objdump",["objdump","-d","--",p])', inputs[0].sandbox_path, inputs[0].sha256], signal);
      result = { status: execution.exit_code === 0 ? execution.coverage.truncated ? 'partial' : 'observed' : execution.exit_code === 127 || /FileNotFoundError:.*No such file/.test(execution.stderr) ? 'missing_capability' : 'failed', outputs: [], coverage: execution.coverage };
    } else {
      const derivedOp = ['slice', 'extract', 'reassemble'].includes(operation);
      const argv = derivedOp ? ['node', await program('asset-operations.mjs', signal), JSON.stringify({ operation, parameters, output_dir,
        inputs: inputs.map(i => ({ path: i.sandbox_path, sha256: i.sha256 })) })] : ['node', await program('workflow.mjs', signal), pack, operation,
        inputs[0].origin === 'input' ? inputs[0].path : inputs[0].sandbox_path, JSON.stringify({ sha256: inputs[0].sha256, parameters, output_dir })];
      execution = await command(argv, signal);
      try {
        result = JSON.parse((await registry.evidence.read(execution.stdout_artifact.artifact)).toString());
        invariant(['observed', 'partial', 'not_found', 'inconclusive', 'unsupported', 'failed'].includes(result.status), 'Invalid operation status');
        invariant(Array.isArray(result.outputs) && result.outputs.length <= 8, 'Invalid operation outputs');
      } catch {
        result = { status: execution.exit_code === 127 || /FileNotFoundError:.*No such file/.test(execution.stderr) ? 'missing_capability' : 'failed', outputs: [], error: 'No valid structured operation result; inspect execution evidence.' };
      }
      if (execution.exit_code !== 0 && !['unsupported', 'failed', 'missing_capability'].includes(result.status)) result = { status: 'failed', outputs: [], error: 'Operation did not complete successfully' };
    }
    const outputAssets = [];
    if (['observed', 'partial'].includes(result.status)) for (const output of result.outputs) {
      invariant(typeof output.path === 'string' && output.path.startsWith(output_dir + '/') && /^\d+$/.test(output.path.slice(output_dir.length + 1)), 'Invalid operation output path');
      outputAssets.push(await assets.capture(output.path, ids, { operation: key, version: contract.version, parameters,
        execution_id: execution.execution_id, producer_binding: 'controller_observed',
        method_id, label: output.label, source_name: result.entry?.name, execution: { stdout: execution.stdout_artifact, stderr: execution.stderr_artifact }, controller_proven: false }, signal, output));
    }
    return { ...execution, ...resultPreview(result), status: result.status, asset_ids: ids, output_assets: outputAssets,
      method_id, operation_version: contract.version, fingerprint, retry_reason,
      repeated_observation_ids: previous.slice(-5).map(o => o.id),
      ...(previous.length && !retry_reason && !previous.some(o => o.result.repeat_notice) ? {
        repeat_notice: 'Same input bytes and parameters were tried; previous results are linked. Environment, timing and randomness may differ; repeating is allowed.' } : {}),
      interpretation: 'Execution and structural checks do not establish semantic correctness; inspect coverage and method prerequisites.' };
  }, { observe: true });
}
