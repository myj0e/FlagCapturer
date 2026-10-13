import { s } from './schema.js';
import { invariant, sha256 } from '../util.js';
const refs = () => s.array(s.text(100));
const strings = () => s.array(s.text(2048), 32);
const blockerKinds = ['evidence_missing', 'prerequisite_unknown', 'hypothesis_rejected', 'capability_missing', 'composition_unclear'];
const bytesFormat = s.enum(['utf8', 'hex']);
export function installReliability(registry, {
  sessions,
  inputBytes,
  command,
  assets
}) {
  const {
    state,
    evidence
  } = registry;
  const define = registry.define.bind(registry);
  define('state_read', 'Read durable current-run state; summaries and claims are model reports. Cursors expire on state changes.', s.object({
    section: s.enum(['overview', 'sessions', ...Object.keys(state.sections)]),
    id: s.text(100),
    cursor: s.text(100),
    limit: s.int(1, 20)
  }, ['section']), async args => state.read(args, sessions.list()));
  define('observation_get', 'Read one historical observation and its raw evidence references.', s.object({
    observation_id: s.text(100)
  }), async ({
    observation_id
  }) => ({
    observation: state.select('observations', [observation_id])[0]
  }));
  define('summary_update', 'Replace the public dashboard. Facts are model reports; do not include private reasoning.', s.object({
    goal: s.text(2048),
    facts: strings(),
    hypotheses: strings(),
    approach: strings(),
    next_steps: strings(),
    blockers: strings(),
    corrections: strings(),
    observation_ids: refs(),
    achieved_capabilities: strings(),
    remaining_gap: s.text(2048),
    blocker_kind: s.enum(blockerKinds)
  }, ['goal', 'facts', 'hypotheses', 'approach', 'next_steps', 'blockers', 'corrections']), async args => {
    state.select('observations', args.observation_ids);
    return state.add('summaries', {
      ...args,
      observation_count: state.sections.observations.size,
      state_revision: state.revision + 1,
      controller_proven: false
    });
  });
  define('candidate_submit', 'Submit exact flag text immediately. Submission completes that objective; continue only actual remaining subtasks, then run_complete with writeup. No platform verification.', s.object({
    candidate: s.text(4096, {
      minLength: 1
    }),
    subtask: s.text(160),
    observation_ids: refs(),
    check_ids: refs(),
    derivation: s.text(2048)
  }, ['candidate']), async args => state.submit(args));
  define('candidate_check', 'Optional local relation check. A pass is not flag correctness proof.', s.object({
    candidate_id: s.text(100),
    operation: s.enum(['base64_equals', 'json_field_equals', 'http_body_equals', 'bytes_equals']),
    path: s.text(512),
    asset_id: s.text(100),
    selector: s.text(512)
  }, ['candidate_id', 'operation']), async ({
    candidate_id,
    operation,
    path,
    asset_id,
    selector = ''
  }, signal) => {
    invariant(Boolean(path) !== Boolean(asset_id), 'Specify exactly one of path or asset_id');
    const candidate = state.select('candidates', [candidate_id])[0];
    const bytes = asset_id ? (await assets.read(asset_id)).bytes : await inputBytes(path);
    const sandboxPath = asset_id ? (await assets.materialize(asset_id, signal)).sandbox_path : `/challenge/${path}`;
    invariant(bytes.length <= 1024 * 1024, 'Local check input exceeds 1 MiB');
    // JSON and HTTP parsing run inside the sandbox as with all untrusted formats.
    const source = `import pathlib,sys,json,base64,hashlib
p,expected,op,candidate,selector=sys.argv[1:]; data=pathlib.Path(p).read_bytes(); assert hashlib.sha256(data).hexdigest()==expected
candidate=base64.b64decode(candidate); passed=False
if op=='bytes_equals': passed=data==candidate
elif op=='base64_equals': passed=base64.b64decode(data.strip(),validate=True)==candidate
elif op=='http_body_equals': passed=data.partition(b'\\r\\n\\r\\n')[2]==candidate and b'\\r\\n\\r\\n' in data
else:
 value=json.loads(data)
 if selector:
  assert selector.startswith('/')
  for key in selector[1:].split('/'):
   key=key.replace('~1','/').replace('~0','~'); value=value[int(key)] if isinstance(value,list) else value[key]
 passed=isinstance(value,str) and value.encode()==candidate
print(json.dumps({'passed':passed}))`;
    const result = await command(['python3', '-I', '-c', source, sandboxPath, sha256(bytes), operation, Buffer.from(candidate.candidate).toString('base64'), selector], signal);
    let passed = false;
    try {
      passed = result.exit_code === 0 && JSON.parse(result.stdout).passed === true;
    } catch {}
    return state.add('checks', {
      candidate_id,
      candidate_sha256: candidate.candidate_sha256,
      source_sha256: sha256(bytes),
      asset_id,
      operation,
      selector,
      passed,
      verified: false,
      validation_level: 'local_check',
      execution: result
    });
  }, {
    observe: true
  });
  define('experiment_record', 'Record actual execution with source byte bindings. Consistency checks never prove the reported conclusion.', s.object({
    execution_observation_id: s.text(100),
    observation_ids: refs(),
    parameters: s.object({}, [], {
      additionalProperties: true
    }),
    bindings: s.array(s.object({
      source_ref: s.text(100),
      parameter_key: s.text(100),
      representation: bytesFormat
    }), 16),
    transformations: strings(),
    purpose: s.text(2048),
    reported_result: s.enum(['unknown', 'supports', 'contradicts']),
    method_id: s.text(100), hypothesis_ids: refs(), expected_observation: s.text(2048),
    next_action: s.text(2048), blocker_kind: s.enum(blockerKinds), retry_reason: s.text(1024, { minLength: 1 })
  }, ['execution_observation_id', 'observation_ids', 'parameters', 'bindings', 'transformations', 'purpose', 'reported_result']), async args => {
    const execution = state.select('observations', [args.execution_observation_id])[0];
    invariant(['command_run', 'script_run', 'script_start', 'session_start', 'session_read', 'session_send', 'workflow_run', 'tool_exec'].includes(execution.tool), 'Execution observation required');
    state.select('observations', args.observation_ids);
    state.select('claims', args.hypothesis_ids);
    const method = args.method_id ? registry.knowledge?.get(args.method_id) : null;
    invariant(!args.method_id || method && !method.origin, 'Unknown built-in method');
    const repeated = [...state.sections.experiments.values()].filter(record => {
      const previous = state.select('observations', [record.execution_observation_id])[0];
      return previous.request.sha256 === execution.request.sha256;
    });
    const checks = [];
    for (const binding of args.bindings) {
      const source = state.select('sources', [binding.source_ref])[0];
      invariant(Object.hasOwn(args.parameters, binding.parameter_key), 'Binding parameter is missing');
      const bytes = (await evidence.read(source.artifact.artifact)).subarray(source.offset, source.offset + source.length);
      invariant(sha256(bytes) === source.sha256, 'Source binding integrity failure');
      const expected = bytes.toString(binding.representation === 'hex' ? 'hex' : 'utf8');
      checks.push({
        source_ref: source.id,
        parameter_key: binding.parameter_key,
        consistent: args.parameters[binding.parameter_key] === expected
      });
    }
    return state.add('experiments', {
      ...args,
      method_version: method?.version,
      repeated_experiment_ids: repeated.slice(-5).map(x => x.id),
      repeat_notice: repeated.length && !args.retry_reason ? 'Repeated execution request; compare evidence and explain changed conditions before retrying.' : undefined,
      source_checks: checks,
      source_consistent: checks.every(x => x.consistent),
      controller_proven: false
    });
  });
  define('claim_record', 'Record a public hypothesis or model-reported support; inconsistent source bindings cannot upgrade a claim.', s.object({
    statement: s.text(2048),
    state: s.enum(['hypothesis', 'supported', 'contradicted']),
    observation_ids: refs(),
    experiment_ids: refs(),
    unknowns: strings(),
    supersedes: refs()
  }, ['statement', 'state', 'observation_ids', 'experiment_ids', 'unknowns']), async args => {
    const observations = state.select('observations', args.observation_ids),
      experiments = state.select('experiments', args.experiment_ids);
    state.select('claims', args.supersedes);
    const supported = observations.length > 0 && experiments.every(x => x.source_consistent);
    return state.add('claims', {
      ...args,
      requested_state: args.state,
      state: args.state === 'supported' && !supported ? 'hypothesis' : args.state,
      controller_proven: false
    });
  });
  define('route_review_record', 'Compatibility tool for an existing historical pending route review. New tasks use periodic state summaries; there is no mandatory review ceremony.', s.object({
    review_id: s.text(100), assumptions: strings(), evidence_gaps: strings(), decision: s.enum(['continue', 'pivot', 'blocked']),
    rationale: s.text(2048, { minLength: 1 }), next_action: s.text(2048, { minLength: 1 }), observation_ids: refs()
  }), async args => {
    const review = state.select('route_reviews', [args.review_id])[0];
    invariant(review.status === 'pending', 'Expected a pending route review');
    invariant(![...state.sections.route_reviews.values()].some(r => r.resolves === review.id), 'Route review already resolved');
    state.select('observations', args.observation_ids);
    return state.add('route_reviews', { ...args, resolves: review.id, status: 'resolved',
      execution_count: [...state.sections.observations.values()].filter(o => ['command_run', 'script_run', 'script_start', 'tool_exec', 'workflow_run'].includes(o.tool)).length,
      candidate_count: state.sections.candidates.size, supported_count: [...state.sections.claims.values()].filter(c => c.state === 'supported').length });
  });
  define('run_complete', 'Save mandatory Markdown writeup and finish explicitly. completed includes every submitted flag and no unresolved work; partial lists actual unfinished subtasks.', s.object({
    outcome: s.enum(['completed', 'partial', 'unsolved', 'candidate_unverified']),
    candidate_ids: refs(),
    summary: s.text(2048, {
      minLength: 1
    }),
    unresolved: strings(),
    writeup: s.text(65536, {
      minLength: 1
    })
  }), async args => state.complete(args));
}
