import { assistantBlocks, assistantDelta, ThinkingBuffer } from './assistant-output.js';
import { createInterface } from 'node:readline';
import { safeTerminal } from '../util.js';
import { safeModelError } from '../pi/auth-errors.js';
import { ToolActivity, toolCardText, logPreview, provisioningText } from './activity.js';
export function terminalObserver(output = process.stdout, { showThinking = false } = {}) {
  const tools = new ToolActivity();
  let streamed = new Set(), textOpen = false, currentKind;
  const closeText = () => { if (textOpen) output.write('\n\n'); textOpen = false; };
  const block = text => { closeText(); output.write(text + '\n\n'); };
  let thinking;
  const finishThinking = (message, interrupted = false) => {
    if (showThinking) return;
    const parts = assistantBlocks(message).filter(part => part.kind === '思考');
    if (parts.length) { thinking ??= new ThinkingBuffer(); thinking.replace(parts); }
    if (thinking) block(`模型思考 · ${thinking.count} 个片段${interrupted ? ' · 已中断' : ''}（正文已折叠，--show-thinking 可显示原文）`);
    thinking = undefined;
  };
  return event => {
    if (event.type === 'run_started') {
      tools.reset(); streamed = new Set();
      block(`━━ 开始解题 · ${safeTerminal(event.run_id)} ━━\n证据目录：${safeTerminal(event.run_dir)}`);
    } else if (event.type === 'user_input_queued') {
      block('补充已排队，等待当前轮次结束');
    } else if (event.type === 'user_input_delivered') {
      block('补充已送入模型');
    } else if (event.type === 'skills_ready') {
      block(`Skills：已${event.restored ? '恢复记录中的' : '加载'} ${event.count} 个指南（正文按需读取）`);
      for (const diagnostic of event.diagnostics ?? []) block('Skill 提示：' + logPreview(diagnostic.message));
    } else if (event.type === 'skill_loaded') {
      block('使用 Skill：' + logPreview(event.name));
    } else if (event.type === 'route_review_required') {
      block('路线复核：' + logPreview(event.reason));
    } else if (event.type === 'tool_provisioning') {
      block('工具准备：' + logPreview(provisioningText(event)));
    } else if (event.type === 'run_resumed') {
      block(`恢复解题：${safeTerminal(event.parent_run_id)}\n已恢复 ${event.restored_files} 个文件位置，待处理 ${event.unresolved} 项。历史进程与工具环境需重新创建。`);
    } else if (event.type === 'turn_start') {
      streamed = new Set();
    } else if (event.type === 'context_refresh_start') {
      block(event.background ? `状态整理：后台更新进展与恢复路径（覆盖至第 ${event.sourceTurn} 轮），解题继续` : '状态整理：开始更新进展与恢复路径');
    } else if (event.type === 'context_summary_repair') {
      block(`状态整理：${logPreview(event.reason)}，正在修复（${event.attempt}/${event.maxAttempts}）`);
    } else if (event.type === 'context_refresh_end') {
      block(`状态整理：${event.errorMessage ? logPreview(event.errorMessage) : '已更新进展摘要、核心参考和恢复路径'}${event.warnings?.length ? '\n' + event.warnings.map(warning => logPreview(warning.message)).join('\n') : ''}`);
    } else if (event.type === 'message_start' && event.message?.role === 'assistant') {
      finishThinking(undefined, true);
      streamed = new Set();
    } else if (assistantDelta(event)) {
      const delta = assistantDelta(event);
      if (delta.kind === '思考' && !showThinking) {
        thinking ??= new ThinkingBuffer(); thinking.add(delta.index, delta.text); return;
      }
      if (currentKind !== delta.kind) closeText();
      if (!textOpen) output.write(delta.kind === '思考' ? '模型思考（服务返回）：\n' : '模型说明（公开）：\n');
      output.write(safeTerminal(delta.text));
      textOpen = true; currentKind = delta.kind; streamed.add(delta.index);
    } else if (event.type === 'message_end' && event.message?.role === 'assistant') {
      closeText();
      finishThinking(event.message, ['error', 'aborted'].includes(event.message.stopReason));
      for (const blockValue of assistantBlocks(event.message)) {
        if (blockValue.kind === '思考' && !showThinking) continue;
        if (!streamed.has(blockValue.index) && !streamed.has(blockValue.kind)) block(`${blockValue.kind === '思考' ? '模型思考（服务返回）' : '模型说明（公开）'}：\n${safeTerminal(blockValue.text)}`);
      }
      streamed = new Set();
    } else if (event.type === 'tool_execution_start') {
      finishThinking();
      block(toolCardText(tools.start(event)));
    } else if (event.type === 'tool_execution_update') {
      const activity = tools.update(event);
      if (activity) block(toolCardText(activity, { resultOnly: true }));
    } else if (event.type === 'tool_execution_end') {
      block(toolCardText(tools.finish(event), { resultOnly: true }));
    } else if (event.type === 'run_finished') {
      finishThinking(undefined, true);
      for (const activity of tools.interrupt(event.status === 'cancelled' ? '已取消' : '已中断')) block(toolCardText(activity, { resultOnly: true }));
      block(`━━ 运行结束 · ${safeTerminal(event.status)} ━━`);
    } else if (event.type === 'compaction_start') block('上下文：正在压缩历史，业务状态与证据继续保留');
    else if (event.type === 'compaction_end') block(event.errorMessage ? `上下文压缩失败：${logPreview(event.errorMessage)}` : '上下文：历史压缩完成');
    else if (event.type === 'auto_retry_start') block(`模型重试：第 ${event.attempt}/${event.maxAttempts} 次${event.errorMessage ? '\n原因：' + safeModelError(event.errorMessage) : ''}`);
    else if (event.type === 'extension_error') block(`扩展错误：${logPreview(event.error?.message ?? '详情见运行证据')}`);
  };
}
export function attachTerminal(controller) {
  if (!process.stdin.isTTY) return {
    ready: () => {},
    close: () => {}
  };
  const lines = createInterface({
    input: process.stdin,
    output: process.stdout,
    terminal: true
  });
  let controls;
  process.stdout.write('Running commands: /stop, /state, /flags; other text steers the agent. Ctrl+C stops.\n');
  lines.on('line', line => {
    if (line === '/stop') controller.abort();else if (line === '/state') process.stdout.write(safeTerminal(JSON.stringify(controls?.state(), null, 2)) + '\n');else if (line === '/flags') process.stdout.write(safeTerminal(JSON.stringify(controls?.candidates(), null, 2)) + '\n');else if (line.trim() && controls) Promise.resolve(controls.steer(line)).catch(error => process.stderr.write(safeTerminal(error.message) + '\n'));
  });
  lines.on('SIGINT', () => controller.abort());
  return {
    ready: value => {
      controls = value;
    },
    close: () => lines.close()
  };
}
