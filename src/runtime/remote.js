import { stat } from 'node:fs/promises';
import { NetworkAuthority, targetSpec } from './authority.js';
import { RelayGateway, RELAY_SOURCE } from './relay.js';
import { RemoteSessions } from './remote-sessions.js';
import { RemoteWorker } from './worker.js';
import { HttpSessions, publicUrl } from './http.js';
import { invariant, readRegular } from '../util.js';

export class RemoteRuntime {
  constructor({ evidence, state, image, runId, approve, credentialsPath, onOwnership, onLost }) {
    Object.assign(this, { evidence, state, image, runId, approve, credentialsPath, onOwnership, onLost });
    this.authority = new NetworkAuthority({ evidence, state, approve });
    this.gateway = new RelayGateway(this.authority); this.streams = new RemoteSessions(this.authority, evidence);
    this.worker = new RemoteWorker({ image, runId, gateway: this.gateway, writable: true, onOwnership, onLost });
    this.browserWorker = new RemoteWorker({ image, runId, gateway: this.gateway, onOwnership, onLost });
    this.connections = new Map(); this.channels = new Map(); this.relayedChannels = new Set(); this.browsers = new Set(); this.browserLocations = new Map(); this.relays = new Map();
  }
  async prepare() { await this.gateway.prepare(); return this.gateway.root; }
  bind({ runtime, sessions, assets }) { Object.assign(this, { runtime, sessions, assets }); this.http = new HttpSessions(this.authority, assets); }
  async grant(spec, options) {
    const result = await this.authority.grant(spec, options);
    if (result.state === 'active') {
      try { await this.gateway.start(); }
      catch (error) { await this.authority.revoke(result.id, 'relay_unavailable'); throw error; }
    }
    return result;
  }
  async grantUrl(url, options) { return this.grant(targetSpec(url), options); }
  async revoke(id) { return this.authority.revoke(id); }
  async relay(targetId, signal) {
    const key = targetId || 'http';
    if (targetId) this.authority.check(targetId, 'tcp');
    else invariant(this.authority.status().some(t => t.state === 'active' && t.capabilities.includes('http')), 'Approve an HTTP target first');
    const cached = this.relays.get(key);
    if (cached && this.sessions.get(cached.relay_session_id).state === 'running') return cached;
    this.relays.delete(key);
    await this.gateway.start();
    const item = await this.sessions.start(['python3', '-I', '-u', '-c', RELAY_SOURCE, '0', targetId || '-', targetId ? 'tcp' : 'http'], signal);
    let output = '';
    for (let i = 0; i < 10 && !output.includes('\n'); i++) output += (await this.sessions.read(item.session_id, { collectSeconds: 1, signal })).output;
    const { port } = JSON.parse(output.split('\n')[0]); invariant(port > 0 && port < 65536, 'Relay did not start');
    const result = { target_id: targetId, relay_session_id: item.session_id, host: '127.0.0.1', port,
      ...(targetId ? { script_configuration: { TARGET_HOST: '127.0.0.1', TARGET_PORT: port } } : { proxy: `http://127.0.0.1:${port}`, notice: 'Set HTTP_PROXY and HTTPS_PROXY explicitly; other destinations remain blocked.' }) };
    this.relays.set(key, result); return result;
  }
  async credential(ref) {
    invariant(this.credentialsPath, 'Configure --remote-credentials with a private credential-reference JSON file');
    const info = await stat(this.credentialsPath);
    invariant((info.mode & 0o077) === 0 && (!process.getuid || info.uid === process.getuid()), 'Credential file must be owned by controller with mode 0600');
    const map = JSON.parse((await readRegular(this.credentialsPath, 1024 * 1024)).toString());
    const value = map[ref]; invariant(value && typeof value.username === 'string' && (typeof value.password === 'string' || typeof value.private_key === 'string'), 'Unknown credential reference');
    return value;
  }
  async sshOpen(targetId, credentialRef, signal) {
    const target = this.authority.check(targetId, 'ssh'); await this.gateway.start();
    invariant(this.connections.size < 16, 'SSH connection limit');
    const credential = await this.credential(credentialRef);
    let result = await this.worker.call('ssh_open', { target_id: targetId, host_key: target.ssh_host_key, credential }, signal);
    if (result.needs_host_key) {
      const accepted = await this.approve?.({ kind: target.ssh_host_key ? 'ssh_host_key_changed' : 'ssh_host_key', target: this.authority.describe(target), expected_fingerprint: target.ssh_host_key, fingerprint: result.needs_host_key }, signal);
      invariant(accepted, `SSH host key approval required: ${result.needs_host_key}`);
      target.ssh_host_key = result.needs_host_key; await this.authority.record(target);
      result = await this.worker.call('ssh_open', { target_id: targetId, host_key: target.ssh_host_key, credential }, signal);
      invariant(!result.needs_host_key, 'SSH host key changed during approval');
    }
    this.connections.set(result.connection_id, targetId);
    await this.state.add('remote_sessions', { ...result, target_id: targetId, transport: 'ssh' });
    return result;
  }
  async ssh(method, args, signal) {
    invariant(!['ssh_read', 'ssh_send', 'ssh_relay'].includes(method) || !this.relayedChannels.has(args.channel_id), 'SSH channel IO belongs to its script relay');
    const connectionId = args.connection_id ?? this.channels.get(args.channel_id);
    invariant(connectionId && this.connections.has(connectionId), 'Unknown current SSH connection');
    const targetId = this.connections.get(connectionId); this.authority.check(targetId, 'ssh');
    const result = await this.worker.call(method, args, signal);
    if (result.channel_id) this.channels.set(result.channel_id, connectionId);
    if (method === 'ssh_close') {
      if (args.channel_id) { this.channels.delete(args.channel_id); this.relayedChannels.delete(args.channel_id); }
      else {
        this.connections.delete(connectionId);
        for (const [channelId, owner] of this.channels) if (owner === connectionId) { this.channels.delete(channelId); this.relayedChannels.delete(channelId); }
      }
    }
    return { ...result, target_id: targetId, connection_id: connectionId };
  }
  async sshRelay(channelId, signal) {
    const result = await this.ssh('ssh_relay', { channel_id: channelId }, signal);
    this.relayedChannels.add(channelId);
    const source = RELAY_SOURCE.replace("peer=upstream({'target_id':target,'capability':capability})", "peer=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM); peer.connect('/run/ctf-network/'+target)");
    const item = await this.sessions.start(['python3', '-I', '-u', '-c', source, '0', result.socket_name, 'ssh'], signal);
    let output = '';
    for (let i = 0; i < 10 && !output.includes('\n'); i++) output += (await this.sessions.read(item.session_id, { collectSeconds: 1, signal })).output;
    const { port } = JSON.parse(output.split('\n')[0]);
    return { channel_id: channelId, host: '127.0.0.1', port, relay_session_id: item.session_id, notice: 'This relay takes ownership of channel IO. Use one script connection; do not mix ssh_read/ssh_send.' };
  }
  async browserOpen(signal) {
    invariant(this.authority.status().some(t => t.state === 'active' && t.capabilities.includes('browser')), 'Approve a browser target first');
    invariant(this.browsers.size < 2, 'At most two isolated browser sessions per run');
    await this.gateway.start(); const result = await this.browserWorker.call('browser_open', {}, signal); this.browsers.add(result.browser_id); return result;
  }
  async browser(args, signal) {
    invariant(this.browsers.has(args.browser_id), 'Unknown current browser');
    if (args.action === 'navigate') { const u = new URL(args.url); invariant(['http:', 'https:'].includes(u.protocol) && !u.username && !u.password, 'Browser navigation requires an HTTP URL'); this.authority.find(u.hostname, Number(u.port || (u.protocol === 'https:' ? 443 : 80)), 'browser'); }
    const result = await this.browserWorker.call('browser_action', args, signal);
    if (args.action === 'close') { this.browsers.delete(args.browser_id); this.browserLocations.delete(args.browser_id); }
    if (result.url && /^https?:/.test(result.url)) {
      const url = new URL(result.url);
      const target = this.authority.find(url.hostname, Number(url.port || (url.protocol === 'https:' ? 443 : 80)), 'browser');
      this.browserLocations.set(args.browser_id, { target_id: target.id, epoch: target.epoch, url: publicUrl(url.href), approved_ips: target.ips });
    }
    if (result.events) result.events = result.events.map(e => ({ ...e, ...(e.url && /^https?:/.test(e.url) ? { url: publicUrl(e.url) } : {}) }));
    return result;
  }
  async close() {
    const results = await Promise.allSettled([this.authority.close(), this.streams.closeAll(), this.worker.close(), this.browserWorker.close()]);
    try { await this.gateway.close(); } catch (error) { results.push({ status: 'rejected', reason: error }); }
    const errors = results.filter(x => x.status === 'rejected').map(x => x.reason);
    if (errors.length) throw new AggregateError(errors, 'Remote cleanup unconfirmed');
  }
}
