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
  if (p.skills?.length) add('使用的指南', [...new Set(p.skills.map(s => s.resource.replace(/^(builtin|vendor|user|custom-\d+)\//, '').replace(/\/SKILL\.md$/, '')))]);
  if (p.route_review) add('历史路线复核', [p.route_review.reason ?? p.route_review.rationale, '记录仅供参考，当前进展以最新摘要为准']);
  const claims = p.claims ?? [];
  add('关键线索', summary?.facts ?? claims.filter(c => c.state === 'supported').map(c => c.statement), '暂无已整理线索');
  add('待验证假设', summary?.hypotheses ?? claims.filter(c => c.state === 'hypothesis').map(c => c.statement), '暂无待验证假设');
  add('失败尝试（未排除）', summary?.unverified_attempts?.map(item => `${item.attempt}：${item.result}\n范围：${item.scope}；依据不足，不能据此排除该方向`));
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
  if (state.networkTargets?.length) add('远端连接范围', state.networkTargets.slice(-8).map(t => `${t.host}:${t.port} · ${t.state === 'active' ? '已授权' : t.state} · ${t.capabilities.join('/')}\n到期：${t.expires_at}`));
  add('运行信息', [`附件授权：${state.consent ? '已授权' : '未授权'}`, `思考强度：${state.thinking ?? 'off'}`, state.busy ? '/stop 停止本次运行' : '/run 开始解题']);
  let updated = '';
  const displayTime = summary?.generated_time ?? summary?.time;
  if (displayTime && Number.isFinite(Date.parse(displayTime))) updated = new Date(displayTime).toLocaleTimeString('zh-CN', { hour12: false });
  const maintenance = state.contextMaintenance;
  const notices = [];
  if (maintenance?.lastSuccessAt && Number.isFinite(Date.parse(maintenance.lastSuccessAt))) notices.push('最近自动整理成功：' + new Date(maintenance.lastSuccessAt).toLocaleTimeString('zh-CN', { hour12: false }));
  if (maintenance?.state === 'failed') notices.push(`${maintenance.reason} · ${summary ? '当前展示上次摘要' : '尚无有效自动摘要'}${maintenance.retryInTurns !== null && maintenance.retryInTurns !== undefined ? ` · ${maintenance.retryInTurns} 个模型轮次后重试` : ''}`);
  else if (maintenance?.state === 'running') notices.push(maintenance.operation === 'refresh' ? '后台整理中，解题继续' : '正在压缩上下文');
  else if (maintenance?.state === 'ready') notices.push('后台摘要已生成，等待下次请求采用');
  else if (maintenance?.state === 'repairing') notices.push(`${maintenance.operation === 'refresh' ? '后台' : ''}正在修复总结回复：` + maintenance.reason);
  else if (maintenance?.state === 'cancelled') notices.push('状态整理已取消，保留已有摘要');
  if (p.summary_pending) notices.push('有新观察，以下为上次摘要');
  if (Number.isInteger(summary?.source_turn)) notices.push(`摘要覆盖至第 ${summary.source_turn} 轮${p.summary_pending ? '，之后有新进展' : ''}`);
  if (summary?.source_time && Number.isFinite(Date.parse(summary.source_time))) notices.push('取材截至：' + new Date(summary.source_time).toLocaleTimeString('zh-CN', { hour12: false }));
  return {
    title: '解题进展',
    subtitle: summary ? `模型整理${updated ? (summary.source_time ? ' · 生成于 ' : ' · 更新于 ') + updated : ''}` : '摘要待发布 · 操作状态实时更新',
    notice: notices.join('\n'), sections
  };
}
export function summaryDocument(state) {
  const summary = solvingSummary(state);
  return [summary.title, summary.subtitle, summary.notice, ...summary.sections.flatMap(section => ['', section.title, ...section.items.map(item => '• ' + item.replaceAll('\n', '\n  '))])].filter(line => line !== undefined).join('\n');
}
