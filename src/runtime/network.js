import { createConnection, isIP } from 'node:net';
import { join } from 'node:path';
import { randomUUID } from 'node:crypto';
import { DockerRuntime, imageReference } from './docker.js';
import { Sessions } from './sessions.js';
import { readRegular, invariant, sha256, privateDir, base64 } from '../util.js';
import { validate, s } from '../tools/schema.js';
const grantSchema = s.object({
  schema_version: s.int(1, 1),
  kind: s.enum(['tcp', 'local-service']),
  challenge_id: s.text(100),
  task_sha256: s.text(64),
  input_sha256: s.array(s.text(64), 256),
  basis: s.text(4096, {
    minLength: 1
  }),
  expires_at: s.text(100),
  ip: s.text(100),
  port: s.int(1, 65535),
  service: s.object({
    image: s.text(512),
    argv: s.array(s.text(4096), 64)
  })
}, ['schema_version', 'kind', 'challenge_id', 'task_sha256', 'input_sha256', 'basis', 'expires_at', 'port']);
export async function loadGrant(path, workspace, now = Date.now()) {
  const bytes = await readRegular(path, 65536);
  const grant = JSON.parse(bytes.toString());
  validate(grant, grantSchema);
  invariant(grant.basis.trim() && grant.challenge_id === workspace.manifest.id && grant.task_sha256 === workspace.manifest.task_sha256, 'Grant is not bound to this challenge');
  invariant(JSON.stringify([...grant.input_sha256].sort()) === JSON.stringify(workspace.manifest.files.map(x => x.sha256).sort()), 'Grant input hashes mismatch');
  invariant(Number.isFinite(Date.parse(grant.expires_at)) && Date.parse(grant.expires_at) > now, 'Network grant expired');
  if (grant.kind === 'tcp') invariant(isIP(grant.ip) !== 0 && !grant.service, 'Remote grant requires a literal IP; no DNS');else {
    invariant(grant.service && !grant.ip, 'Local service grant requires service image/argv only');
    imageReference(grant.service.image);
    invariant(grant.service.argv.length > 0, 'Service argv is empty');
  }
  return {
    ...grant,
    grant_sha256: sha256(bytes)
  };
}
const EXCHANGE = `import socket,sys,json,base64
port=int(sys.argv[1]); data=base64.b64decode(sys.argv[2]); timeout=float(sys.argv[3])
s=socket.create_connection(('127.0.0.1',port),timeout); s.settimeout(timeout)
try:
 s.sendall(data); s.shutdown(socket.SHUT_WR); out=b''
 while len(out)<16385:
  part=s.recv(min(4096,16385-len(out)))
  if not part: break
  out+=part
 print(json.dumps({'data_base64':base64.b64encode(out[:16384]).decode(),'truncated':len(out)>16384}))
finally: s.close()
`;
export class NetworkBroker {
  constructor(grant, {
    evidence,
    connect = createConnection,
    runtimeFactory = config => new DockerRuntime(config),
    now = Date.now,
    onOwnership = async () => {},
    onLost = () => {}
  } = {}) {
    this.grant = grant;
    this.evidence = evidence;
    this.connect = connect;
    this.runtimeFactory = runtimeFactory;
    this.now = now;
    this.onOwnership = onOwnership;
    this.onLost = onLost;
    this.runtime = null;
    this.sessions = null;
    this.closed = false;
    this.controller = new AbortController();
  }
  remaining() {
    const milliseconds = Date.parse(this.grant.expires_at) - this.now();
    invariant(milliseconds > 0, 'Network grant expired');
    return Math.min(milliseconds, 30000);
  }
  start(signal) {
    invariant(!this.starting && !this.closed, 'Network broker already used');
    this.scheduleExpiry();
    const scopedSignal = signal ? AbortSignal.any([signal, this.controller.signal]) : this.controller.signal;
    this.starting = this.open(scopedSignal);
    return this.starting;
  }
  scheduleExpiry() {
    const left = Date.parse(this.grant.expires_at) - this.now();
    invariant(left > 0, 'Network grant expired');
    this.expiryTimer = setTimeout(() => {
      if (Date.parse(this.grant.expires_at) > this.now()) return this.scheduleExpiry();
      this.close().catch(error => this.onLost(error));
    }, Math.min(left, 2147483647));
    this.expiryTimer.unref();
  }
  async open(signal) {
    this.remaining();
    signal.throwIfAborted();
    if (this.grant.kind === 'local-service') {
      const input = await privateDir(join(this.evidence.root, 'service-input'));
      this.runtime = this.runtimeFactory({
        input,
        image: this.grant.service.image,
        runId: this.evidence.root.split('/').at(-1)
      });
      await this.onOwnership(this.runtime.name);
      await this.runtime.start(signal);
      this.sessions = new Sessions(this.runtime, this.evidence);
      this.service = await this.sessions.start(this.grant.service.argv, signal);
    }
    signal.throwIfAborted();
    this.remaining();
    await this.evidence.append('network_scope', {
      grant: this.grant
    });
  }
  target() {
    return {
      host: this.grant.kind === 'tcp' ? this.grant.ip : 'challenge',
      port: this.grant.port,
      protocol: 'tcp',
      expires_at: this.grant.expires_at
    };
  }
  async exchange({
    host,
    port,
    protocol,
    data_base64
  }, signal) {
    invariant(!this.closed, 'Network broker closed');
    signal = signal ? AbortSignal.any([signal, this.controller.signal]) : this.controller.signal;
    signal?.throwIfAborted();
    invariant(host === this.target().host && port === this.grant.port && protocol === 'tcp', 'Endpoint is outside grant');
    const data = base64(data_base64);
    invariant(data.length <= 16384, 'TCP request exceeds 16 KiB');
    const timeout = this.remaining();
    let bytes,
      truncated = false;
    if (this.grant.kind === 'local-service') {
      invariant(this.sessions.get(this.service.session_id).status === 'running', 'Local service has stopped');
      const result = await this.runtime.execute(['python3', '-I', '-c', EXCHANGE, String(port), data_base64, String(timeout / 1000)], {
        timeout,
        signal,
        limit: 65536
      });
      invariant(result.code === 0 && !result.timedOut && !result.cancelled, 'Local TCP exchange failed');
      const parsed = JSON.parse(result.stdout.toString());
      bytes = base64(parsed.data_base64);
      truncated = parsed.truncated;
    } else {
      const result = await new Promise((resolve, reject) => {
        const socket = this.connect({
            host,
            port,
            family: isIP(host)
          }),
          chunks = [];
        let count = 0,
          settled = false;
        const finish = (error, cut = false) => {
          if (settled) return;
          settled = true;
          clearTimeout(timer);
          signal?.removeEventListener('abort', abort);
          socket.destroy();
          if (error) reject(error);else resolve({
            bytes: Buffer.concat(chunks),
            truncated: cut
          });
        };
        const abort = () => finish(new Error('TCP exchange cancelled'));
        const timer = setTimeout(() => finish(new Error('TCP exchange deadline or grant expiry')), timeout);
        signal?.addEventListener('abort', abort, {
          once: true
        });
        if (signal?.aborted) abort();
        socket.once('connect', () => {
          try {
            this.remaining();
            socket.end(data);
          } catch (error) {
            finish(error);
          }
        });
        socket.on('data', chunk => {
          const remaining = 16384 - count;
          chunks.push(chunk.subarray(0, remaining));
          count += Math.min(remaining, chunk.length);
          if (chunk.length > remaining) finish(null, true);
        });
        socket.once('end', () => finish());
        socket.once('error', error => finish(error));
        socket.once('close', () => {
          if (!settled) finish(new Error('TCP connection closed before response EOF'));
        });
      });
      bytes = result.bytes;
      truncated = result.truncated;
    }
    return {
      data_base64: bytes.toString('base64'),
      request: await this.evidence.artifact(data),
      response: await this.evidence.artifact(bytes),
      coverage: {
        received_bytes: bytes.length,
        truncated,
        filter: 'single authorized TCP exchange; no redirects, DNS or TLS interpretation'
      }
    };
  }
  close() {
    if (!this.closing) {
      this.closed = true;
      clearTimeout(this.expiryTimer);
      this.controller.abort(new Error('Network broker closed'));
      this.closing = this.dispose();
    }
    return this.closing;
  }
  async dispose() {
    // A create/start already in flight may finish after cancellation. Wait before final cleanup.
    await this.starting?.catch(() => {});
    const results = await Promise.allSettled([this.sessions?.closeAll()]);
    let error = results.find(x => x.status === 'rejected')?.reason;
    try {
      await this.runtime?.close();
    } catch (caught) {
      error = caught;
    }
    if (error) throw error;
  }
}
