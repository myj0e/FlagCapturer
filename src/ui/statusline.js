export function elapsedTime(milliseconds = 0) {
  const seconds = Number.isFinite(milliseconds) ? Math.max(0, Math.floor(milliseconds / 1000)) : 0;
  const pad = value => String(value).padStart(2, '0');
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}:${pad(seconds % 60)}`;
  return `${Math.floor(seconds / 3600)}:${pad(Math.floor(seconds / 60) % 60)}:${pad(seconds % 60)}`;
}
export function compactTokens(value) {
  if (!Number.isFinite(value) || value < 0) return '—';
  if (value < 1000) return String(Math.floor(value));
  // Promote a rounded 1000K to 1M at the unit boundary.
  if (value < 1000000 && Math.round(value / 100) < 10000) return `${Number((value / 1000).toFixed(1))}K`;
  return `${Number((value / 1000000).toFixed(1))}M`;
}
export function statusline(state) {
  const usage = state.usage, context = state.context;
  const window = context?.contextWindow ?? state.contextWindow;
  const known = Number.isFinite(context?.tokens) && context.tokens >= 0 && Number.isFinite(window) && window > 0;
  // Pi returns null after compaction until a fresh response; never reuse old usage or show 0%.
  const contextText = known
    ? `上下文 ${Number((context.tokens / window * 100).toFixed(1))}% (${compactTokens(context.tokens)}/${compactTokens(window)})`
    : `上下文 待更新 (—/${compactTokens(window)})`;
  return ` ${elapsedTime(state.elapsed)} · ${usage ? `${compactTokens(usage.tokens?.total ?? 0)} tokens` : '尚无模型用量'} · ${contextText} · 工具 ${state.tools} · Flag ${state.candidates.length}${usage && state.provider.startsWith('ctf-custom-') ? ' · 成本未知' : usage ? ` · $${Number(usage.cost ?? 0).toFixed(4)} 估算` : ''}`;
}
