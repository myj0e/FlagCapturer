import { readRegular, invariant, sha256 } from '../util.js';
import { s, validate } from './schema.js';
import { knowledgeCatalog, searchKnowledge } from './knowledge.js';
export async function installOptional(registry, {
  broker,
  memoryPath
}) {
  if (broker) registry.define('remote_tcp_exchange', 'One fresh bounded TCP exchange to the exact operator-granted endpoint only. No DNS, redirects or proxy handling. ' + JSON.stringify(broker.target()), s.object({
    host: s.text(100),
    port: s.int(1, 65535),
    protocol: s.enum(['tcp']),
    data_base64: s.text(21848)
  }), (args, signal) => broker.exchange(args, signal), {
    observe: true
  });
  if (broker) registry.define('http_request', 'Send one bounded GET or HEAD to the exact granted TCP endpoint. Plain HTTP only; redirects are never followed.', s.object({
    method: s.enum(['GET', 'HEAD']),
    path: s.text(2048, {
      minLength: 1
    })
  }), async ({
    method,
    path
  }, signal) => {
    invariant(path.startsWith('/') && !/[\r\n\0 ]/.test(path), 'Invalid HTTP request target');
    const target = broker.target();
    const host = target.host.includes(':') ? `[${target.host}]` : target.host;
    const request = `${method} ${path} HTTP/1.1\r\nHost: ${host}:${target.port}\r\nConnection: close\r\n\r\n`;
    return {
      ...(await broker.exchange({
        ...target,
        data_base64: Buffer.from(request).toString('base64')
      }, signal)),
      redirect_followed: false
    };
  }, {
    observe: true
  });
  if (memoryPath) {
    const bytes = await readRegular(memoryPath, 4 * 1024 * 1024),
      memory = JSON.parse(bytes.toString());
    const entry = s.object({
      id: s.text(100),
      namespace: s.text(100),
      body: s.text(65536),
      body_sha256: s.text(64),
      source_url: s.text(2048),
      license: s.text(256, {
        minLength: 1
      }),
      tags: s.array(s.text(100), 32),
      contamination: s.enum(['general', 'development', 'holdout'])
    });
    validate(memory, s.object({
      schema_version: s.int(1, 1),
      reviewed: s.bool(),
      basis: s.text(4096, {
        minLength: 1
      }),
      namespaces: s.array(s.text(100), 32),
      entries: s.array(entry, 1000)
    }));
    invariant(memory.reviewed === true && memory.basis.trim(), 'Memory must be explicitly reviewed');
    const entries = new Map();
    for (const item of memory.entries) {
      invariant(memory.namespaces.includes(item.namespace) && sha256(item.body) === item.body_sha256 && !entries.has(item.id), 'Invalid memory scope/hash/ID');
      invariant(item.contamination !== 'holdout', 'Holdout memory cannot be exposed to solver');
      entries.set(item.id, item);
    }
    await registry.evidence.append('memory_snapshot', {
      sha256: sha256(bytes),
      basis: memory.basis,
      namespaces: memory.namespaces
    });
    for (const item of entries.values()) knowledgeCatalog(registry).set(`memory/${item.id}`, { ...item, id: `memory/${item.id}`, memory_id: item.id, origin: 'reviewed-memory' });
    registry.define('memory_search', 'Search only the explicitly reviewed memory snapshot. No network or automatic writes.', s.object({
      query: s.text(256)
    }), async ({
      query
    }) => ({
      matches: searchKnowledge([...knowledgeCatalog(registry).values()].filter(x => x.origin === 'reviewed-memory'), query, 10).map(match => {
        const { body, ...metadata } = entries.get(match.memory_id);
        return { ...metadata, matched_terms: match.matched_terms };
      })
    }));
    registry.define('memory_read', 'Read one authorized memory entry with source and license.', s.object({
      id: s.text(100)
    }), async ({
      id
    }) => {
      const item = entries.get(id);
      invariant(item, 'Unknown memory entry');
      return {
        entry: item,
        artifact: await registry.evidence.artifact(item.body, 'text/markdown')
      };
    }, {
      observe: true
    });
  }
}
