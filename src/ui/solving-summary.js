import { stripTerminalSequences } from '@earendil-works/pi-tui';
import { safeTerminal } from '../util.js';
import { provisioningState } from './activity.js';
const plain = value => safeTerminal(stripTerminalSequences(String(value ?? ''))).replaceAll('\t', '  ');
const values = value => Array.isArray(value) ? value.filter(x => x !== undefined && x !== null && x !== '') : value ? [value] : [];
export function fileSize(bytes) {
  if (!Number.isFinite(bytes)) return '大小未知';
  if (bytes < 1024) return `${bytes} B`;
  return bytes < 1024 * 1024 ? `${(bytes / 1024).toFixed(1)} KiB` : `${(bytes / 1024 / 1024).toFixed(1)} MiB`;
}
const statuses = { observed: '已取得观察', partial: '部分结果', not_found: '本次检查未发现', inconclusive: '尚不能判断', unsupported: '输入或方法不适用', missing_capability: '缺少所需能力', failed: '执行失败', completed: '任务已完成', unsolved: '本次未解出', cancelled: '已停止', closed: '已关闭', running: '运行中' };
const blockers = { evidence_missing: '缺少区分方向的证据', prerequisite_unknown: '方法前提尚未确认', hypothesis_rejected: '原假设已被反驳', capability_missing: '缺少所需工具或访问能力', composition_unclear: '已有能力与目标尚未连通' };
export function solvingSummary(state) {
  const p = state.progress ?? {}, summary = p.summary;
  // Internal references remain in durable state; this view names their meaning.
  const human = value => plain(value)
    .replace(/\bassets-\d+\b/g, id => plain(p.asset_names?.[id] ?? '相关文件'))
    .replace(/method\/[a-z0-9-]+/g, id => plain(p.method_names?.[id] ?? '所选方法'))
    .replace(/\b(?:observations|experiments|claims|summaries|sources)-\d+\b/g, '相关记录');
  const sections = [];
  const add = (title, items, empty, literal = false) => {
    const lines = values(items).map(literal ? plain : human);
    if (lines.length || empty) sections.push({ title, items: lines.length ? lines : [empty] });
  };
  const completion = p.completion;
  if (state.candidates?.length) add('候选结果', [
    ...state.candidates.slice(-2).map(c => plain(c.candidate ?? c.text ?? '已提交候选')),
    '已提交；平台接受情况未验证', '/flags 查看全部原文'
  ], undefined, true);
  add('当前目标', summary?.goal ?? p.task, '等待导入题目并开始解题');
  if (state.currentActivity) add('正在进行', state.currentActivity.purpose);
  else if (state.latestActivity) add('最近操作', `${state.latestActivity.purpose} · ${state.latestActivity.status}`);
  else if (p.workflow) add('最近操作', `${p.workflow.method_title ?? '工作流实验'} · ${statuses[p.workflow.status] ?? p.workflow.status}`);
  add('当前方案', summary?.approach, summary ? '尚未说明当前方案' : '等待 Agent 发布公开摘要');
  const claims = p.claims ?? [];
  add('关键线索', summary?.facts ?? claims.filter(c => c.state === 'supported').map(c => c.statement), '暂无已整理线索');
  add('待验证假设', summary?.hypotheses ?? claims.filter(c => c.state === 'hypothesis').map(c => c.statement), '暂无待验证假设');
  add('下一步', summary?.next_steps, completion ? '本次运行已结束，可阅读解题报告' : '等待下一次实验安排');
  add('阻塞与缺口', [...values(summary?.blockers), ...values(summary?.remaining_gap), ...(summary?.blocker_kind ? [blockers[summary.blocker_kind] ?? summary.blocker_kind] : [])], '暂无已报告阻塞');
  add('已有能力', summary?.achieved_capabilities);
  add('工具准备', (p.tool_environments ?? []).map(x => `${x.capabilities.join('、')} · ${provisioningState(x.state)}${x.cache_hit ? ' · 缓存命中' : ''}${x.error ? '\n' + x.error.message : ''}`));
  add('更正与撤回', summary?.corrections);
  if (p.experiments?.length) add('最近实验', p.experiments.map(e => `${e.purpose} · ${({ supports: '模型报告支持假设', contradicts: '模型报告反驳假设', unknown: '尚无确定结论' })[e.reported_result] ?? '结果待解释'}${e.next_action ? '\n下一步：' + e.next_action : ''}`));
  const files = p.files ?? [];
  add('文件与中间结果', files.map(file => `${file.name} · ${fileSize(file.bytes)}${file.parents?.length ? '\n来自：' + file.parents.join('、') : '\n' + file.kind}`), '尚未保存中间文件');
  if (p.file_counts?.derived > files.length) add('更多文件', `已保存 ${p.file_counts.derived} 个结果，以上展示最近 ${files.length} 个`);
  if (completion) add('运行结果', [statuses[completion.outcome] ?? completion.outcome, completion.summary, ...values(completion.unresolved), '/writeup 阅读解题报告']);
  add('运行信息', [`附件授权：${state.consent ? '已授权' : '未授权'}`, `思考强度：${state.thinking ?? 'off'}`, state.busy ? '/stop 停止本次运行' : '/run 开始解题']);
  let updated = '';
  if (summary?.time && Number.isFinite(Date.parse(summary.time))) updated = new Date(summary.time).toLocaleTimeString('zh-CN', { hour12: false });
  return {
    title: '解题进展',
    subtitle: summary ? `模型整理${updated ? ' · 更新于 ' + updated : ''}` : '摘要待发布 · 操作状态实时更新',
    notice: p.summary_pending ? '有新观察，以下为上次摘要' : '', sections
  };
}
export function summaryDocument(state) {
  const summary = solvingSummary(state);
  return [summary.title, summary.subtitle, summary.notice, ...summary.sections.flatMap(section => ['', section.title, ...section.items.map(item => '• ' + item.replaceAll('\n', '\n  '))])].filter(line => line !== undefined).join('\n');
}
