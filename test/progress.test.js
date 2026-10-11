import test from 'node:test';
import assert from 'node:assert/strict';
import { tools } from './helpers.js';
import { assetDisplayName, createProgressReader } from '../src/application/progress.js';
import { summaryDocument } from '../src/ui/solving-summary.js';

test('progress projection names files, preserves provenance, and tracks summary freshness by observation count', async t => {
  const { state, call, registry } = await tools(t);
  const reader = createProgressReader(state, { task: '寻找附件中的 Flag', knowledge: registry.knowledge });
  const input = [...state.sections.assets.values()][0];
  const decoded = await state.add('assets', { origin: 'derived', bytes: 48, parents: [input.id], transformation: { operation: 'crypto/decode', label: 'base64' } });
  await state.add('assets', { origin: 'derived', bytes: 12, parents: [decoded.id], transformation: { operation: 'forensics/extract', source_name: 'dir/config.json' } });
  await call('summary_update', { goal: '寻找 Flag', facts: [`${decoded.id} 已解码`], hypotheses: ['可能存在第二层编码'], approach: ['检查文件结构'], next_steps: ['读取 config.json'], blockers: [], corrections: [] });
  const first = reader();
  assert.strictEqual(reader(), first);
  assert.equal(first.summary_pending, false);
  assert.equal(first.files[0].name, '提取的 config.json');
  assert.deepEqual(first.files[0].parents, ['Base64 解码结果']);
  assert.equal(first.files[1].name, 'Base64 解码结果');
  await call('state_read', { section: 'overview' });
  assert.equal(reader().summary_pending, false);
  await call('command_run', { argv: ['echo', 'test'] });
  assert.equal(reader().summary_pending, true);
  const doc = summaryDocument({ progress: reader(), candidates: [{ candidate: 'flag{assets-99}' }], currentActivity: { purpose: '检查文件格式' } });
  assert.match(doc, /有新观察/);
  assert.match(doc, /模型整理/);
  assert.match(doc, /Base64 解码结果 已解码/);
  assert.match(doc, /flag\{assets-99\}/);
  assert.match(doc, /正在进行\n• 检查文件格式/);
  assert.doesNotMatch(doc, /assets-2|assets-3/);
  await call('summary_update', { goal: '寻找 Flag', facts: ['文件是 JSON'], hypotheses: [], approach: ['读取配置'], next_steps: ['检查密钥'], blockers: ['需要密码'], corrections: ['撤回第二层编码猜测'], achieved_capabilities: ['可提取配置'], remaining_gap: '尚无密钥', blocker_kind: 'prerequisite_unknown' });
  assert.equal(reader().summary_pending, false);
  const updated = summaryDocument({ progress: reader() });
  for (const text of ['可提取配置', '尚无密钥', '撤回第二层编码猜测', '方法前提尚未确认']) assert.ok(updated.includes(text));
});

test('file display names are metadata and duplicate labels remain distinguishable', async t => {
  const { state } = await tools(t);
  for (let i = 0; i < 2; i++) await state.add('assets', { origin: 'derived', captured_path: '/work/.workflow-output/random/0', bytes: 1, transformation: { operation: 'crypto/decode', label: 'hex' } });
  const projection = createProgressReader(state)();
  assert.deepEqual(projection.files.map(f => f.name), ['Hex 解码结果（2）', 'Hex 解码结果']);
  assert.equal(assetDisplayName({ origin: 'derived', transformation: { display_name: '恢复的二维码', description: 'implementation detail' } }), '恢复的二维码');
  assert.equal(assetDisplayName({ origin: 'derived', transformation: { operation: 'forensics/slice', parameters: { offset: 10, length: 20 } } }), '字节片段（10–30）');
});

test('asset capture accepts a display label without changing paths or exact byte identity', async t => {
  const { state, call } = await tools(t);
  const source = [...state.sections.assets.values()][0];
  const captured = await call('asset_capture', { path: '/work/result.bin', parent_asset_ids: [source.id], transformation: 'script output', display_name: '恢复的二维码' });
  assert.equal(captured.captured_path, '/work/result.bin');
  assert.equal(captured.transformation.display_name, '恢复的二维码');
  assert.equal(createProgressReader(state)().files[0].name, '恢复的二维码');
  assert.equal((await call('asset_read', { asset_id: captured.id, offset: 0, length: 10, encoding: 'utf8' })).data, 'checkpoint');
});
