import test from 'node:test';
import assert from 'node:assert/strict';
import { tools } from './helpers.js';
import { audit, readEvents } from '../src/evidence/store.js';
import { sha256 } from '../src/util.js';
const completion = {
  outcome: 'completed',
  candidate_ids: [],
  summary: 'Synthetic task complete',
  unresolved: [],
  writeup: '# Synthetic solution\nActual fixture evidence.'
};
test('candidate submission preserves exact text and does not end the run', async t => {
  const {
    call,
    state,
    evidence
  } = await tools(t);
  const candidate = await call('candidate_submit', {
    candidate: ' flag{原文}\n',
    subtask: 'first'
  });
  assert.equal(candidate.candidate, ' flag{原文}\n');
  assert.equal(candidate.verified, false);
  assert.equal(state.completion, null);
  await call('run_complete', {
    ...completion,
    candidate_ids: [candidate.id]
  });
  assert.equal(state.completion.outcome, 'completed');
  assert.equal((await audit(evidence.root)).ok, true);
  await assert.rejects(call('challenge_list', {}), /after run completion/);
});
test('completion rejects invented candidates and incomplete status invariants', async t => {
  const {
    call,
    state
  } = await tools(t);
  await assert.rejects(call('run_complete', {
    ...completion,
    candidate_ids: ['invented']
  }), /Unknown/);
  await assert.rejects(call('run_complete', completion), /candidates/);
  const candidate = await call('candidate_submit', {
    candidate: 'flag{one}'
  });
  await assert.rejects(call('run_complete', {
    ...completion,
    candidate_ids: [candidate.id],
    unresolved: ['second']
  }), /unresolved/);
  await assert.rejects(call('run_complete', {
    ...completion,
    outcome: 'partial',
    candidate_ids: [candidate.id]
  }), /unfinished/);
  await assert.rejects(call('run_complete', {
    ...completion,
    candidate_ids: [candidate.id],
    writeup: ' '
  }), /Writeup/);
  assert.equal(state.completion, null);
});
test('multi-flag completion must account for every submission', async t => {
  const {
    call
  } = await tools(t);
  const a = await call('candidate_submit', {
      candidate: 'a'
    }),
    b = await call('candidate_submit', {
      candidate: 'b'
    });
  await assert.rejects(call('run_complete', {
    ...completion,
    candidate_ids: [a.id]
  }), /every/);
  const result = await call('run_complete', {
    ...completion,
    candidate_ids: [a.id, b.id]
  });
  assert.equal(result.candidate_ids.length, 2);
});
test('focused reads bind actual bytes; inconsistent experiments cannot support claims', async t => {
  const {
    call,
    state
  } = await tools(t);
  const read = await call('challenge_read_bytes', {
    path: 'attachment.txt',
    offset: 0,
    length: 4
  });
  assert.equal(read.data, Buffer.from('flag').toString('hex'));
  const command = await call('command_run', {
    argv: ['file', '/challenge/attachment.txt']
  });
  const experiment = await call('experiment_record', {
    execution_observation_id: command.observation_id,
    observation_ids: [read.observation_id],
    parameters: {
      value: 'wrong'
    },
    bindings: [{
      source_ref: read.source_ref,
      parameter_key: 'value',
      representation: 'utf8'
    }],
    transformations: [],
    purpose: 'synthetic mismatch',
    reported_result: 'supports'
  });
  assert.equal(experiment.source_consistent, false);
  const claim = await call('claim_record', {
    statement: 'claimed fact',
    state: 'supported',
    observation_ids: [read.observation_id],
    experiment_ids: [experiment.id],
    unknowns: []
  });
  assert.equal(claim.state, 'hypothesis');
  assert.equal(claim.controller_proven, false);
  assert.equal(state.sections.observations.size, 2);
});
test('large tool replies remain bounded with retrievable full raw evidence', async t => {
  const {
    registry,
    evidence
  } = await tools(t);
  registry.define('large', 'fixture', {
    type: 'object',
    properties: {},
    required: [],
    additionalProperties: false
  }, async () => ({
    text: 'x'.repeat(50000)
  }));
  const result = await registry.invoke('large', {});
  assert.ok(Buffer.byteLength(result.content[0].text) <= 16384);
  const reply = JSON.parse(result.content[0].text);
  assert.equal(reply.truncated, true);
  assert.equal(JSON.parse((await evidence.read(reply.full_result.artifact)).toString()).text.length, 50000);
});
test('schema rejects unknown arguments and wrong integer types', async t => {
  const {
    call
  } = await tools(t);
  await assert.rejects(call('challenge_read_bytes', {
    path: 'attachment.txt',
    offset: '0',
    length: 4
  }), /Integer/);
  await assert.rejects(call('challenge_list', {
    host: 'example.com'
  }), /Unknown property/);
  await assert.rejects(call('candidate_submit', {
    candidate: 'x',
    observation_ids: ['fake']
  }), /Unknown/);
});
test('image tool appears only for a multimodal model and returns actual image content', async t => {
  const {
    registry,
    runtime
  } = await tools(t, {
    supportsImages: true
  });
  runtime.execute = async () => ({
    code: 0,
    stdout: Buffer.from(JSON.stringify({
      data: Buffer.from('synthetic png').toString('base64'),
      width: 1,
      height: 1
    }))
  });
  const result = await registry.invoke('image_view', {
    path: '/challenge/attachment.txt'
  });
  assert.equal(result.content[1].type, 'image');
  assert.equal(result.content[1].mimeType, 'image/png');
  assert.equal((await tools(t)).registry.definitions.has('image_view'), false);
});
test('command evidence records both failures and raw streams', async t => {
  const {
    call,
    evidence,
    runtime
  } = await tools(t);
  runtime.execute = async () => ({
    code: 1,
    stdout: Buffer.from('partial'),
    stderr: Buffer.from('failure'),
    timedOut: false,
    truncated: false
  });
  const result = await call('command_run', {
    argv: ['false']
  });
  assert.equal(result.exit_code, 1);
  assert.equal((await evidence.read(result.stderr_artifact.artifact)).toString(), 'failure');
  assert.ok((await readEvents(evidence.root)).some(x => x.type === 'tool_finished'));
});
test('state cursors expire when records change', async t => {
  const {
    call
  } = await tools(t);
  await call('candidate_submit', {
    candidate: 'a'
  });
  await call('candidate_submit', {
    candidate: 'b'
  });
  const page = await call('state_read', {
    section: 'candidates',
    limit: 1
  });
  assert.ok(page.cursor);
  await call('candidate_submit', {
    candidate: 'c'
  });
  await assert.rejects(call('state_read', {
    section: 'candidates',
    cursor: page.cursor,
    limit: 1
  }), /State changed/);
});
test('cross-candidate local check references cannot be reused', async t => {
  const {
    state,
    call
  } = await tools(t);
  await state.add('checks', {
    candidate_sha256: sha256('first')
  });
  await assert.rejects(call('candidate_submit', {
    candidate: 'second',
    check_ids: ['checks-1']
  }), /another candidate/);
});
test('running sessions are successful tool results while failed sessions remain errors', async t => {
  const {
    registry,
    sessions
  } = await tools(t);
  sessions.start = async () => ({
    session_id: 'fixture',
    state: 'running',
    exit_code: null
  });
  assert.ok(!(await registry.invoke('session_start', {
    argv: ['cat']
  })).isError);
  sessions.read = async () => ({
    session_id: 'fixture',
    state: 'running',
    exit_code: null
  });
  assert.ok(!(await registry.invoke('session_read', {
    session_id: 'fixture'
  })).isError);
  sessions.read = async () => ({
    session_id: 'fixture',
    state: 'failed',
    exit_code: null
  });
  assert.equal((await registry.invoke('session_read', {
    session_id: 'fixture'
  })).isError, true);
  sessions.close = async () => ({
    session_id: 'fixture',
    state: 'closed',
    exit_code: 137
  });
  assert.ok(!(await registry.invoke('session_close', {
    session_id: 'fixture'
  })).isError);
});

test('command previews retain tail offsets and full evidence when the useful result is at the end', async t => {
  const { call, runtime, evidence } = await tools(t);
  const stdout = Buffer.from('progress\n'.repeat(900) + 'final result at tail');
  runtime.execute = async () => ({ code: 0, stdout, stderr: Buffer.alloc(0), timedOut: false, truncated: false });
  const result = await call('command_run', { argv: ['python3', '-c', 'print("synthetic")'] });
  assert.ok(!result.stdout.includes('final result at tail'));
  assert.match(result.stdout_tail.text, /final result at tail$/);
  assert.equal(result.stdout_tail.offset, stdout.length - 1024);
  assert.equal(result.stdout_tail.source_bytes, stdout.length);
  assert.deepEqual(await evidence.read(result.stdout_artifact.artifact), stdout);
});
