import { appendFile, writeFile, readdir } from 'node:fs/promises';
import { join } from 'node:path';
import { randomUUID } from 'node:crypto';
import { privateDir, readRegular, confined, sha256, json, invariant, atomicWrite } from '../util.js';
export class EvidenceStore {
  constructor(root) {
    this.root = root;
    this.sequence = 0;
    this.pendingArtifacts = new Map();
    this.eventQueue = Promise.resolve();
  }
  static async create(parent) {
    const root = await privateDir(join(await privateDir(parent), randomUUID()));
    await privateDir(join(root, 'artifacts'));
    await writeFile(join(root, 'events.jsonl'), '', {
      flag: 'wx',
      mode: 0o600
    });
    return new EvidenceStore(root);
  }
  async artifact(value, mediaType = 'application/octet-stream') {
    const bytes = Buffer.isBuffer(value) ? value : Buffer.from(typeof value === 'string' ? value : json(value));
    const digest = sha256(bytes);
    if (this.pendingArtifacts.has(digest)) return this.pendingArtifacts.get(digest);
    const pending = this.writeArtifact(bytes, digest, mediaType);
    this.pendingArtifacts.set(digest, pending);
    try {
      return await pending;
    } finally {
      this.pendingArtifacts.delete(digest);
    }
  }
  async writeArtifact(bytes, digest, mediaType) {
    const path = `artifacts/${digest}`;
    try {
      await writeFile(join(this.root, path), bytes, {
        flag: 'wx',
        mode: 0o600
      });
    } catch (error) {
      if (error.code !== 'EEXIST') throw error;
      await this.read(path);
    }
    return {
      artifact: path,
      sha256: digest,
      bytes: bytes.length,
      media_type: mediaType
    };
  }
  async read(path) {
    invariant(/^artifacts\/[a-f0-9]{64}$/.test(path), 'Invalid evidence reference');
    const bytes = await readRegular(await confined(this.root, path), 32 * 1024 * 1024);
    invariant(sha256(bytes) === path.slice(10), 'Evidence integrity failure');
    return bytes;
  }
  append(type, data = {}) {
    const pending = this.eventQueue.then(async () => {
      const event = {
        ...data,
        schema_version: 1,
        sequence: ++this.sequence,
        time: new Date().toISOString(),
        type
      };
      await appendFile(join(this.root, 'events.jsonl'), JSON.stringify(event) + '\n', {
        mode: 0o600
      });
      return event;
    });
    this.eventQueue = pending;
    return pending;
  }
  async document(name, value) {
    invariant(['writeup.md', 'report.md', 'result.json', 'recovery.json', 'metrics.json'].includes(name), 'Invalid document name');
    await atomicWrite(join(this.root, name), typeof value === 'string' ? value : json(value));
  }
}
export async function readEvents(root) {
  const bytes = await readRegular(await confined(root, 'events.jsonl'), 64 * 1024 * 1024);
  return bytes.toString('utf8').split('\n').filter(Boolean).map((line, index) => {
    try {
      return JSON.parse(line);
    } catch {
      return {
        type: 'invalid_event',
        line: index + 1,
        raw: line
      };
    }
  });
}
export async function audit(root) {
  const store = new EvidenceStore(root);
  const errors = [];
  const events = await readEvents(root);
  for (const event of events) if (event.type === 'invalid_event') errors.push(`Invalid event line ${event.line}`);
  for (const name of await readdir(await confined(root, 'artifacts'))) {
    try {
      await store.read(`artifacts/${name}`);
    } catch (error) {
      errors.push(error.message);
    }
  }
  const references = new Map();
  function visit(value) {
    if (!value || typeof value !== 'object') return;
    if (typeof value.artifact === 'string') references.set(value.artifact, value);
    for (const item of Object.values(value)) visit(item);
  }
  events.forEach(visit);
  for (const [path, reference] of references) {
    try {
      const bytes = await store.read(path);
      invariant(!reference.sha256 || reference.sha256 === sha256(bytes), 'Reference hash mismatch');
      invariant(reference.bytes === undefined || reference.bytes === bytes.length, 'Reference size mismatch');
      if (reference.media_type === 'application/json') visit(JSON.parse(bytes.toString()));
    } catch (error) {
      errors.push(`${path}: ${error.message}`);
    }
  }
  return {
    ok: errors.length === 0,
    events: events.length,
    references: references.size,
    errors
  };
}
