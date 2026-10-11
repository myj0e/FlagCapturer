// Read-only projection of existing run state. No inferred facts, file parsing or agent loop.
const meaningfulTools = new Set(['tool_exec', 'command_run', 'script_run', 'script_start', 'session_start', 'session_read', 'session_send', 'workflow_run', 'challenge_list', 'challenge_read_text', 'challenge_read_bytes', 'artifact_read', 'artifact_tail', 'artifact_find', 'workflow_environment', 'asset_read', 'asset_capture', 'artifact_export', 'image_view', 'http_request', 'remote_tcp_exchange', 'candidate_check']);
const basename = path => String(path ?? '').split('/').filter(Boolean).at(-1) ?? '';
export function assetDisplayName(asset) {
  const transform = asset.transformation ?? {};
  if (transform.display_name) return transform.display_name;
  if (asset.origin === 'input') return asset.path;
  if (transform.source_name) return `提取的 ${basename(transform.source_name)}`;
  if (transform.operation === 'crypto/decode') return `${({ base64: 'Base64', hex: 'Hex' })[transform.label] ?? '编码'} 解码结果`;
  const names = { 'crypto/rsa': 'RSA 解密结果', 'stego/ppm_lsb': 'LSB 提取结果', 'forensics/extract': '归档提取文件', 'forensics/reassemble': '分片重组结果' };
  if (names[transform.operation]) return names[transform.operation];
  if (transform.operation === 'forensics/slice') {
    const p = transform.parameters ?? {};
    return `字节片段${Number.isInteger(p.offset) && Number.isInteger(p.length) ? `（${p.offset}–${p.offset + p.length}）` : ''}`;
  }
  if (transform.description) return transform.description;
  const name = basename(asset.captured_path);
  return name && !/^\d+$/.test(name) ? name : '脚本生成文件';
}
export function createProgressReader(state, { task, knowledge = new Map() } = {}) {
  let revision = -1, cached;
  return () => {
    if (revision === state.revision) return cached;
    const assets = [...state.sections.assets.values()];
    const counts = new Map();
    const names = Object.fromEntries(assets.map(asset => {
      const title = assetDisplayName(asset);
      const count = (counts.get(title) ?? 0) + 1;
      counts.set(title, count);
      return [asset.id, count === 1 ? title : `${title}（${count}）`];
    }));
    const summaries = [...state.sections.summaries.values()];
    const publicSummary = summaries.at(-1) ?? null;
    const managed = [...state.sections.context_snapshots.values()].at(-1);
    const summary = managed && (!publicSummary || managed.state_revision > (publicSummary.state_revision ?? 0))
      ? { ...managed.public_summary, id: managed.id, time: managed.time, observation_count: managed.observation_count } : publicSummary;
    const observations = [...state.sections.observations.values()];
    const afterSummary = summary?.observation_count !== undefined ? observations.slice(summary.observation_count) : observations.filter(o => !summary || o.time > summary.time);
    const claims = [...state.sections.claims.values()];
    const superseded = new Set(claims.flatMap(claim => claim.supersedes ?? []));
    const workflow = observations.findLast(o => o.tool === 'workflow_run')?.result;
    const derived = assets.filter(a => a.origin !== 'input');
    cached = {
      task, revision: state.revision, summary,
      tool_environments: [...new Map([...(state.sections.tool_environments?.values() ?? [])].map(x => [x.preparation_id, x])).values()].slice(-8),
      summary_pending: Boolean(summary && afterSummary.some(o => meaningfulTools.has(o.tool))),
      claims: claims.filter(c => !superseded.has(c.id)).slice(-8).map(c => ({ statement: c.statement, state: c.state })),
      experiments: [...state.sections.experiments.values()].slice(-3).map(e => ({ purpose: e.purpose, reported_result: e.reported_result, next_action: e.next_action })),
      workflow: workflow ? { status: workflow.status, method_title: knowledge.get(workflow.method_id)?.title } : null,
      asset_names: names,
      method_names: Object.fromEntries([...knowledge.values()].filter(k => k.title).map(k => [k.id, k.title])),
      files: (derived.length ? derived : assets).slice(-8).reverse().map(a => ({
        name: names[a.id], bytes: a.bytes, kind: a.origin === 'input' ? '原始附件' : '已保存结果',
        parents: (a.parents ?? []).slice(0, 2).map(id => names[id] ?? '来源文件')
      })),
      file_counts: { inputs: assets.length - derived.length, derived: derived.length },
      completion: state.completion
    };
    revision = state.revision;
    return cached;
  };
}
