export function parseMemoryResponse(text) {
  const trimmed = text.trim();
  const fenced = /^```(?:json)?[ \t]*\r?\n([\s\S]*?)\r?\n```$/i.exec(trimmed);
  // Accept only a complete JSON document or one complete wrapping code fence.
  // Never scrape a guessed object out of prose, multiple documents or trailing text.
  return JSON.parse(fenced ? fenced[1] : trimmed);
}

export function maintenanceFailure(error) {
  if (error instanceof SyntaxError) return { kind: 'json', reason: 'JSON 格式错误' };
  const text = error.message ?? '';
  if (/Unknown memory|Duplicate memory/.test(text)) return { kind: 'reference', reason: '证据或文件引用无效' };
  if (/snapshot budget/.test(text)) return { kind: 'output_budget', reason: '总结输出超过长度限制' };
  if (/Maintenance response did not finish/.test(text)) return { kind: 'response', reason: '模型回复未完整结束' };
  if (/RunState changed|stale context/.test(text)) return { kind: 'stale', reason: '总结期间任务状态发生变化' };
  if (/Invalid|Missing|Unknown property|Expected|Unsupported choice|outside range|Reserved property|Duplicate|require scoped evidence/.test(text)) return { kind: 'schema', reason: '总结字段不符合要求' };
  if (/too large|exceeds.*input|bounded input/.test(text)) return { kind: 'input_budget', reason: '总结输入超过安全长度' };
  return { kind: 'request', reason: '总结请求或状态校验失败' };
}

export const repairable = error => ['json', 'reference', 'schema', 'response', 'output_budget'].includes(maintenanceFailure(error).kind);
