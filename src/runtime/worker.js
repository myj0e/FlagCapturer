import { spawn } from 'node:child_process';
import { readFile, mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { randomUUID } from 'node:crypto';
import { DockerRuntime } from './docker.js';
import { RELAY_SOURCE } from './relay.js';
import { invariant } from '../util.js';

/** Fixed code only; solver tools never receive this runtime or credential paths. */
export class RemoteWorker {
  constructor({ image, runId, gateway, writable = false, onOwnership, onLost = () => {} }) { Object.assign(this, { image, runId, gateway, writable, onOwnership, onLost }); this.pending = new Map(); this.closed = false; }
  async start(signal) {
    this.input = await mkdtemp(join(tmpdir(), 'ctf-worker-'));
    this.runtime = new DockerRuntime({ input: this.input, image: this.image, runId: this.runId, networkSocket: this.gateway.root, networkSocketWritable: this.writable, onLost: this.onLost });
    await this.onOwnership?.(this.runtime.name); await this.runtime.start(signal);
    await this.runtime.write('/work/relay.py', Buffer.from(RELAY_SOURCE), signal);
    const source = await readFile(new URL('./worker.py', import.meta.url), 'utf8');
    this.child = spawn('docker', ['exec', '-i', this.runtime.name, 'python3', '-I', '-u', '-c', source], { stdio: ['pipe', 'pipe', 'pipe'], shell: false });
    let buffer = Buffer.alloc(0);
    this.child.stdout.on('data', chunk => {
      buffer = Buffer.concat([buffer, chunk]);
      if (buffer.length > 32 * 1024 * 1024) { this.fail(new Error('Worker response limit')); return; }
      for (let index; (index = buffer.indexOf(10)) >= 0;) {
        const line = buffer.subarray(0, index); buffer = buffer.subarray(index + 1);
        try { const reply = JSON.parse(line); const item = this.pending.get(reply.id); if (!item) continue; this.pending.delete(reply.id); reply.error ? item.reject(new Error(reply.error)) : item.resolve(reply.result); }
        catch { this.fail(new Error('Worker protocol failed')); }
      }
    });
    this.child.stderr.on('data', () => {}); this.child.stdin.on('error', () => {});
    this.child.once('error', error => this.fail(error)); this.child.once('close', () => this.fail(new Error('Remote worker closed')));
  }
  fail(error) {
    this.unavailable ??= error;
    for (const item of this.pending.values()) item.reject(error);
    this.pending.clear();
    if (this.child && this.child.exitCode === null && !this.child.killed) this.child.kill('SIGKILL');
  }
  async call(method, args, signal) {
    invariant(!this.closed && !this.unavailable, this.unavailable?.message ?? 'Remote worker closed');
    this.starting ??= this.start(signal); await this.starting; invariant(!this.closed && !this.unavailable, this.unavailable?.message ?? 'Remote worker closed'); signal?.throwIfAborted();
    const id = randomUUID(); let abort;
    const result = new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      abort = () => { this.fail(new Error('Remote operation cancelled')); this.closed = true; this.runtime?.close().catch(this.onLost); };
      signal?.addEventListener('abort', abort, { once: true });
      this.child.stdin.write(JSON.stringify({ id, method, args }) + '\n', error => { if (error) this.fail(error); });
    });
    const timer = setTimeout(() => abort(), 65000);
    try { return await result; }
    finally { clearTimeout(timer); signal?.removeEventListener('abort', abort); this.pending.delete(id); }
  }
  async close() { this.closed = true; this.fail(new Error('Run closed')); await this.starting?.catch(() => {}); this.fail(new Error('Run closed')); await this.runtime?.close(); if (this.input) await rm(this.input, { recursive: true, force: true }); }
}
