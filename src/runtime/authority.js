import { lookup } from 'node:dns/promises';
import { createConnection, isIP } from 'node:net';
import { randomUUID } from 'node:crypto';
import { domainToASCII } from 'node:url';
import { invariant } from '../util.js';

export const NETWORK_CAPABILITIES = ['http', 'browser', 'tcp', 'ssh'];
export function targetSpec(value) {
  const url = new URL(value.includes('://') ? value : `tcp://${value}`);
  const protocol = url.protocol.slice(0, -1);
  invariant(['http', 'https', 'tcp', 'ssh', 'telnet'].includes(protocol) && !url.username && !url.password, 'Use an http/https/tcp/ssh target without embedded credentials');
  const host = url.hostname.replace(/^\[|\]$/g, '').toLowerCase();
  return { host, port: Number(url.port || ({ http: 80, https: 443, ssh: 22, telnet: 23 })[protocol]),
    capabilities: protocol === 'ssh' ? ['ssh'] : ['http', 'https'].includes(protocol) ? ['http', 'browser'] : ['tcp'] };
}
export class NetworkAuthority {
  constructor({ evidence, state, resolve = host => lookup(host, { all: true }), connect = createConnection, approve, now = Date.now }) {
    Object.assign(this, { evidence, state, resolve, connect, approve, now });
    this.targets = new Map(); this.streams = new Map(); this.closed = false; this.epoch = 0; this.denials = [];
  }
  async prepare(spec) {
    invariant(!this.closed, 'Network authority closed');
    const host = isIP(spec.host) ? spec.host : domainToASCII(String(spec.host).toLowerCase().replace(/\.$/, ''));
    invariant(host && !/[\s/\\\0]/.test(host) && Number.isInteger(spec.port) && spec.port > 0 && spec.port < 65536, 'Invalid target');
    invariant(Array.isArray(spec.capabilities) && spec.capabilities.length && spec.capabilities.every(c => NETWORK_CAPABILITIES.includes(c)), 'Invalid network capabilities');
    const ips = isIP(host) ? [host] : [...new Set((await this.resolve(host)).map(x => x.address))];
    invariant(ips.length > 0 && ips.length <= 32 && ips.every(ip => isIP(ip)), 'Target DNS has no valid bounded address set');
    const expires = spec.expires_at ? Date.parse(spec.expires_at) : this.now() + 8 * 3600000;
    invariant(Number.isFinite(expires) && expires > this.now(), 'Network grant expired');
    return { host, port: spec.port, capabilities: [...new Set(spec.capabilities)], ips, expires_at: new Date(expires).toISOString(),
      ...(spec.ssh_host_key ? { ssh_host_key: spec.ssh_host_key } : {}) };
  }
  async grant(spec, { signal, authorized = false } = {}) {
    const prepared = await this.prepare(spec);
    signal?.throwIfAborted();
    if (!authorized && !(await this.approve?.({ kind: 'network', target: prepared }, signal))) return { state: 'approval_required', target: prepared };
    signal?.throwIfAborted(); invariant(!this.closed, 'Network authority closed');
    const item = { ...prepared, id: randomUUID(), epoch: ++this.epoch, state: 'active' };
    this.targets.set(item.id, item);
    this.scheduleExpiry(item);
    try { await this.record(item); }
    catch (error) {
      item.state = 'record_failed'; item.epoch = ++this.epoch; clearTimeout(item.timer);
      for (const [socket, owner] of this.streams) if (owner === item.id) socket.destroy(new Error('Network grant could not be recorded'));
      throw error;
    }
    return this.describe(item);
  }
  scheduleExpiry(item) {
    const remaining = Date.parse(item.expires_at) - this.now();
    if (remaining <= 0) { this.revoke(item.id, 'expired').catch(() => {}); return; }
    item.timer = setTimeout(() => {
      if (Date.parse(item.expires_at) <= this.now()) this.revoke(item.id, 'expired').catch(() => {});
      else this.scheduleExpiry(item);
    }, Math.min(remaining, 2147483647));
    item.timer.unref();
  }
  describe({ timer, ...item }) { return { ...item }; }
  status() { return [...this.targets.values()].map(item => this.describe(item)); }
  check(id, capability) {
    const target = this.targets.get(id);
    invariant(!this.closed && target?.state === 'active' && Date.parse(target.expires_at) > this.now(), 'Target is inactive, revoked or expired');
    invariant(target.capabilities.includes(capability), `Target does not permit ${capability}`);
    return target;
  }
  find(host, port, capability) {
    host = host.replace(/^\[|\]$/g, '').toLowerCase().replace(/\.$/, '');
    const target = [...this.targets.values()].find(t => t.host === host && t.port === port && t.capabilities.includes(capability) && t.state === 'active' && Date.parse(t.expires_at) > this.now());
    if (!target) { this.denials.push({ host, port, capability, time: new Date(this.now()).toISOString() }); this.denials = this.denials.slice(-16); }
    invariant(target, `scope_blocked: ${host}:${port} (${capability}); request authorization for this service`);
    return this.check(target.id, capability);
  }
  async record(target) {
    const record = this.describe(target);
    if (this.state) await this.state.add('network_targets', { ...record, target_id: record.id, attempt_id: this.state.identity.run_id });
    else await this.evidence.append('network_target', record);
  }
  async revoke(id, reason = 'revoked') {
    const target = this.targets.get(id); invariant(target, 'Unknown target');
    if (target.state !== 'active') return this.describe(target);
    target.state = reason; target.epoch = ++this.epoch; clearTimeout(target.timer);
    for (const [socket, owner] of this.streams) if (owner === id) socket.destroy(new Error(`Network target ${reason}`));
    await this.record(target); return this.describe(target);
  }
  async dial(id, capability, { signal } = {}) {
    signal?.throwIfAborted(); const target = this.check(id, capability), epoch = target.epoch;
    invariant(this.streams.size < 64, 'At most 64 active network streams');
    // Only approved literal addresses are dialed; no second resolver in a client.
    let lastError;
    for (const ip of target.ips) {
      const socket = this.connect({ host: ip, port: target.port, family: isIP(ip) });
      this.streams.set(socket, id);
      const abort = () => socket.destroy(new Error('Connection cancelled'));
      signal?.addEventListener('abort', abort, { once: true });
      socket.once('close', () => { this.streams.delete(socket); signal?.removeEventListener('abort', abort); });
      socket.on('error', () => {});
      try {
        await new Promise((resolve, reject) => {
          const timer = setTimeout(() => socket.destroy(new Error('Connection deadline')), 10000);
          const done = error => { clearTimeout(timer); socket.off('error', failed); socket.off('connect', connected); error ? reject(error) : resolve(); };
          const failed = error => done(error), connected = () => done();
          socket.once('error', failed); socket.once('connect', connected);
          if (signal?.aborted || this.closed || target.epoch !== epoch) abort();
        });
        invariant(this.check(id, capability).epoch === epoch, 'Target authorization changed');
        await this.evidence.append('network_connection', { connection_id: randomUUID(), target_id: id, epoch, actual_peer_ip: ip, port: target.port, capability });
        return socket;
      } catch (error) { socket.destroy(); lastError = error; if (signal?.aborted || target.epoch !== epoch || this.closed) throw error; }
    }
    throw lastError;
  }
  async close() {
    this.closed = true;
    for (const socket of this.streams.keys()) socket.destroy(new Error('Run closed'));
    await Promise.all([...this.targets.values()].filter(t => t.state === 'active').map(t => this.revoke(t.id, 'run_closed')));
  }
}
