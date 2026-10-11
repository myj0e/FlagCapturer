// Operator-invoked live smoke check. Uses authored fixtures and the currently saved provider/model.
import { readFile, writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { ModelCatalog } from '../src/pi/catalog.js';
import { importFile } from '../src/challenge/workspace.js';
import { solve } from '../src/application/solve.js';
import { audit, EvidenceStore, readEvents } from '../src/evidence/store.js';
import { runProcess } from '../src/runtime/process.js';
const root = resolve(process.argv[2]);
const cases = JSON.parse(await readFile(join(root, 'expected.json'), 'utf8'));
const catalog = await ModelCatalog.create();
const { provider, model, thinkingLevel, image } = catalog.preferences;
if (!provider || !model || !catalog.runtime.getProviderAuthStatus(provider).configured) throw new Error('Select and authenticate a model in the workbench first.');
const controller = new AbortController();
const stop = () => controller.abort();
process.once('SIGINT', stop); process.once('SIGTERM', stop);
const results = [];
const persist = () => writeFile(join(root, 'summary.json'), JSON.stringify({ provider, model, thinkingLevel, results }, null, 2), { mode: 0o600 });
try {
  for (const item of cases) {
    if (controller.signal.aborted) break;
    const workspace = await importFile(join(root, 'fixtures', item.file), join(root, 'imports'), {
      authorized: true, basis: 'Operator requested live end-to-end smoke check using authored synthetic data.',
      task: '这是一个离线 CTF 附件。请独立分析附件，找出唯一 flag，准确提交并以中文保存可复现的 writeup。'
    });
    console.log(JSON.stringify({ event: 'case_started', id: item.id, provider, model, thinkingLevel }));
    const started = Date.now();
    const result = await solve({ workspace: workspace.root, provider, model, thinkingLevel, image,
      modelRuntime: catalog.runtime, confirmModelUsage: true, runsRoot: join(root, 'runs'), signal: controller.signal,
      onEvent: event => {
        if (event.type === 'tool_execution_start') console.log(JSON.stringify({ event: 'tool', id: item.id, name: event.toolName }));
        if (event.type === 'run_started') console.log(JSON.stringify({ event: 'run_started', id: item.id, run_dir: event.run_dir }));
      }
    });
    const events = result.run_dir ? await readEvents(result.run_dir) : [];
    const store = result.run_dir ? new EvidenceStore(result.run_dir) : null;
    const candidates = [];
    for (const event of events) if (event.type === 'state_record' && event.section === 'candidates') {
      candidates.push(JSON.parse((await store.read(event.artifact.artifact)).toString()).candidate);
    }
    const recovery = result.run_dir ? JSON.parse(await readFile(join(result.run_dir, 'recovery.json'), 'utf8')) : null;
    const inspection = recovery?.container ? await runProcess('docker', ['ps', '-a', '--filter', `name=^/${recovery.container}$`, '--format', '{{.Names}}']) : null;
    const row = { id: item.id, status: result.status, ok: result.ok, candidate_count: candidates.length,
      exact_answer_match: candidates.length === 1 && candidates[0] === item.expected,
      evidence_integrity: result.run_dir ? (await audit(result.run_dir)).ok : false,
      cleanup_confirmed: recovery?.pending === false && inspection?.code === 0 && inspection.stdout.length === 0,
      elapsed_ms: Date.now() - started, run_dir: result.run_dir,
      tool_calls: events.filter(e => e.type === 'tool_finished').length,
      ...(result.error ? { error: result.error } : {}) };
    results.push(row); await persist(); console.log(JSON.stringify({ event: 'case_finished', ...row }));
    if (result.cleanup_error) break;
  }
} finally {
  await persist();
  process.removeListener('SIGINT', stop); process.removeListener('SIGTERM', stop);
}
if (results.length !== cases.length || results.some(r => !r.ok || !r.exact_answer_match || !r.evidence_integrity || !r.cleanup_confirmed)) process.exitCode = 1;
