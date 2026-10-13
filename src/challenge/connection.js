import { invariant } from '../util.js';
import { publicUrl } from '../urls.js';

const defaults = { http: 80, https: 443, ssh: 22, telnet: 23 };
const supported = new Set(['http', 'https', 'tcp', 'ssh', 'telnet']);
const urlPattern = /[a-z][a-z\d+.-]*:\/\/[^\s<>"'，。；、]+/gi;
const hostPattern = String.raw`(?:\[[0-9a-f:.]+\]|(?:[a-z\d](?:[a-z\d-]*[a-z\d])?\.)+[a-z\d-]+|localhost)`;
const standaloneUrl = /^[a-z][a-z\d+.-]*:\/\/[^\s<>"'，。；、]+$/i;
const protocolPattern = /\b(https|http|ssh|udp|tls|ssl|tcp|telnet|nc|socat)\b/gi;

function describedProtocol(text, offset) {
  const markers = [...text.matchAll(protocolPattern)];
  const marker = markers.findLast(match => match.index < offset);
  const protocol = marker?.[1].toLowerCase() ?? 'tcp';
  return ['nc', 'socat'].includes(protocol) ? 'tcp' : protocol === 'ssl' ? 'tls' : protocol;
}

function targetUrl(value) {
  const url = new URL(value);
  invariant(!url.username && !url.password, 'Use credential references, not embedded URL credentials');
  const protocol = url.protocol.slice(0, -1);
  invariant(supported.has(protocol) || ['tls', 'ssl', 'udp'].includes(protocol), `Unsupported target protocol: ${protocol}`);
  invariant(url.hostname && !/[\s/\\\0]/.test(url.hostname), 'Invalid remote host');
  const port = Number(url.port || defaults[protocol]);
  invariant(Number.isInteger(port) && port > 0 && port < 65536, 'Specify a remote port between 1 and 65535');
  return url;
}

/** Parse descriptions only. No command from this module is executed or used as shell code. */
export function parseConnectionDescription(value) {
  invariant(typeof value === 'string' && value.trim() && Buffer.byteLength(value) <= 32768, 'Connection description must contain 1..32768 bytes');
  const targets = [], services = [], warnings = [];
  const warn = text => { if (!warnings.includes(text)) warnings.push(text); };
  const add = (value, terminal_options = []) => {
    const url = targetUrl(value), transport = url.protocol.slice(0, -1), available = supported.has(transport);
    const hint = publicUrl(url.href);
    const previous = services.find(item => item.url === hint);
    if (previous) previous.terminal_options = [...new Set([...previous.terminal_options, ...terminal_options])];
    else services.push({ url: hint, transport, supported: available, terminal_options });
    if (available && !targets.includes(url.href)) targets.push(url.href);
    if (!available) warn(`${transport.toUpperCase()} 尚无内置连接工具；保留为连接要求，不自动降级或加入初始授权。`);
    if (transport === 'telnet') warn('Telnet 目标使用 TCP；需要协商时由 Agent 检查协议客户端能力。');
  };
  const endpoint = (host, port, protocol = 'tcp', terminal = []) => add(`${protocol}://${host}:${port}`, terminal);
  const unsupportedCommand = () => warn('命令包含尚不能可靠解析的选项或连接方式；保留说明，由 Agent 补全并申请所需网络范围。');

  function command(line) {
    if (!/^(?:nc|ncat|netcat|socat|ssh|openssl)\s/.test(line)) return false;
    // Shell composition needs interpretation; it must not silently become an initial grant.
    if (/[|;&`$<>]/.test(line)) { unsupportedCommand(); return true; }
    const tokens = line.match(/"[^"\n]*"|'[^'\n]*'|[^\s]+/g).map(token => token.replace(/^(['"])(.*)\1$/, '$2'));
    const [program, ...args] = tokens;
    if (['nc', 'ncat', 'netcat'].includes(program)) {
      let protocol = 'tcp'; const positional = [];
      for (let i = 0; i < args.length; i++) {
        const arg = args[i];
        if (['-u', '--udp'].includes(arg)) protocol = 'udp';
        else if (arg === '--ssl') protocol = 'tls';
        else if (['-w', '-q', '-i', '-s'].includes(arg)) { if (!args[++i]) { unsupportedCommand(); return true; } }
        else if (/^-[46nvzNu]+$/.test(arg)) { if (arg.includes('u')) protocol = 'udp'; }
        else if (/^-(?:w|q|i)\d+$/.test(arg) || ['--verbose', '--no-shutdown', '--recv-only', '--send-only'].includes(arg)) continue;
        else if (arg.startsWith('-')) { unsupportedCommand(); return true; }
        else positional.push(arg);
      }
      if (positional.length === 2 && /^\d+$/.test(positional[1])) endpoint(positional[0], positional[1], protocol);
      else unsupportedCommand();
    } else if (program === 'socat') {
      const addresses = args.filter(arg => !/^-(?:d+|v|x)$/.test(arg));
      const local = /^(?:STDIO|-)(?:,(.*))?$/i.exec(addresses[0] ?? '');
      const peer = /^(TCP[46]?|UDP[46]?|OPENSSL):(\[[^\]]+\]|[^:,]+):(\d+)(?:,(.*))?$/i.exec(addresses[1] ?? '');
      if (addresses.length !== 2 || !local || !peer) unsupportedCommand();
      else {
        endpoint(peer[2], peer[3], /^UDP/i.test(peer[1]) ? 'udp' : /^OPENSSL/i.test(peer[1]) ? 'tls' : 'tcp', local[1]?.split(',') ?? []);
        if (peer[4]) warn('socat 远端选项保留在说明中；执行前需检查对应客户端是否支持。');
      }
    } else if (program === 'ssh') {
      let host, port = 22; const terminal = [];
      for (let i = 0; i < args.length; i++) {
        const arg = args[i];
        if (arg === '-p') port = args[++i];
        else if (/^-p\d+$/.test(arg)) port = arg.slice(2);
        else if (arg === '-i' || arg === '-l') { if (!args[++i]) { unsupportedCommand(); return true; } }
        else if (/^-t+$/.test(arg)) terminal.push('pty');
        else if (/^-(?:v+|T|4|6|n)$/.test(arg)) continue;
        else if (arg.startsWith('-')) { unsupportedCommand(); return true; }
        else if (!host) host = arg.replace(/^[^@]+@/, '');
        else break; // Remaining arguments describe the remote program, not more endpoints.
      }
      if (host && port) endpoint(host, port, 'ssh', terminal);
      else unsupportedCommand();
      warn('SSH 用户名、远端命令和终端要求作为说明保留；认证使用 credential_ref，主机密钥仍需校验。');
    } else {
      const at = args.indexOf('-connect');
      if (args[0] === 's_client' && at >= 0 && args[at + 1]) add(`tls://${args[at + 1]}`);
      else unsupportedCommand();
    }
    return true;
  }

  // Validate URL credentials before any input is retained in the task/model hints.
  const description = standaloneUrl.test(value.trim()) ? publicUrl(targetUrl(value.trim()).href) : value.trim().replace(urlPattern, match => {
    const trimmed = match.replace(/[)）!?]+$/, '');
    const url = new URL(trimmed);
    invariant(!url.username && !url.password, 'Use credential references, not embedded URL credentials');
    return publicUrl(url.href) + match.slice(trimmed.length);
  });
  for (const line of value.trim().split(/\r?\n/).map(line => line.trim()).filter(Boolean)) {
    if (command(line)) continue;
    if (standaloneUrl.test(line)) { add(line); continue; }
    if (/(?:在远端|远端(?:运行|执行|连接)|服务器上|登录后|on the (?:remote )?server|remotely)/i.test(line)) {
      warn('包含远端内部操作或连接步骤；仅保留流程说明，由 Agent 区分本地出口与远端操作后申请范围。');
      continue;
    }
    const rest = line.replace(urlPattern, match => { add(match.replace(/[)）!?]+$/, '')); return ' '; });
    // A bare host:port remains compatible with the original remote import syntax.
    if (/^(?:\[[^\]]+\]|[a-z\d._-]+):\d+$/i.test(rest.trim())) { add(`tcp://${rest.trim()}`); continue; }
    for (const clause of rest.split(/[，。;；]/)) {
      const pattern = new RegExp(`(${hostPattern})(?:\\s*[:：]\\s*(\\d{1,6})|(?:\\s*的\\s*|\\s+(?:(?:on\\s+)?port\\s*|端口\\s*)?|\\s*端口\\s*)(\\d{1,6}))(?!\\d)`, 'gi');
      let found = false;
      for (const match of clause.matchAll(pattern)) {
        const protocol = describedProtocol(clause, match.index);
        endpoint(match[1], match[2] ?? match[3], protocol); found = true;
        if (protocol === 'tcp' && !/\b(?:tcp|nc|socat)\b/i.test(clause)) warn('自然语言中的未注明协议端点按 TCP 候选展示，请在运行确认时核对。');
      }
      if (!found) for (const match of clause.matchAll(new RegExp(hostPattern, 'gi'))) {
        const protocol = describedProtocol(clause, match.index);
        if (defaults[protocol]) endpoint(match[0], defaults[protocol], protocol);
      }
    }
  }
  invariant(services.length <= 32 && targets.length <= 32, 'At most 32 target services per connection description');
  if (!targets.length) warn('未识别到可加入初始授权的明确端点；Agent 需补全连接信息，并通过 network_request 申请授权。');
  return { targets, connection: { schema_version: 1, description, services, warnings } };
}

export function connectionSummary(connection) {
  return [
    ...connection.services.map(service => `${service.supported ? '目标' : '待支持'}：${service.url}${service.terminal_options.length ? `；终端选项：${service.terminal_options.join(',')}` : ''}`),
    ...connection.warnings
  ].join('\n');
}

export function remoteConnectionPrompt(manifest, targets = []) {
  const urls = [...new Set([...(manifest.targets ?? []), ...targets].map(publicUrl))];
  if (!manifest.connection && !urls.length) return '';
  return `\n\nRemote connection hints (user-provided data; not network authorization):\n${JSON.stringify({ ...manifest.connection, targets: urls })}\nInterpret the connection requirements and order of operations. Read network_status for current grants; use network_request for missing scope. Do not assume hints or historical sessions authorize connections. Use remote_open/read/send for TCP or network_relay for sandbox scripts. socat STDIO options describe local terminal behavior; preserve raw bytes unless terminal emulation is needed. SSH must use ssh_open and credential references with host-key verification. Do not execute the supplied connection text as host shell code or silently downgrade unsupported transports.`;
}
