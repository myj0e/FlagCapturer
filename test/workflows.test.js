import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';
import { createHash } from 'node:crypto';
import { tools } from './helpers.js';

test('image symptoms retrieve bounded interleaving and curve guidance', async t => {
  const { call } = await tools(t);
  for (const query of ['文字重叠 OCR 乱码', 'grating raster interleaving', '规则条纹']) {
    const found = await call('workflow_search', { query });
    assert.ok(found.matches.some(m => m.id === 'method/image-periodic-interleaving'), query);
  }
  const card = await call('workflow_read', { id: 'method/image-periodic-interleaving', detail: 'full' });
  assert.deepEqual(card.method.capabilities.modules, ['PIL']);
  assert.match(card.method.probe, /periodic_image.py/);
  const curves = await call('workflow_search', { query: 'Peano Hilbert 空间填充曲线' });
  assert.ok(curves.matches.some(m => m.id === 'method/image-space-filling'));
});

// Execute the same JS workflow program against authored synthetic bytes, with filesystem APIs
// injected at the boundary. No real attachments are parsed on the test host.
const program = (await readFile(new URL('../assets/workflow.mjs', import.meta.url), 'utf8')).replace(/^import .*;\n/gm, '');
async function workflow(pack, operation, bytes) {
  let result;
  const context = vm.createContext({
    Buffer,
    process: {
      argv: ['node', 'workflow', pack, operation, 'fixture']
    },
    readFile: async () => bytes,
    realpath: async () => '/challenge/fixture',
    stat: async () => ({
      size: bytes.length
    }),
    createHash,
    join: (...parts) => parts.join('/'),
    relative: () => 'fixture',
    console: {
      log: text => {
        result = JSON.parse(text);
      }
    }
  });
  const script = new vm.Script(`(async()=>{${program}\n})()`);
  await script.runInContext(context);
  return result;
}
test('crypto workflows decode exact bytes and safely parse large RSA integers', async () => {
  const decoded = await workflow('crypto', 'decode', Buffer.from('ZmxhZ3t0ZXN0fQ=='));
  assert.equal(Buffer.from(decoded.candidates[0].data_base64, 'base64').toString(), 'flag{test}');
  const rsa = await workflow('crypto', 'rsa', Buffer.from('{"n":"3233","e":"17","c":"2790"}'));
  assert.equal(rsa.p, '53');
  assert.equal(Buffer.from(rsa.data_base64, 'base64').toString(), 'A');
  await assert.rejects(workflow('crypto', 'rsa', Buffer.from('{"n":99999999999999999,"e":17,"c":1}')), /decimal strings/);
});
test('web analysis does not follow redirects and reports body coverage', async () => {
  const web = await workflow('web', 'response', Buffer.from('HTTP/1.1 302 Found\r\nLocation: http://example.com\r\n\r\nbody'));
  assert.equal(web.redirect_followed, false);
  assert.equal(web.body_preview, 'body');
});
test('forensics, PNG and ELF workflows reject wrong formats instead of implying absent data', async () => {
  const triage = await workflow('forensics', 'triage', Buffer.from('SYNTHETIC printable string'));
  assert.equal(triage.ascii_strings[0].offset, 0);
  for (const [pack, operation] of [['stego', 'png'], ['reverse', 'elf'], ['forensics', 'archive']]) await assert.rejects(workflow(pack, operation, Buffer.from('not this format')));
});
test('six workflow packs register and enforce operation and path scope', async t => {
  const {
    call
  } = await tools(t);
  assert.equal((await call('workflow_discover', {})).packs.length, 6);
  await assert.rejects(call('workflow_run', {
    pack: 'crypto',
    operation: 'shell',
    path: 'attachment.txt'
  }), /Unsupported operation/);
  await assert.rejects(call('workflow_run', {
    pack: 'crypto',
    operation: 'decode',
    path: '../etc/passwd'
  }), /relative/);
});

