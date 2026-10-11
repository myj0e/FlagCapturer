import test from 'node:test';
import assert from 'node:assert/strict';
import { ToolActivity, describeTool, summarizeTool, toolCardText, logPreview } from '../src/ui/activity.js';
import { terminalObserver } from '../src/ui/terminal.js';
const reply = value => ({ content: [{ type: 'text', text: JSON.stringify(value) }] });

test('tool descriptions expose actual argv and bounded code without treating grep patterns as programs', () => {
  const python = describeTool('command_run', { argv: ['python3', '-I', '-c', 'from pathlib import Path\nprint(Path("/challenge/data").read_text())'] });
  assert.match(python.purpose, /Python/);
  assert.match(python.details[0].text, /python3 -I -c/);
  assert.match(python.details[1].text, /Path/);
  const grep = describeTool('command_run', { argv: ['grep', '-e', 'pattern with spaces', '/challenge/data'] });
  assert.equal(grep.details.length, 1);
  assert.match(grep.details[0].text, /"pattern with spaces"/);
  const saved = describeTool('script_save', { language: 'python', source: 'line\n'.repeat(1000) });
  assert.match(saved.details[0].text, /5000/);
  assert.ok(saved.details[1].text.length < 800);
  assert.match(saved.details[1].text, /已省略/);
});

test('log summaries sanitize terminal controls and credentials, retain useful errors and bound output', () => {
  const preview = logPreview('Authorization: Bearer synthetic-secret\napi_key="another secret"\n\x1b[31mvisible\x1b[0m\x1b]52;c;hidden\x07');
  assert.ok(!preview.includes('synthetic-secret'));
  assert.ok(!preview.includes('another secret'));
  assert.ok(!preview.includes('\x1b'));
  assert.match(preview, /visible/);
  const summary = summarizeTool('command_run', reply({ exit_code: 2, stdout: 'x'.repeat(10000), stderr: 'bad input\ntrace', observation_id: 'observations-7', coverage: { truncated: true } }), true);
  const text = summary.details.map(x => x.label + ':' + x.text).join('\n');
  assert.equal(summary.status, '执行失败');
  assert.match(text, /退出码:2/);
  assert.match(text, /stderr:bad input/);
  assert.match(text, /observations-7/);
  assert.match(text, /已省略/);
  assert.ok(text.length < 2400);
  const plainError = summarizeTool('command_run', { content: [{ type: 'text', text: 'Sandbox unavailable' }] }, true);
  assert.match(plainError.details[0].text, /Sandbox unavailable/);
});

test('workflow results show the experiment, outputs and coverage instead of a JSON dump', () => {
  const description = describeTool('workflow_run', { pack: 'forensics', operation: 'extract', asset_id: 'assets-2', parameters: { entry_index: 3 } });
  assert.match(description.purpose, /提取.*校验/);
  assert.match(description.details[0].text, /assets-2/);
  const result = summarizeTool('workflow_run', reply({ status: 'partial', stdout: '{"large":"opaque"}', result: { status: 'partial', entries: [{ index: 0, name: 'source.py', bytes: 100 }], coverage: { truncated: true } }, output_assets: [{ id: 'assets-3', bytes: 100 }], observation_id: 'observations-4' }));
  const text = result.details.map(x => x.text).join('\n');
  assert.equal(result.status, '部分结果');
  assert.match(text, /source.py/);
  assert.match(text, /assets-3/);
  assert.ok(!text.includes('opaque'));
  assert.match(text, /不能据此推断/);
});

test('call IDs preserve correlation and reported durations even when results arrive out of order', () => {
  let now = 100;
  const activity = new ToolActivity(() => now);
  const first = activity.start({ toolCallId: 'a', toolName: 'command_run', args: { argv: ['file', 'first'] } });
  const second = activity.start({ toolCallId: 'b', toolName: 'command_run', args: { argv: ['strings', 'second'] } });
  now = 1200;
  assert.equal(activity.finish({ toolCallId: 'b', toolName: 'command_run', result: reply({ stdout: 'second output' }), durationMs: 73 }), second);
  assert.equal(second.duration, 73);
  assert.equal(activity.finish({ toolCallId: 'a', toolName: 'command_run', result: reply({ stdout: 'first output' }) }), first);
  assert.equal(first.index, 1);
  assert.equal(first.duration, 1100);
  assert.match(toolCardText(first), /识别文件格式/);
  assert.match(toolCardText(first), /first output/);
  assert.ok(!toolCardText(first).includes('second output'));
  assert.equal(activity.calls.size, 0);
});

