import { createInterface } from 'node:readline';
import { safeTerminal } from '../util.js';
import { ToolActivity, toolCardText, logPreview } from './activity.js';
export function terminalObserver(output = process.stdout) {
  const tools = new ToolActivity();
  let streamed = false, textOpen = false;
  const closeText = () => { if (textOpen) output.write('\n\n'); textOpen = false; };
  const block = text => { closeText(); output.write(text + '\n\n'); };
  return event => {
    if (event.type === 'run_started') {
      tools.reset(); streamed = false;
      block(`━━ 开始解题 · ${safeTerminal(event.run_id)} ━━\n证据目录：${safeTerminal(event.run_dir)}`);
    } else if (event.type === 'turn_start') {
      streamed = false;
    } else if (event.type === 'context_refresh_end') {
      if (event.errorMessage) block(`上下文：${logPreview(event.errorMessage)}`);
    } else if (event.type === 'message_start' && event.message?.role === 'assistant') {
      streamed = false;
    } else if (event.type === 'message_update' && event.assistantMessageEvent?.type === 'text_delta') {
      if (!textOpen) output.write('模型说明（公开）：\n');
      // Sanitize streamed text without buffering or clipping the public explanation.
      output.write(safeTerminal(event.assistantMessageEvent.delta));
      textOpen = true; streamed = true;
    } else if (event.type === 'message_end' && event.message?.role === 'assistant') {
      const text = event.message.content?.filter(x => x.type === 'text').map(x => x.text).join('\n');
      if (!streamed && text) block(`模型说明（公开）：\n${safeTerminal(text)}`);
      closeText();
    } else if (event.type === 'tool_execution_start') {
      block(toolCardText(tools.start(event)));
    } else if (event.type === 'tool_execution_end') {
      block(toolCardText(tools.finish(event), { resultOnly: true }));
    } else if (event.type === 'run_finished') {
      for (const activity of tools.interrupt(event.status === 'cancelled' ? '已取消' : '已中断')) block(toolCardText(activity, { resultOnly: true }));
      block(`━━ 运行结束 · ${safeTerminal(event.status)} ━━`);
    } else if (event.type === 'compaction_start') block('上下文：正在压缩历史，业务状态与证据继续保留');
    else if (event.type === 'compaction_end') block(event.errorMessage ? `上下文压缩失败：${logPreview(event.errorMessage)}` : '上下文：历史压缩完成');
    else if (event.type === 'auto_retry_start') block(`模型重试：第 ${event.attempt}/${event.maxAttempts} 次`);
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