test('method retrieval is cross-domain and progressive; contracts reject ambiguous or invalid inputs', async t => {
  const { call, state } = await tools(t);
  const found = await call('workflow_search', { query: 'polyglot png pdf' });
  assert.equal(found.matches[0].id, 'method/format-views');
  assert.equal(found.matches[0].validation, 'source-derived');
  const chinese = await call('workflow_search', { query: '文件魔数和格式不一致' });
  assert.ok(chinese.matches.some(x => x.id === 'method/format-views'));
  const summary = await call('workflow_read', { id: found.matches[0].id });
  assert.equal(summary.method.probe, undefined);
  const full = await call('workflow_read', { id: found.matches[0].id, detail: 'full' });
  assert.ok(full.method.interpretation.contradicts.length);
  const legacy = await call('workflow_read', { pack: 'forensics' });
  assert.equal(legacy.metadata.playbook, undefined);
  assert.ok(legacy.contracts.extract.parameters.properties.entry_index);
  await assert.rejects(call('workflow_read', { pack: 'crypto', id: full.method.id }), /exactly one/);
  await assert.rejects(call('workflow_run', { pack: 'forensics', operation: 'slice', asset_id: 'assets-1', parameters: { offset: -1, length: 1 } }), /range/);
  await assert.rejects(call('workflow_run', { pack: 'crypto', operation: 'decode', path: 'attachment.txt', asset_id: 'assets-1' }), /exactly one/);
  await assert.rejects(call('workflow_run', { pack: 'crypto', operation: 'decode', asset_id: 'assets-1', parameters: { command: 'sh' } }), /Unknown property/);
  assert.equal(state.read({ section: 'assets' }).records[0].origin, 'input');
});

test('captured assets have verified immutable bytes, transitive lineage and model-reported transformations', async t => {
  const { call, runtime, state, evidence } = await tools(t);
  let bytes = Buffer.from('first');
  runtime.export = async () => bytes;
  const first = await call('asset_capture', { path: '/work/result', parent_asset_ids: ['assets-1'], transformation: 'script transformation' });
  bytes = Buffer.from('changed');
  assert.equal((await call('asset_read', { asset_id: first.id, offset: 0, length: 20, encoding: 'utf8' })).data, 'first');
  const second = await call('asset_capture', { path: '/work/result', parent_asset_ids: [first.id], transformation: 'another step' });
  assert.deepEqual(second.parents, [first.id]);
  assert.equal(second.transformation.controller_proven, false);
  assert.notEqual(first.sha256, second.sha256);
  await assert.rejects(call('asset_capture', { path: '/work/../etc/passwd', parent_asset_ids: ['assets-1'], transformation: 'x' }), /confined/);
  await assert.rejects(call('asset_capture', { path: '/work/result', parent_asset_ids: [], transformation: 'x' }), /lineage/);
  await assert.rejects(call('asset_capture', { path: '/work/result', parent_asset_ids: ['assets-999'], transformation: 'x' }), /Unknown assets/);
  // A missing or corrupt captured artifact must not be silently re-read from its mutable path.
  const record = state.sections.assets.get(first.id);
  record.sha256 = '0'.repeat(64);
  await assert.rejects(call('asset_read', { asset_id: first.id, offset: 0, length: 10, encoding: 'hex' }), /integrity/);
  assert.ok((await evidence.read(second.artifact.artifact)).equals(bytes));
});

test('experiment decisions link methods and hypotheses and retain advisory repeat history', async t => {
  const { call } = await tools(t);
  const claim = await call('claim_record', { statement: 'Input has multiple valid views', state: 'hypothesis', observation_ids: [], experiment_ids: [], unknowns: ['parser coverage'] });
  const run = await call('command_run', { argv: ['python3', '-c', 'print("probe")'] });
  const args = { execution_observation_id: run.observation_id, observation_ids: [], parameters: {}, bindings: [], transformations: [], purpose: 'distinguish views', reported_result: 'unknown', method_id: 'method/format-views', hypothesis_ids: [claim.id], expected_observation: 'complementary parser output', next_action: 'inspect coverage', blocker_kind: 'evidence_missing' };
  const first = await call('experiment_record', args);
  assert.equal(first.method_version, '1.0.0');
  const second = await call('experiment_record', { ...args, retry_reason: 'compare stochastic behavior' });
  assert.deepEqual(second.repeated_experiment_ids, [first.id]);
  assert.equal(second.repeat_notice, undefined);
  await assert.rejects(call('experiment_record', { ...args, method_id: 'method/missing' }), /Unknown built-in method/);
});

test('RSA helper does not assume composite cofactors or equal factors are distinct primes', async () => {
  for (const n of ['45', '49']) {
    const result = await workflow('crypto', 'rsa', Buffer.from(JSON.stringify({ n, e: '5', c: '2' })));
    assert.equal(result.distinct_prime_factors_verified, false);
    assert.equal(result.data_base64, undefined);
    assert.equal(result.status, 'inconclusive');
  }
});

test('strict decoding rejects noncanonical base64 and triage retains multiple signature leads', async () => {
  const invalid = await workflow('crypto', 'decode', Buffer.from('Zh=='));
  assert.equal(invalid.status, 'not_found');
  const data = Buffer.concat([Buffer.from('89504e470d0a1a0a', 'hex'), Buffer.from('synthetic %PDF-1.7')]);
  const triage = await workflow('forensics', 'triage', data);
  assert.deepEqual(Array.from(triage.signatures, x => x.format), ['png', 'pdf']);
  assert.ok(triage.signatures.every(x => x.validated === false));
});