test('CLI separates public explanation, command calls and results with stable IDs', () => {
  let text = '';
  const observe = terminalObserver({ write: value => { text += value; } });
  observe({ type: 'run_started', run_id: 'test', run_dir: '/tmp/evidence' });
  observe({ type: 'turn_start' });
  observe({ type: 'message_update', assistantMessageEvent: { type: 'thinking_delta', delta: 'PRIVATE' } });
  observe({ type: 'message_update', assistantMessageEvent: { type: 'text_delta', delta: '先检查文件格式。' } });
  observe({ type: 'message_end', message: { role: 'assistant', content: [{ type: 'text', text: '先检查文件格式。' }, { type: 'thinking', thinking: 'PRIVATE' }] } });
  observe({ type: 'tool_execution_start', toolCallId: 'test-1', toolName: 'command_run', args: { argv: ['file', '/challenge/test'] } });
  observe({ type: 'tool_execution_end', toolCallId: 'test-1', toolName: 'command_run', durationMs: 150, result: reply({ exit_code: 0, stdout: 'PNG image data', observation_id: 'observations-1' }), isError: false });
  observe({ type: 'run_finished', status: 'completed' });
  assert.doesNotMatch(text, /第\s*\d+\s*轮/);
  assert.equal(text.split('先检查文件格式。').length - 1, 1);
  assert.equal(text.split('工具 #1').length - 1, 2);
  assert.match(text, /file \/challenge\/test/);
  assert.match(text, /stdout：PNG image data/);
  assert.match(text, /0.15s/);
  assert.ok(!text.includes('PRIVATE'));
  assert.match(text, /先检查文件格式。\n\n┌─/);
});

test('unfinished calls are explicitly closed on stop and binary blocks never enter text logs', () => {
  let text = '';
  const observe = terminalObserver({ write: value => { text += value; } });
  observe({ type: 'tool_execution_start', toolName: 'image_view', toolCallId: 'image', args: { path: '/work/image.png' } });
  observe({ type: 'tool_execution_end', toolName: 'image_view', toolCallId: 'image', result: { content: [...reply({ width: 2, height: 3 }).content, { type: 'image', data: 'BINARY_SECRET' }] } });
  observe({ type: 'tool_execution_start', toolName: 'session_read', toolCallId: 'wait', args: { session_id: 's1', collect_seconds: 60 } });
  observe({ type: 'run_finished', status: 'cancelled' });
  assert.match(text, /2 × 3/);
  assert.ok(!text.includes('BINARY_SECRET'));
  assert.match(text, /已取消/);
  assert.match(text, /未收到此调用的最终返回/);
});

test('long command output exposes actual tail separately from a clipped head', () => {
  const summary = summarizeTool('command_run', reply({ exit_code: 0, stdout: 'progress\n'.repeat(400), stdout_tail: { text: 'progress\n'.repeat(50) + 'final useful result', offset: 6000, source_bytes: 7024 }, coverage: { truncated: true }, observation_id: 'observations-9' }));
  const tail = summary.details.find(x => x.label.startsWith('stdout 尾部'));
  assert.match(tail.text, /final useful result/);
  assert.match(tail.label, /6000/);
});

test('unfamiliar result shapes cannot crash the event subscriber', () => {
  const summary = summarizeTool('workflow_run', reply({ result: { entries: [null] } }));
  assert.equal(summary.status, '已返回（摘要不可用）');
  assert.match(summary.details[0].text, /完整结果/);
  const activity = new ToolActivity();
  activity.start({ toolName: 'unknown', toolCallId: 'broken', args: null });
  assert.doesNotThrow(() => activity.finish({ toolName: 'unknown', toolCallId: 'broken', result: { content: {} } }));
});
