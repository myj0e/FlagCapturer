import http from 'node:http';
import https from 'node:https';
import { isIP } from 'node:net';
import { randomUUID } from 'node:crypto';
import { invariant } from '../util.js';
import { publicUrl } from '../urls.js';
export { publicUrl } from '../urls.js';
const MAX_BODY = 16 * 1024 * 1024;
const secretHeader = /^(authorization|proxy-authorization|cookie|set-cookie)$/i;
export const safeHeaders = (headers, baseUrl) => headers.map(([name, value]) => {
  if (secretHeader.test(name)) return [name, '[redacted]'];
  if (/^location$/i.test(name) && baseUrl) {
    try { return [name, publicUrl(new URL(value, baseUrl).href)]; } catch { return [name, '[redacted]']; }
  }
  return [name, value];
});

export class HttpSessions {
  constructor(authority, assets) { this.authority = authority; this.assets = assets; this.items = new Map(); }
  open() { invariant(this.items.size < 32, 'HTTP session limit'); const id = randomUUID(); this.items.set(id, { cookies: [] }); return { session_id: id }; }
  close(id) { this.items.delete(id); return { state: 'closed' }; }
  async request({ session_id, url, method = 'GET', headers = [], body = Buffer.alloc(0), follow_redirects = false, insecure_tls = false }, signal, update) {
    const session = session_id ? this.items.get(session_id) : null; invariant(!session_id || session, 'Unknown HTTP session');
    invariant(body.length <= MAX_BODY && /^[A-Z]{1,20}$/.test(method), 'Invalid HTTP method/body');
    invariant(headers.every(([key, value]) => /^[!#$%&'*+.^_`|~0-9A-Za-z-]+$/.test(key) && !/[\r\n\0]/.test(value)), 'Invalid HTTP headers');
    let current = new URL(url); invariant(!current.username && !current.password, 'Use session credentials, not URL passwords');
    const chain = []; let requestHeaders = headers;
    for (let hop = 0; hop < 6; hop++) {
      signal?.throwIfAborted(); invariant(['http:', 'https:'].includes(current.protocol) && !current.username && !current.password, 'Only credential-free HTTP/HTTPS URLs');
      const port = Number(current.port || (current.protocol === 'https:' ? 443 : 80));
      const target = this.authority.find(current.hostname, port, 'http');
      update?.({ content: [{ type: 'text', text: `${method} ${publicUrl(current.href)}` }], details: { target_id: target.id, hop } });
      const cookies = session?.cookies.filter(c => (c.host === current.hostname || !c.host_only && current.hostname.endsWith('.' + c.host)) && (current.pathname === c.path || current.pathname.startsWith(c.path.endsWith('/') ? c.path : c.path + '/')) && (!c.secure || current.protocol === 'https:') && c.expires > Date.now()) ?? [];
      const suppliedCookie = requestHeaders.some(([k]) => /^cookie$/i.test(k));
      const effective = [...requestHeaders, ...(!suppliedCookie && cookies.length ? [['Cookie', cookies.map(c => `${c.name}=${c.value}`).join('; ')]] : [])];
      // Node treats an array of raw header pairs as a complete header list and omits Host.
      if (!effective.some(([key]) => /^host$/i.test(key))) effective.push(['Host', current.host]);
      if (body.length && !effective.some(([k]) => /^content-length$/i.test(k))) effective.push(['Content-Length', String(body.length)]);
      const socket = await this.authority.dial(target.id, 'http', { signal });
      let response;
      try {
        response = await new Promise((resolve, reject) => {
          const agent = current.protocol === 'https:' ? new https.Agent({ keepAlive: false }) : new http.Agent({ keepAlive: false });
          // Own the only dial. HTTPS wraps this pinned socket; Host and SNI keep the URL identity.
          if (current.protocol === 'https:') agent.createConnection = options => importTls(socket, current.hostname, !insecure_tls);
          else agent.createConnection = () => socket;
          const client = current.protocol === 'https:' ? https : http;
          const req = client.request(current, { method, headers: effective.flat(), agent, signal, rejectUnauthorized: !insecure_tls }, res => {
            const chunks = []; let size = 0, truncated = false;
            res.on('data', chunk => { const available = MAX_BODY - size; chunks.push(chunk.subarray(0, available)); size += Math.min(available, chunk.length); if (chunk.length > available) { truncated = true; res.destroy(); } });
            let completed = false;
            const done = () => { if (completed) return; completed = true; agent.destroy(); resolve({ status: res.statusCode, headers: Array.from({ length: res.rawHeaders.length / 2 }, (_, i) => res.rawHeaders.slice(i * 2, i * 2 + 2)), bytes: Buffer.concat(chunks), truncated: truncated || !res.complete }); };
            res.once('end', done); res.once('close', done); res.once('error', error => { if (!truncated) reject(error); });
          });
          const deadline = setTimeout(() => req.destroy(new Error('HTTP deadline')), 30000);
          req.once('close', () => clearTimeout(deadline));
          req.setTimeout(30000, () => req.destroy(new Error('HTTP idle deadline')));
          req.once('error', error => { agent.destroy(); reject(error); }); req.end(body);
        });
      } finally { socket.destroy(); }
      if (session) for (const [name, value] of response.headers) if (/^set-cookie$/i.test(name)) {
        const parts = value.split(';').map(x => x.trim()), equal = parts[0].indexOf('='); if (equal < 1) continue;
        const attributes = new Map(parts.slice(1).map(x => { const at = x.indexOf('='); return [at < 0 ? x.toLowerCase() : x.slice(0, at).toLowerCase(), at < 0 ? '' : x.slice(at + 1)]; }));
        const domain = attributes.get('domain')?.toLowerCase().replace(/^\./, '');
        if (domain && !(domain === current.hostname || current.hostname.endsWith('.' + domain) && domain.includes('.'))) continue;
        const cookie = { name: parts[0].slice(0, equal), value: parts[0].slice(equal + 1), host: domain || current.hostname, host_only: !domain, path: attributes.get('path') || current.pathname.slice(0, current.pathname.lastIndexOf('/') + 1) || '/', secure: attributes.has('secure'), expires: attributes.has('max-age') ? Date.now() + Number(attributes.get('max-age')) * 1000 : attributes.has('expires') ? Date.parse(attributes.get('expires')) : Infinity };
        session.cookies = session.cookies.filter(c => !(c.host === cookie.host && c.path === cookie.path && c.name === cookie.name));
        session.cookies.push(cookie); invariant(session.cookies.length <= 256, 'Cookie jar limit');
      }
      const headersSafe = safeHeaders(response.headers, current.href);
      const asset = await this.assets.addRemote(response.bytes, { target_id: target.id, epoch: target.epoch, actual_peer_ip: socket.remoteAddress ?? target.ips[0], url: publicUrl(current.href), method, status: response.status, headers: headersSafe, truncated: response.truncated, redirect_chain: [...chain] });
      const location = response.headers.find(([name]) => /^location$/i.test(name))?.[1];
      const result = { status: response.status, headers: headersSafe, url: publicUrl(current.href), body_asset_id: asset.id, bytes: response.bytes.length, preview: response.bytes.toString('utf8').slice(0, 4096), truncated: response.truncated, content_encoding: response.headers.find(([name]) => /^content-encoding$/i.test(name))?.[1] ?? null, redirect_chain: chain, tls_verified: current.protocol === 'https:' && !insecure_tls };
      if (!follow_redirects || !location || ![301, 302, 303, 307, 308].includes(response.status)) return { ...result, ...(location ? { redirect_location: headersSafe.find(([name]) => /^location$/i.test(name))?.[1] } : {}) };
      let next;
      try { next = new URL(location, current); }
      catch { return { ...result, scope_blocked: '[invalid redirect URL]', reason: 'Redirect URL could not be parsed' }; }
      if (!['http:', 'https:'].includes(next.protocol) || next.username || next.password) return { ...result, scope_blocked: publicUrl(next.href), reason: 'Redirect must use credential-free HTTP/HTTPS' };
      try { this.authority.find(next.hostname, Number(next.port || (next.protocol === 'https:' ? 443 : 80)), 'http'); }
      catch (error) { return { ...result, scope_blocked: publicUrl(next.href), reason: error.message }; }
      chain.push({ status: response.status, url: publicUrl(current.href), asset_id: asset.id });
      if (next.origin !== current.origin) requestHeaders = requestHeaders.filter(([k]) => !/^(authorization|cookie|host)$/i.test(k));
      if (response.status === 303 || [301, 302].includes(response.status) && method === 'POST') { method = 'GET'; body = Buffer.alloc(0); requestHeaders = requestHeaders.filter(([k]) => !/^content-(length|type)$/i.test(k)); }
      current = next;
    }
    throw new Error('HTTP redirect limit reached; inspect captured chain');
  }
}
import { connect as tlsConnect } from 'node:tls';
function importTls(socket, host, rejectUnauthorized) { return tlsConnect({ socket, servername: isIP(host.replace(/^\[|\]$/g, '')) ? undefined : host, rejectUnauthorized }); }
