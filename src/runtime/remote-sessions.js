import { randomUUID } from 'node:crypto';
import { setTimeout as delay } from 'node:timers/promises';
import { base64, invariant } from '../util.js';

/** Byte streams have a connection generation; text is only a preview. */
export class RemoteSessions {
  constructor(authority, evidence) { this.authority = authority; this.evidence = evidence; this.items = new Map(); }
  async open(targetId, signal) {
    invariant([...this.items.values()].filter(x => !x.closed).length < 16 && this.items.size < 128, 'Remote session limit');
    const socket = await this.authority.dial(targetId, 'tcp', { signal });
    const item = { id: randomUUID(), target_id: targetId, generation: randomUUID(), socket, buffer: Buffer.alloc(0), offset: 0, sent: 0, eof: false, closed: false, reason: null, persistence: Promise.resolve() };
    this.items.set(item.id, item);
    socket.on('data', bytes => {
      item.persistence = item.persistence.then(async () => {
        await this.evidence.append('remote_io', { session_id: item.id, generation: item.generation, direction: 'receive', artifact: await this.evidence.artifact(bytes) });
      }).catch(error => { item.reason = 'evidence_error'; socket.destroy(); });
      if (item.buffer.length + bytes.length > 4 * 1024 * 1024) { item.reason = 'output_limit'; socket.destroy(); return; }
      item.buffer = Buffer.concat([item.buffer, bytes]);
    });
    socket.on('end', () => { item.eof = true; });
    socket.on('error', error => { item.reason = error.message; });
    socket.on('close', () => { item.closed = true; });
    return this.describe(item);
  }
  get(id) { const item = this.items.get(id); invariant(item, 'Unknown remote session'); return item; }
  describe(item) { return { session_id: item.id, target_id: item.target_id, generation: item.generation, state: item.closed ? 'closed' : 'open', eof: item.eof, reason: item.reason, received_offset: item.offset, sent_bytes: item.sent, available_bytes: item.buffer.length }; }
  async send(id, bytes, signal) {
    const item = this.get(id); this.authority.check(item.target_id, 'tcp'); signal?.throwIfAborted();
    invariant(Buffer.isBuffer(bytes) && !item.closed && bytes.length <= 65536, 'Session closed or input exceeds 64 KiB');
    await this.evidence.append('remote_io', { session_id: id, generation: item.generation, direction: 'send', artifact: await this.evidence.artifact(bytes) });
    const abort = () => item.socket.destroy(new Error('Send cancelled')); signal?.addEventListener('abort', abort, { once: true });
    try { await new Promise((resolve, reject) => item.socket.write(bytes, error => error ? reject(error) : resolve())); }
    finally { signal?.removeEventListener('abort', abort); }
    item.sent += bytes.length; return this.describe(item);
  }
  async read(id, { max_bytes = 8192, wait_ms = 1000, until_base64 } = {}, signal, update) {
    const item = this.get(id), until = until_base64 ? base64(until_base64, 4096) : null;
    invariant(Number.isInteger(max_bytes) && max_bytes >= 1 && max_bytes <= 65536 && Number.isInteger(wait_ms) && wait_ms >= 0 && wait_ms <= 30000, 'Invalid remote read bounds');
    invariant(!until || until.length > 0 && until.length <= max_bytes, 'Read limit must fit the delimiter');
    const deadline = Date.now() + wait_ms;
    update?.({ content: [{ type: 'text', text: '等待远端数据' }], details: { session_id: id } });
    while (!item.closed && !item.eof && Date.now() < deadline && (!item.buffer.length || until && item.buffer.indexOf(until) < 0 && item.buffer.length < max_bytes)) {
      signal?.throwIfAborted(); await delay(Math.min(25, Math.max(1, deadline - Date.now())), undefined, { signal });
    }
    await item.persistence;
    const match = until ? item.buffer.indexOf(until) : -1;
    const count = Math.min(max_bytes, match >= 0 ? match + until.length : item.buffer.length);
    const bytes = item.buffer.subarray(0, count), offset = item.offset;
    item.buffer = item.buffer.subarray(count); item.offset += count;
    return { ...this.describe(item), offset, bytes: count, data_base64: bytes.toString('base64'), preview: bytes.toString('utf8').slice(0, 2048), artifact: await this.evidence.artifact(bytes), matched: match >= 0 && match + until.length <= max_bytes,
      timed_out: !item.eof && !item.closed && Date.now() >= deadline && (until ? match < 0 : count === 0) };
  }
  shutdown(id) { const item = this.get(id); this.authority.check(item.target_id, 'tcp'); item.socket.end(); return this.describe(item); }
  async close(id) { const item = this.get(id); item.reason ??= 'client_closed'; item.closed = true; item.socket.destroy(); await item.persistence; return this.describe(item); }
  async closeAll() { await Promise.all([...this.items.keys()].map(id => this.close(id))); }
}
