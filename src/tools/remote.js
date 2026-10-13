import { randomUUID } from 'node:crypto';
import { s } from './schema.js';
import { base64, invariant } from '../util.js';
import { publicUrl } from '../runtime/http.js';
const id = () => s.text(100), headers = () => s.array(s.object({ name: s.text(200), value: s.text(8192) }), 64);
export function installRemote(registry, remote, { supportsImages = false } = {}) {
  const define = (name, description, parameters, handler) => registry.define(name, description, parameters, handler, { observe: true });
  define('network_status', 'Read current authorized target services. Historical grants and sessions do not authorize this attempt.', s.object(), () => ({ targets: remote.authority.status(), blocked_requests: remote.authority.denials }));
  define('network_request', 'Ask the operator to authorize a target service or refresh DNS. No approval is inferred from model text. No control over remote internal second hops.', s.object({ host: s.text(253), port: s.int(1, 65535), capabilities: s.array(s.enum(['http', 'browser', 'tcp', 'ssh']), 4), expires_at: s.text(100), ssh_host_key: s.text(200) }, ['host', 'port', 'capabilities']), (args, signal) => remote.grant(args, { signal }));
  define('network_revoke', 'Immediately revoke a current target and destroy its live connections.', s.object({ target_id: id() }), ({ target_id }) => remote.revoke(target_id));
  define('network_relay', 'Create a sandbox loopback relay for pwntools remote(TARGET_HOST,TARGET_PORT); omit target_id for an HTTP/HTTPS proxy. No unrestricted Docker networking.', s.object({ target_id: id() }, []), ({ target_id }, signal) => remote.relay(target_id, signal));
  define('remote_open', 'Open a persistent binary TCP stream. Keep one connection across menu, leak and payload. No automatic reconnect or Telnet negotiation.', s.object({ target_id: id() }), async ({ target_id }, signal) => {
    const result = await remote.streams.open(target_id, signal); await registry.state.add('remote_sessions', { ...result, transport: 'tcp' }); return result;
  });
  define('remote_send', 'Send exact bytes without closing the connection. Use base64 for binary payloads.', s.object({ session_id: id(), data_base64: s.text(87384) }), ({ session_id, data_base64 }, signal) => remote.streams.send(session_id, base64(data_base64), signal));
  define('remote_read', 'Read bounded exact bytes. Timeout preserves received bytes; until includes delimiter and preserves subsequent bytes. UTF8 preview is not the authoritative payload.', s.object({ session_id: id(), max_bytes: s.int(1, 65536), wait_ms: s.int(0, 30000), until_base64: s.text(4096) }, ['session_id']), async ({ session_id, ...args }, signal, update) => {
    const result = await remote.streams.read(session_id, args, signal, update);
    if (result.bytes) { const asset = await registry.assets.addRemote(base64(result.data_base64), { target_id: result.target_id, session_id, generation: result.generation, display_name: '远端 TCP 输出', offset: result.offset }); result.asset_id = asset.id; }
    return result;
  });
  define('remote_shutdown', 'Explicitly half-close the TCP write direction while keeping reads available.', s.object({ session_id: id() }), ({ session_id }) => remote.streams.shutdown(session_id));
  define('remote_close', 'Close a current TCP connection.', s.object({ session_id: id() }), ({ session_id }) => remote.streams.close(session_id));
  define('http_session', 'Create or close a private challenge cookie jar. Authentication state is ephemeral and not exported on resume.', s.object({ action: s.enum(['open', 'close']), session_id: id() }, ['action']), ({ action, session_id }) => action === 'open' ? remote.http.open() : remote.http.close(session_id));
  define('web_request', 'HTTP/HTTPS request with cookie session and captured body asset. Response headers redact credentials. Body bytes retain content encoding. Redirects require authorization at each service. Use raw TCP/relay for malformed HTTP.', s.object({ url: s.text(4096), session_id: id(), method: s.text(20), headers: headers(), body_text: s.text(60000), body_asset_id: id(), follow_redirects: s.bool(), insecure_tls: s.bool(), form: s.array(s.object({ name: s.text(200), value: s.text(8192), asset_id: id(), filename: s.text(200) }, ['name']), 32) }, ['url']), async (args, signal, update) => {
    invariant([args.body_text !== undefined, !!args.body_asset_id, !!args.form].filter(Boolean).length <= 1, 'Choose one request body format');
    let body = args.body_asset_id ? (await registry.assets.read(args.body_asset_id)).bytes : Buffer.from(args.body_text ?? '');
    const requestHeaders = (args.headers ?? []).map(h => [h.name, h.value]);
    if (args.form) {
      const boundary = 'ctf-' + randomUUID(); const parts = [];
      for (const field of args.form) {
        invariant(!/[\r\n"\\]/.test(field.name + (field.filename ?? '')), 'Invalid multipart name');
        parts.push(Buffer.from(`--${boundary}\r\nContent-Disposition: form-data; name="${field.name}"${field.asset_id ? `; filename="${field.filename ?? 'attachment.bin'}"` : ''}\r\n${field.asset_id ? 'Content-Type: application/octet-stream\r\n' : ''}\r\n`));
        parts.push(field.asset_id ? (await registry.assets.read(field.asset_id)).bytes : Buffer.from(field.value ?? ''), Buffer.from('\r\n'));
      }
      parts.push(Buffer.from(`--${boundary}--\r\n`)); body = Buffer.concat(parts); requestHeaders.push(['Content-Type', `multipart/form-data; boundary=${boundary}`]);
    }
    return remote.http.request({ ...args, headers: requestHeaders, body }, signal, update);
  });
  define('browser_open', 'Start an isolated Chromium/CDP session using current image, private profile and authorized proxy. Never uses personal browser or provider login cookies.', s.object(), (_, signal, update) => { update?.({ content: [{ type: 'text', text: '启动隔离浏览器' }] }); return remote.browserOpen(signal); });
  define('browser_action', 'Navigate/inspect/click/fill/evaluate page JavaScript, capture screenshot, read network/console events or close. JS runs only in page. Download via web_request so bytes enter the asset chain.', s.object({ browser_id: id(), action: s.enum(['navigate', 'inspect', 'click', 'fill', 'evaluate', 'screenshot', 'events', 'close']), url: s.text(4096), selector: s.text(1024), value: s.text(8192), expression: s.text(16000), wait_ms: s.int(0, 10000) }, ['browser_id', 'action']), async (args, signal, update) => {
    const needed = { navigate: 'url', click: 'selector', fill: 'selector', evaluate: 'expression' }[args.action]; invariant(!needed || args[needed], `Action requires ${needed}`);
    update?.({ content: [{ type: 'text', text: `浏览器：${args.action}` }] });
    const result = await remote.browser(args, signal);
    if (result.data_base64) {
      const bytes = base64(result.data_base64, 24 * 1024 * 1024); invariant(bytes.length <= 16 * 1024 * 1024, 'Screenshot asset exceeds 16 MiB'); const asset = await registry.assets.addRemote(bytes, { browser_id: args.browser_id, ...remote.browserLocations.get(args.browser_id), display_name: '网页截图', mime_type: 'image/png' });
      return { asset_id: asset.id, bytes: bytes.length, artifact: asset.artifact, ...(supportsImages && bytes.length <= 1024 * 1024 ? { image: { type: 'image', data: result.data_base64, mimeType: 'image/png' } } : {}) };
    }
    if (result.html !== undefined) {
      const asset = await registry.assets.addRemote(Buffer.from(result.html), { browser_id: args.browser_id, ...remote.browserLocations.get(args.browser_id), display_name: '网页 DOM', url: /^https?:/.test(result.url) ? publicUrl(result.url) : result.url, completeness: 'DOM snapshot bounded to 1M characters' });
      const { html, ...rest } = result; return { ...rest, url: /^https?:/.test(rest.url) ? publicUrl(rest.url) : rest.url, dom_asset_id: asset.id, html_truncated: html.length >= 1000000 };
    }
    return result;
  });
  define('web_cookie_sync', 'Explicitly copy challenge cookies between HTTP and browser sessions without exposing cookie values in ordinary results. localStorage/page memory are not copied.', s.object({ session_id: id(), browser_id: id(), direction: s.enum(['to_browser', 'to_http']) }), async ({ session_id, browser_id, direction }, signal) => {
    const session = remote.http.items.get(session_id); invariant(session, 'Unknown HTTP session');
    if (direction === 'to_browser') { const cookies = session.cookies.map(c => ({ name: c.name, value: c.value, domain: c.host, path: c.path, secure: c.secure, ...(Number.isFinite(c.expires) ? { expires: c.expires / 1000 } : {}) })); await remote.browser({ browser_id, action: 'cookies', cookies }, signal); return { copied: cookies.length }; }
    const { cookies } = await remote.browser({ browser_id, action: 'cookies' }, signal);
    session.cookies = cookies.map(c => ({ name: c.name, value: c.value, host: c.domain.replace(/^\./, ''), path: c.path, secure: c.secure, expires: c.expires > 0 ? c.expires * 1000 : Infinity })); return { copied: cookies.length };
  });
  define('ssh_open', 'Authenticate a fixed isolated SSH worker using an operator-configured credential_ref. Passwords/keys never belong in tool arguments. Host key requires explicit pin or operator approval.', s.object({ target_id: id(), credential_ref: id() }), ({ target_id, credential_ref }, signal) => remote.sshOpen(target_id, credential_ref, signal));
  define('ssh_exec', 'Run a command on the authorized SSH server; remote internal second hops are not constrained. Returns a channel to read/send. PTY defaults off for binary correctness.', s.object({ connection_id: id(), command: s.text(8192), pty: s.bool() }, ['connection_id', 'command']), (args, signal) => remote.ssh('ssh_exec', args, signal));
  define('ssh_shell', 'Open an SSH shell channel. PTY is explicit; does not imply local host command execution.', s.object({ connection_id: id(), pty: s.bool() }, ['connection_id']), (args, signal) => remote.ssh('ssh_shell', args, signal));
  define('ssh_relay', 'Give a sandbox script exclusive byte-stream ownership of an SSH channel through a local port. Use pwntools remote(host,port); no credentials enter the solver.', s.object({ channel_id: id() }), ({ channel_id }, signal) => remote.sshRelay(channel_id, signal));
  define('ssh_send', 'Send exact channel bytes; do not send authentication credentials here.', s.object({ channel_id: id(), data_base64: s.text(87384) }), (args, signal) => { base64(args.data_base64); return remote.ssh('ssh_send', args, signal); });
  define('ssh_read', 'Read binary stdout/stderr from an SSH channel, keeping EOF distinct from timeout.', s.object({ channel_id: id(), max_bytes: s.int(1, 65536), wait_ms: s.int(0, 30000) }, ['channel_id']), async (args, signal, update) => {
    update?.({ content: [{ type: 'text', text: '读取 SSH 输出' }] }); const result = await remote.ssh('ssh_read', args, signal);
    const bytes = base64(result.data_base64);
    const asset = bytes.length ? await registry.assets.addRemote(bytes, { target_id: result.target_id, connection_id: result.connection_id, channel_id: args.channel_id, display_name: 'SSH 远端输出' }) : null;
    return { ...(asset ? { asset_id: asset.id } : {}), ...result, preview: bytes.toString('utf8').slice(0, 2048), artifact: await registry.evidence.artifact(bytes), stderr_artifact: await registry.evidence.artifact(base64(result.stderr_base64)) };
  });
  define('ssh_transfer', 'Explicit SFTP upload from an asset or bounded download into a remote root asset. Remote effects are not automatically replayed on resume.', s.object({ connection_id: id(), direction: s.enum(['upload', 'download']), path: s.text(2048), asset_id: id() }, ['connection_id', 'direction', 'path']), async (args, signal) => {
    invariant(args.direction !== 'upload' || args.asset_id, 'Upload requires asset_id');
    let upload;
    if (args.direction === 'upload') {
      upload = (await registry.assets.read(args.asset_id)).bytes;
      invariant(upload.length <= 16 * 1024 * 1024, 'SFTP upload is limited to 16 MiB');
    }
    const request = { ...args, ...(upload ? { data_base64: upload.toString('base64') } : {}) };
    const result = await remote.ssh('ssh_transfer', request, signal);
    if (Object.hasOwn(result, 'data_base64')) { const asset = await registry.assets.addRemote(base64(result.data_base64, 23 * 1024 * 1024), { target_id: result.target_id, connection_id: result.connection_id, display_name: `远端文件 ${args.path}`, protocol: 'sftp' }); return { asset_id: asset.id, bytes: asset.bytes }; }
    return result;
  });
  define('ssh_close', 'Close an SSH connection or channel. Closing cannot guarantee that remote background processes exit.', s.object({ connection_id: id(), channel_id: id() }, []), (args, signal) => { invariant(Boolean(args.connection_id) !== Boolean(args.channel_id), 'Specify exactly one SSH connection_id or channel_id'); return remote.ssh('ssh_close', args, signal); });
}
