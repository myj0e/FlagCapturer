import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdir, writeFile, readFile, symlink, rm } from 'node:fs/promises';
import { join } from 'node:path';
import { tools } from './helpers.js';
import { SkillCatalog, discoverSkills } from '../src/skills/catalog.js';
import { loadSdk } from '../src/pi/sdk.js';
import { installSkills } from '../src/tools/skills.js';
import { EvidenceStore, readEvents } from '../src/evidence/store.js';
import { RunState } from '../src/evidence/state.js';
import { inheritRun } from '../src/application/resume.js';
import { ContextMemory } from '../src/context/memory.js';
import { stateInput } from '../src/context/format.js';
import { sha256 } from '../src/util.js';

async function custom(root, content = 'Original instruction') {
  const path = join(root, 'registered');
  await mkdir(join(path, 'custom-skill'), { recursive: true });
  await writeFile(join(path, 'custom-skill/SKILL.md'), `---\nname: custom-skill\ndescription: Read the authored custom fixture.\n---\n${content}\n`);
  await writeFile(join(path, 'custom-skill/reference.md'), '中文参考\n'.repeat(5000));
  return path;
}

test('Pi discovers curated skills, diagnoses collisions and excludes ambient/challenge roots', async t => {
  const { root } = await tools(t);
  const path = await custom(root);
  await mkdir(join(path, 'ctf-crypto'));
  await writeFile(join(path, 'ctf-crypto/SKILL.md'), '---\nname: ctf-crypto\ndescription: Duplicate.\n---\nDuplicate');
  const result = await discoverSkills(await loadSdk(), { skillPaths: [path], userDirectory: null });
  assert.equal(result.skills.length, 13);
  assert.equal(result.skills.filter(s => s.name === 'ctf-crypto').length, 1);
  assert.ok(result.diagnostics.length);
  assert.ok(result.skills.every(s => !s.filePath.includes('/challenge/')));
});

test('skill resources are bounded, immutable, confined and resume from captured bytes', async t => {
  const data = await tools(t); const sdk = await loadSdk(); const path = await custom(data.root);
  await symlink('/etc/passwd', join(path, 'custom-skill/escape.md'));
  const catalog = await SkillCatalog.create({ sdk, ...data, skillPaths: [path], userDirectory: null });
  installSkills(data.registry, catalog);
  await writeFile(join(path, 'custom-skill/SKILL.md'), 'Changed after snapshot');
  assert.match((await catalog.read({ name: 'custom-skill' })).text, /Original instruction/);
  await assert.rejects(catalog.read({ path: '/etc/passwd' }), /registered/);
  await assert.rejects(catalog.read({ name: 'custom-skill', path: '../../../etc/passwd' }), /registered/);
  await assert.rejects(catalog.read({ name: 'custom-skill', path: 'escape.md' }), /registered/);
  const first = await catalog.read({ name: 'custom-skill', path: 'reference.md', limit: 24000 });
  assert.ok(first.next_offset > 0 && first.next_offset < first.total_characters);
  const next = await catalog.read({ name: 'custom-skill', path: 'reference.md', offset: first.next_offset });
  assert.equal(first.text + next.text, ('中文参考\n'.repeat(5000)).slice(0, first.text.length + next.text.length));
  assert.ok(Buffer.byteLength(JSON.stringify(first)) < 16384);
  const oldPath = catalog.skills.find(s => s.name === 'custom-skill').filePath;
  await catalog.recordUse(oldPath, 'explicit');
  const target = await EvidenceStore.create(join(data.root, 'resumed'));
  const state = new RunState(target, data.state.identity);
  await inheritRun({ root: data.evidence.root, source: data.evidence, events: await readEvents(data.evidence.root) }, target, state);
  await rm(path, { recursive: true });
  const restored = await SkillCatalog.create({ sdk, evidence: target, state, userDirectory: null });
  assert.match((await restored.read({ path: oldPath })).text, /Original instruction/);
  assert.equal(restored.record.fingerprint, catalog.record.fingerprint);
  assert.equal(state.sections.skill_uses.size, 1);
});

test('captured helper materialization creates a provenance asset without executing', async t => {
  const data = await tools(t); const catalog = await SkillCatalog.create({ sdk: await loadSdk(), ...data, userDirectory: null });
  const writes = []; data.runtime.write = async (path, bytes) => writes.push({ path, bytes });
  let executions = 0; data.runtime.execute = async () => { executions++; throw Error('must not execute'); };
  installSkills(data.registry, catalog);
  const result = await data.call('skill_materialize', { path: 'vendor/ctf-pwn/scripts/shellcraft_asm.py' });
  assert.equal(executions, 0); assert.equal(writes.length, 1);
  assert.match(result.sandbox_path, /^\/work\/\.assets\//);
  assert.equal(sha256(writes[0].bytes), result.sha256);
  assert.equal(data.state.select('assets', [result.asset_id])[0].skill_resource, 'vendor/ctf-pwn/scripts/shellcraft_asm.py');
  await assert.rejects(data.call('skill_materialize', { path: 'vendor/LICENSE' }), /helper/);
});

test('historical pending route reviews remain readable and can be resolved without a new trigger', async t => {
  const data = await tools(t);
  const review = await data.state.add('route_reviews', { status: 'pending', reason: '历史映射前提尚未确认' });
  await data.call('route_review_record', { review_id: review.id, assumptions: ['映射尚未确认'], evidence_gaps: ['原始字节范围'], decision: 'continue', rationale: '下一实验能够区分两种布局', next_action: '检查嵌入签名', observation_ids: [] });
  assert.equal([...data.state.sections.route_reviews.values()].at(-1).status, 'resolved');
});

test('vendored content exactly matches pinned source hashes and retains MIT attribution', async () => {
  const base = new URL('../assets/vendor/ctf-skills/', import.meta.url);
  const manifest = JSON.parse(await readFile(new URL('SOURCE.json', base)));
  assert.match(manifest.commit, /^[a-f0-9]{40}$/);
  for (const [file, hash] of Object.entries(manifest.files)) assert.equal(sha256(await readFile(new URL(file, base))), hash, file);
  assert.match(await readFile(new URL('LICENSE', base), 'utf8'), /MIT License/);
});

test('current context keeps only a recovery pointer to legacy review instructions', async t => {
  const data = await tools(t);
  await data.state.add('skill_uses', { resource: 'builtin/ctf-forensics/SKILL.md', sha256: 'a'.repeat(64) });
  await data.state.add('route_reviews', { status: 'pending', reason: '映射前提尚未确认', instruction: '检查遗漏证据' });
  const memory = new ContextMemory({ ...data, model: { contextWindow: 128000, maxTokens: 4096 }, task: '合成题目' });
  const projected = JSON.parse(memory.currentLayer());
  assert.equal(projected.recent_skill_uses[0].sha256, 'a'.repeat(64));
  assert.equal(projected.legacy_route_reviews.historical_only, true);
  assert.doesNotMatch(memory.currentLayer(), /检查遗漏证据/);
  const input = stateInput(data.state);
  assert.equal(input.skill_uses[0].resource, 'builtin/ctf-forensics/SKILL.md');
  assert.equal(input.route_reviews[0].status, 'pending');
  assert.equal(input.route_reviews[0].historical_only, true);
  assert.equal(input.route_reviews[0].instruction, undefined);
});
