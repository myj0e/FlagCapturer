import { stripTerminalSequences } from '@earendil-works/pi-tui';
import { safeTerminal } from '../util.js';

// Presentation only: no attachment reads, tool execution, or model inference.
const plain = value => safeTerminal(stripTerminalSequences(String(value ?? ''))).replaceAll('\t', '  ');
const redact = text => text
  .replace(/\bBearer\s+[^\s'";]+/gi, 'Bearer [已隐藏]')
  .replace(/((?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|authorization)["']?\s*[=:]\s*)(?:"[^"\n]*"|'[^'\n]*'|[^\s,;]+)/gi, '$1[已隐藏]');
export function logPreview(value, limit = 900, lines = 6) {
  const text = redact(plain(value));
  const selected = text.split('\n').slice(0, lines).join('\n');
  return selected.length > limit || text.split('\n').length > lines ? selected.slice(0, limit) + '\n…（已省略，完整内容见运行证据）' : selected;
}
const purposes = {
  tool_request: '检查或准备所需工具', tool_status: '查看工具准备状态', tool_exec: '在已验证环境中执行分析',
  challenge_list: '查看题目附件清单', challenge_read_text: '读取附件文本', challenge_read_bytes: '查看附件的指定字节',
  artifact_read: '读取已保存证据的指定范围', artifact_tail: '查看证据末尾', artifact_find: '在证据中查找文本', artifact_export: '保存工作文件和来源记录',
  asset_read: '查看已校验产物', asset_capture: '保存脚本产物并关联来源',
  command_run: '在沙箱中执行命令', session_start: '启动后台或交互进程', session_send: '向交互进程发送数据', session_read: '收集后台进程输出', session_close: '结束后台进程',
  script_save: '保存可复现的分析脚本', script_run: '运行已保存脚本', script_start: '后台运行已保存脚本',
  workflow_discover: '查看可用方法和题型线索', workflow_search: '根据线索查找解题方法', workflow_read: '阅读方法的前提与验证步骤', workflow_environment: '检查工具、模块与版本', workflow_run: '执行工作流实验',
  state_read: '恢复当前解题状态', observation_get: '查看历史观察与证据', summary_update: '更新公开解题进展',
  claim_record: '记录或修正假设', experiment_record: '记录实验结果及下一步', candidate_submit: '提交题目候选结果', candidate_check: '检查候选与本地数据的关系', run_complete: '保存解题报告并结束任务',
  image_view: '查看图像或指定区域', http_request: '向授权目标发送 HTTP 请求', remote_tcp_exchange: '与授权目标交换 TCP 数据', memory_search: '查找受审参考资料', memory_read: '阅读受审参考资料'
};
const operations = {
  decode: '严格解码并保存完整产物', rsa: '检查 RSA 参数及小因子', triage: '检查文件签名与可打印字符串', archive: '查看 ZIP 条目清单', extract: '提取一个 ZIP 成员并校验', slice: '截取指定字节范围', reassemble: '按显式顺序重组分片', png: '检查 PNG 块、校验和与尾随内容', ppm_lsb: '验证指定的 RGB LSB 隐写布局', elf: '检查 ELF 结构与保护线索', disassemble: '查看程序反汇编', response: '检查已保存的 HTTP 响应'
};
const commandPurposes = {
  python: '运行 Python 分析代码', python3: '运行 Python 分析代码', node: '运行 JavaScript 分析代码', sh: '运行沙箱 Shell 指令', bash: '运行沙箱 Shell 指令',
  file: '识别文件格式', strings: '提取可打印字符串', xxd: '查看十六进制字节', hexdump: '查看十六进制字节', objdump: '检查二进制结构或汇编', readelf: '检查 ELF 信息', gdb: '调试程序', exiftool: '检查文件元数据', tshark: '分析网络流量', unzip: '检查或提取 ZIP 文件', ls: '查看目录内容', cat: '读取文件内容', grep: '查找文本模式', rg: '查找文本模式', find: '查找文件', sha256sum: '计算文件哈希'
};
function commandDetails(argv) {
  if (!Array.isArray(argv)) return [];
  const binary = String(argv[0] ?? '').split('/').at(-1);
  const flags = ['python', 'python3'].includes(binary) ? ['-c'] : binary === 'node' ? ['-e', '--eval', '-p', '--print'] : ['bash', 'sh'].includes(binary) ? ['-c', '-lc', '-ic', '-xc'] : [];
  const inline = argv.findIndex((arg, i) => i > 0 && flags.includes(arg));
  const display = [...argv];
  const code = inline >= 0 ? display[inline + 1] : undefined;
  if (code !== undefined) display[inline + 1] = '<内联代码>';
  // Render argv boundaries for readability; this is not a shell command to execute.
  for (let i = 1; i < display.length; i++) if (/^(?:--?(?:password|token|api-key)|-H)$/i.test(String(display[i - 1]))) {
    if (display[i - 1] !== '-H' || /authorization/i.test(String(display[i]))) display[i] = '[已隐藏]';
  }
  const quote = value => /^[\w./:=+-]+$/.test(String(value)) ? String(value) : JSON.stringify(value);
  return [{ label: '指令', text: logPreview(display.map(quote).join(' '), 700, 2) }, ...(code !== undefined ? [{ label: '代码预览', text: logPreview(code, 700, 6) }] : [])];
}
export function describeTool(name, args = {}) {
  args = args && typeof args === 'object' ? args : {};
  const binary = Array.isArray(args.argv) ? String(args.argv[0] ?? '').split('/').at(-1) : '';
  const purpose = name === 'workflow_run' ? operations[args.operation] ?? purposes[name] : name === 'command_run' || name === 'session_start' ? commandPurposes[binary] ?? purposes[name] : purposes[name] ?? '执行扩展工具';
  const details = [];
  if (Array.isArray(args.argv)) details.push(...commandDetails(args.argv));
  if (name === 'script_save') {
    details.push({ label: '脚本', text: `${args.language ?? 'unknown'} · ${String(args.source ?? '').length} 字符` });
    details.push({ label: '代码预览', text: logPreview(args.source, 700, 6) });
  }
  const fields = ['capabilities', 'preparation_id', 'environment_id', 'inputs', 'outputs', 'pack', 'operation', 'method_id', 'id', 'path', 'asset_id', 'asset_ids', 'parent_asset_ids', 'script_id', 'session_id', 'section', 'offset', 'length', 'encoding', 'query', 'literal', 'collect_seconds', 'selector', 'candidate_id', 'subtask', 'host', 'port', 'method', 'crop', 'parameters', 'positions', 'retry_reason'];
  const values = fields.filter(key => args[key] !== undefined).map(key => `${key}=${typeof args[key] === 'object' ? JSON.stringify(args[key]) : args[key]}`);
  if (values.length) details.push({ label: '参数', text: logPreview(values.join(' · '), 850, 3) });
  for (const key of ['purpose', 'statement', 'goal', 'expected_observation', 'next_action', 'transformation']) if (args[key]) details.push({ label: ({ purpose: '实验目的', statement: '假设', goal: '目标', expected_observation: '预期现象', next_action: '下一步', transformation: '转换说明' })[key], text: logPreview(args[key], 350, 2) });
  if (args.data_base64) details.push({ label: '发送', text: `${Math.floor(String(args.data_base64).length * 3 / 4) - (String(args.data_base64).match(/=*$/)?.[0].length ?? 0)} 字节（不展开二进制数据）` });
  return { purpose, details };
}
function payloadOf(result) {
  const texts = result?.content?.filter(block => block.type === 'text').map(block => block.text) ?? [];
  const text = texts.join('\n');
  try {
    const payload = JSON.parse(text);
    return payload && typeof payload === 'object' && !Array.isArray(payload) ? { payload } : { payload: {}, raw: text };
  } catch { return { payload: {}, raw: text }; }
}
const statusNames = { observed: '已取得观察', partial: '部分结果', not_found: '本次检查未发现', inconclusive: '尚不能判断', unsupported: '输入或方法不适用', missing_capability: '缺少所需能力', failed: '执行失败', running: '进程运行中', exited: '进程已退出', closed: '进程已关闭', timed_out: '执行超时', output_limit: '达到输出上限', completed: '已完成', unsolved: '未解出' };
function summarizePayload(name, reply, isError = false) {
  const { payload: p, raw } = payloadOf(reply);
  const details = [];
  const add = (label, value, limit = 800, lines = 5) => {
    if (value !== undefined && value !== null && value !== '') details.push({ label, text: logPreview(value, limit, lines) });
  };
  let status = p.cancelled ? '已取消' : p.timed_out ? '执行超时' : statusNames[p.status ?? p.state ?? p.outcome] ?? (isError ? '执行失败' : '已返回');
  if (isError && !p.status && !p.state) status = '执行失败';
  if (p.exit_code !== undefined && p.exit_code !== null) add('退出码', p.exit_code);
  if (p.passed !== undefined) add('局部检查', p.passed ? '关系成立（不代表平台接受）' : '关系未成立');
  if (p.session_id) add('进程', `${p.session_id}${p.elapsed_ms !== undefined ? ` · 已运行 ${(p.elapsed_ms / 1000).toFixed(1)}s` : ''}${p.idle_ms !== undefined ? ` · 无输出 ${(p.idle_ms / 1000).toFixed(1)}s` : ''}`);
  if (p.script_id || name === 'script_save') add('脚本', `${p.script_id ?? p.id ?? ''}${p.path ? ' · ' + p.path : ''}`);
  if (p.width && p.height) add('图像', `${p.width} × ${p.height} 像素`);
  if (p.coverage?.received_bytes !== undefined) add('接收', `${p.coverage.received_bytes} 字节${p.truncated ? ' · 已截断' : ''}`);
  if (p.positions) add('匹配', `${p.total_matches_in_scan ?? p.positions.length} 处 · 偏移 ${p.positions.slice(0, 10).join(', ') || '无'}`);
  if (p.bytes !== undefined) add('大小', `${p.bytes} 字节${p.id ? ' · ' + p.id : ''}`);
  if (p.candidate && name === 'candidate_submit') add('候选', `${p.candidate}\n已提交；平台接受情况未验证`, 600, 3);
  if (p.summary) add('摘要', p.summary);
  for (const [key, label] of [['facts', '事实'], ['next_steps', '下一步'], ['blockers', '阻塞'], ['unresolved', '未完成项'], ['achieved_capabilities', '已有能力']]) {
    if (p[key]?.length) add(label, p[key].slice(0, 3).join('\n'), 600, 3);
  }
  if (p.statement) add('判断', `${p.state} · ${p.statement}`);
  if (p.reported_result) add('实验结论', `${p.reported_result}（模型报告）${p.next_action ? '\n下一步：' + p.next_action : ''}`);
  if (p.packs) add('工作流', (Array.isArray(p.packs) ? p.packs : Object.keys(p.packs)).join(' · '));
  if (p.matches) add('命中', p.matches.length ? p.matches.slice(0, 5).map(x => `${x.id}${x.title ? ' · ' + x.title : ''}`).join('\n') : '没有匹配项');
  if (p.method) add('方法', `${p.method.title}\n${p.method.probe ?? p.method.summary ?? ''}`);
  if (p.metadata) add('操作', `${p.metadata.name} · ${(p.metadata.operations ?? []).join(', ')}`);
  if (p.entry) add('参考资料', `${p.entry.id}\n${p.entry.body ?? ''}`, 500, 4);
  if (p.inventory) add('附件', p.inventory.slice(0, 6).map(x => `${x.path} · ${x.bytes} 字节`).join('\n'));
  if (p.records) add('记录', p.records.length ? p.records.slice(0, 5).map(x => `${x.id ?? ''} · ${x.title ?? x.statement ?? x.path ?? x.purpose ?? x.origin ?? ''}`).join('\n') : '无记录');
  if (p.counts) add('状态', Object.entries(p.counts).map(([key, count]) => `${key} ${count}`).join(' · '));
  if (p.capabilities && !Array.isArray(p.capabilities)) {
    const missing = p.unmet_required;
    add('能力', missing?.binaries?.length || missing?.modules?.length ? `缺少必需项：${[...missing.binaries, ...missing.modules].join(', ')}` : '所检查的必需项可用；兼容性仍需结合版本确认');
    add('工具路径', Object.entries(p.capabilities.binaries ?? {}).slice(0, 5).map(([key, value]) => `${key}: ${value.path ?? '未找到'}`).join('\n'));
    add('模块', Object.entries(p.capabilities.modules ?? {}).map(([key, value]) => `${key}: ${value.importable ? value.version ?? '可导入，版本未知' : '不可导入'}`).join('\n'));
  }
  if (p.preparation_id) add('工具环境', `${p.preparation_id} · ${provisioningState(p.state)}${p.cache_hit ? ' · 缓存命中' : ''}${p.environment_id ? '\n' + p.environment_id : ''}`);
  if (p.catalog) add('可申请能力', p.catalog.map(x => `${x.id} · ${x.title}`).join('\n'), 1000, 12);
  const r = p.result;
  if (r && typeof r === 'object') {
    if (r.signatures) add('格式线索', r.signatures.length ? r.signatures.slice(0, 6).map(x => `${x.format} @ ${x.offset}`).join(' · ') : '未发现已知签名（仅限本次检查）');
    if (r.magic_hex) add('文件头', r.magic_hex);
    if (r.candidates) add('解码', r.candidates.length ? r.candidates.map(x => `${x.encoding} · ${x.bytes ?? '?'} 字节`).join('\n') : '没有符合条件的编码');
    if (r.entries) add('归档条目', r.entries.slice(0, 5).map(x => `#${x.index ?? '?'} ${x.name} · ${x.bytes} 字节`).join('\n') || '空归档');
    if (r.chunks) add('PNG', `已展示 ${r.chunks.length} 个块 · IEND ${r.has_iend ? '已发现' : '未确认'} · 尾随 ${r.trailing_bytes ?? '?'} 字节`);
    if (r.bits) add('ELF', `${r.bits} 位 ${r.endian} · PIE ${r.pie} · canary ${r.canary}`);
    if (r.modulus_bits) add('RSA', `${r.modulus_bits} 位${r.p ? ' · 找到因子，结构与可逆性需结合结果判断' : ' · 未获得因子'}`);
    if (r.headers) add('HTTP', r.headers, 600, 4);
    if (r.ascii_strings?.length) add('字符串', r.ascii_strings.slice(0, 4).map(x => `@${x.offset} ${x.text}`).join('\n'), 600, 4);
    if (r.entry) add('提取', `#${r.entry.index} ${r.entry.name} · CRC ${r.entry.crc_valid ? '通过' : '未确认'}`);
    if (r.duplicate_content) add('重组提示', '存在内容相同的分片，请结合位置判断是否合理');
    if (r.reason || r.error) add('说明', r.reason ?? r.error);
  } else {
    add('stdout', p.stdout, 1200, 8);
  }
  add('stderr', p.stderr, 800, 5);
  for (const stream of ['stdout', 'stderr']) {
    if (stream === 'stdout' && p.result) continue;
    const tail = p[stream + '_tail'];
    if (tail?.text) add(`${stream} 尾部（偏移 ${tail.offset}）`, tail.text.split('\n').slice(-5).join('\n').slice(-700), 700, 6);
    else if (!p.result && typeof p[stream] === 'string' && (p[stream].length > (stream === 'stdout' ? 1200 : 800) || p[stream].split('\n').length > (stream === 'stdout' ? 8 : 5))) {
      add(`${stream} 预览末尾`, p[stream].split('\n').slice(-4).join('\n').slice(-500), 500, 4);
    }
  }
  add('进程输出', p.output, 1200, 8);
  add('内容预览', p.text ?? p.data ?? p.body_preview, 1000, 7);
  if (p.output_assets?.length) add('产物', p.output_assets.slice(0, 5).map(x => `${x.id} · ${x.bytes} 字节${x.transformation?.label ? ' · ' + x.transformation.label : ''}`).join('\n'));
  if (p.error) add('错误', typeof p.error === 'string' ? p.error : p.error.message ?? '工具返回错误');
  if (p.message) add('说明', p.message);
  if (raw) add(isError ? '错误' : '返回', raw, 1100, 8);
  if (p.repeat_notice) add('重复提示', '相同输入和参数已有尝试记录；请检查之前的结果或说明重试原因');
  const truncated = p.truncated || p.result_view_truncated || p.coverage?.truncated || p.result?.coverage?.truncated;
  if (truncated) add('覆盖', '输出或检查范围有限；完整内容见证据，不能据此推断不存在其他内容');
  const refs = [p.observation_id, p.source_ref, p.full_result?.artifact].filter(Boolean);
  if (refs.length) add('证据', refs.join(' · '));
  if (!details.length) add('结果', isError ? '工具返回错误；详细数据保存在运行证据中' : '工具已返回；详细数据保存在运行证据中');
  return { status, details, isError: Boolean(isError) };
}

export function summarizeTool(name, reply, isError = false) {
  try { return summarizePayload(name, reply, isError); }
  catch {
    // Malformed or unfamiliar tool data must not stop the agent event subscriber.
    return { status: isError ? '执行失败' : '已返回（摘要不可用）', isError: Boolean(isError),
      details: [{ label: '说明', text: '返回格式无法生成摘要；请查看运行证据中的完整结果' }] };
  }
}

export function provisioningState(state) {
  return ({ queued: '等待准备', checking: '检查已有工具', downloading: '下载已锁定的小型依赖', installing: '离线安装依赖', verifying: '验证工具组合', ready: '已可使用', failed: '准备失败', cancelled: '已取消' })[state] ?? state;
}
export function provisioningText(event) {
  return `${(event.capabilities ?? []).join('、')} · ${provisioningState(event.state)}${event.cache_hit ? ' · 缓存命中' : ''}${event.error ? '\n' + event.error.message : ''}`;
}

export class ToolActivity {
  constructor(now = Date.now) { this.now = now; this.reset(); }
  reset() { this.index = 0; this.calls = new Map(); }
  interrupt(status = '已中断') {
    const pending = [...this.calls.values()];
    for (const activity of pending) Object.assign(activity, { status, duration: Math.max(0, this.now() - activity.started),
      result: [{ label: '说明', text: '运行已结束，未收到此调用的最终返回；以运行证据为准' }] });
    this.calls.clear();
    return pending;
  }
  start(event) {
    const activity = { index: ++this.index, name: plain(event.toolName), started: this.now(), status: '执行中', ...describeTool(event.toolName, event.args), result: [] };
    this.calls.set(event.toolCallId ?? event.toolName, activity);
    return activity;
  }
  finish(event) {
    const key = event.toolCallId ?? event.toolName;
    const activity = this.calls.get(key) ?? this.start(event);
    this.calls.delete(key);
    const summary = summarizeTool(event.toolName, event.result, event.isError);
    Object.assign(activity, { status: summary.status, result: summary.details, isError: summary.isError,
      duration: Number.isFinite(event.durationMs) ? Math.max(0, event.durationMs) : Math.max(0, this.now() - activity.started) });
    return activity;
  }
}
export function toolCardText(activity, { resultOnly = false } = {}) {
  const title = `工具 #${activity.index} · ${activity.name} · ${activity.status}${activity.duration !== undefined ? ` · ${(activity.duration / 1000).toFixed(2)}s` : ''}`;
  const fields = resultOnly ? activity.result : [{ label: '用途', text: activity.purpose }, ...activity.details, ...activity.result];
  return `┌─ ${title}\n${fields.flatMap(field => field.text.split('\n').map((line, i) => i === 0 ? `${field.label}：${line}` : `  ${line}`)).map(line => '│ ' + line).join('\n')}\n└─`;
}
