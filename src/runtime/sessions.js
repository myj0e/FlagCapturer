import { spawn } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { setTimeout as delay } from 'node:timers/promises';
import { invariant, integer } from '../util.js';

/** Interactive pipes, not a PTY. Output is drained even when no model read is pending. */
export class Sessions {
  constructor(runtime, evidence, {
    spawnProcess = spawn
  } = {}) {
    this.runtime = runtime;
    this.evidence = evidence;
    this.spawnProcess = spawnProcess;
    this.items = new Map();
  }
  async start(argv, signal) {
    signal?.throwIfAborted();
    invariant(this.items.size < 64, 'At most 64 retained sessions per run');
    invariant([...this.items.values()].filter(x => x.status === 'running').length < 8, 'At most eight live sessions');
    const id = randomUUID();
    const child = this.spawnProcess('docker', this.runtime.execArgs(argv, id, 30 * 60 * 1000, 16 * 1024 * 1024), {
      stdio: ['pipe', 'pipe', 'pipe'],
      shell: false
    });
    const item = {
      id,
      argv,
      child,
      status: 'running',
      code: null,
      chunks: [],
      bytes: 0,
      dropped: 0,
      started: Date.now(),
      lastOutput: null,
      sequence: 0,
      persistence: Promise.resolve(),
      error: null
    };
    this.items.set(id, item);
    const record = (direction, bytes) => {
      item.persistence = item.persistence.then(async () => {
        const artifact = await this.evidence.artifact(bytes);
        await this.evidence.append('session_io', {
          session_id: id,
          direction,
          artifact,
          seq: ++item.sequence
        });
      }).catch(async error => {
        item.error = error;
        this.runtime.onLost?.(error);
        try {
          await this.runtime.killSession(id);
        } finally {
          child.kill('SIGKILL');
        }
      }).catch(error => {
        item.error = new AggregateError([item.error, error], 'Session persistence and termination failed');
        this.runtime.onLost?.(item.error);
      });
    };
    item.record = record;
    const receive = channel => chunk => {
      item.lastOutput = Date.now();
      record(channel, chunk);
      item.chunks.push(chunk);
      item.bytes += chunk.length;
      while (item.bytes > 1024 * 1024 && item.chunks.length) {
        const lost = item.chunks.shift().length;
        item.bytes -= lost;
        item.dropped += lost;
      }
    };
    child.stdout.on('data', receive('stdout'));
    child.stderr.on('data', receive('stderr'));
    child.stdin.on('error', error => {
      item.stdinError = error.message;
    });
    item.done = new Promise(resolve => {
      child.once('error', error => {
        item.status = 'failed';
        item.error = error;
        resolve();
      });
      child.once('close', code => {
        item.status = code === 124 ? 'timed_out' : code === 125 ? 'output_limit' : code === 0 ? 'exited' : 'failed';
        item.code = code;
        resolve();
      });
    });
    const abort = () => {
      this.close(id).catch(error => {
        item.error = error;
      });
    };
    signal?.addEventListener('abort', abort, {
      once: true
    });
    if (signal?.aborted) abort();
    item.done.then(() => signal?.removeEventListener('abort', abort));
    await this.evidence.append('session_started', {
      session_id: id,
      argv
    });
    return this.describe(item);
  }
  get(id) {
    const item = this.items.get(id);
    invariant(item, 'Unknown session ID');
    return item;
  }
  describe(item) {
    return {
      session_id: item.id,
      state: item.status,
      exit_code: item.code,
      elapsed_ms: Date.now() - item.started,
      idle_ms: item.lastOutput === null ? Date.now() - item.started : Date.now() - item.lastOutput,
      observed_at: new Date().toISOString(),
      retained_bytes: item.bytes,
      dropped_bytes: item.dropped,
      error: item.error?.message ?? item.stdinError ?? null
    };
  }
  async send(id, bytes) {
    invariant(bytes.length <= 16384, 'Session input exceeds 16 KiB');
    const item = this.get(id);
    invariant(item.status === 'running' && !item.child.stdin.destroyed, 'Session is not writable');
    item.record('stdin', bytes);
    await new Promise((resolve, reject) => item.child.stdin.write(bytes, error => error ? reject(error) : resolve()));
    return this.describe(item);
  }
  async read(id, {
    collectSeconds = 0,
    signal
  } = {}) {
    integer(collectSeconds, 0, 60, 'collectSeconds');
    const item = this.get(id);
    if (collectSeconds && item.status === 'running') await Promise.race([delay(collectSeconds * 1000, undefined, {
      signal,
      ref: false
    }), item.done]);
    await item.persistence;
    if (item.error) throw item.error;
    const all = Buffer.concat(item.chunks);
    const data = all.subarray(0, 8192);
    item.chunks = all.length > data.length ? [all.subarray(data.length)] : [];
    item.bytes = all.length - data.length;
    const artifact = await this.evidence.artifact(data);
    return {
      ...this.describe(item),
      output: data.toString('utf8'),
      output_artifact: artifact,
      output_bytes: data.length
    };
  }
  async close(id) {
    const item = this.get(id);
    if (!item.closing) item.closing = this.terminate(item);
    return item.closing;
  }
  async terminate(item) {
    const wasRunning = item.status === 'running';
    if (wasRunning) {
      await this.runtime.killSession(item.id);
      await Promise.race([item.done, delay(2000)]);
      if (item.status === 'running') {
        item.child.kill('SIGKILL');
        await item.done;
      }
    }
    await item.persistence;
    if (item.error) throw item.error;
    if (wasRunning && item.status === 'failed') item.status = 'closed';
    return this.describe(item);
  }
  async closeAll() {
    const results = await Promise.allSettled([...this.items.keys()].map(id => this.close(id)));
    const failures = results.filter(x => x.status === 'rejected');
    if (failures.length) throw new AggregateError(failures.map(x => x.reason), 'Session cleanup failed');
  }
  list() {
    return [...this.items.values()].map(item => this.describe(item));
  }
}
