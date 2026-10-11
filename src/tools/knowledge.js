import { readFile } from 'node:fs/promises';
import { s, validate } from './schema.js';
import { invariant } from '../util.js';

const texts = () => s.array(s.text(2048, { minLength: 1 }), 32);
const methodSchema = s.object({
  id: s.text(100), version: s.text(32), title: s.text(160), summary: s.text(1024),
  tags: texts(), triggers: texts(), prerequisites: texts(), exclusions: texts(),
  probe: s.text(4096), interpretation: s.object({ supports: texts(), contradicts: texts(), inconclusive: texts() }),
  branches: texts(), capabilities: s.object({ binaries: texts(), modules: texts() }),
  operations: texts(), sources: texts(), validation: s.enum(['source-derived', 'synthetic-tested', 'reproduced']),
  license: s.text(256)
});
export async function loadMethods() {
  const cards = JSON.parse(await readFile(new URL('../../assets/methods/catalog.json', import.meta.url), 'utf8'));
  invariant(Array.isArray(cards) && cards.length <= 200, 'Invalid method catalog');
  const ids = new Set();
  for (const card of cards) {
    validate(card, methodSchema);
    invariant(!ids.has(card.id) && /^method\/[a-z0-9-]+$/.test(card.id), 'Invalid or duplicate method ID');
    ids.add(card.id);
  }
  return cards;
}
export function knowledgeCatalog(registry) {
  registry.knowledge ??= new Map();
  return registry.knowledge;
}
export function methodSummary(card) {
  return { id: card.id, version: card.version, title: card.title, summary: card.summary,
    tags: card.tags, capabilities: card.capabilities, validation: card.validation, origin: 'builtin-method' };
}
// Transparent lexical matching, no probabilistic confidence and no environment inference.
export function searchKnowledge(entries, query, limit = 5) {
  const words = query.toLowerCase().match(/[\p{L}\p{N}_-]+/gu) ?? [];
  const terms = words.flatMap(word => /[\p{Script=Han}]/u.test(word) ? [word, ...[...word].slice(0, -1).map((_, i) => [...word].slice(i, i + 2).join(''))] : [word]);
  const tokens = [...new Set(terms)].slice(0, 48);
  return [...entries].map(entry => {
    const title = `${entry.title ?? ''} ${entry.tags.join(' ')}`.toLowerCase();
    const body = `${entry.summary ?? ''} ${(entry.triggers ?? []).join(' ')} ${entry.body ?? ''}`.toLowerCase();
    const matched = tokens.filter(token => title.includes(token) || body.includes(token));
    return { entry, matched, score: matched.reduce((sum, token) => sum + (title.includes(token) ? 3 : 1), 0) };
  }).filter(x => x.score > 0).sort((a, b) => b.score - a.score || a.entry.id.localeCompare(b.entry.id)).slice(0, limit).map(({ entry, matched }) => ({
    ...(entry.origin === 'reviewed-memory' ? {
      id: entry.id, memory_id: entry.memory_id, origin: entry.origin, tags: entry.tags,
      source_url: entry.source_url, license: entry.license, contamination: entry.contamination,
      validation: 'reviewed-source', summary: 'Reviewed external memory; read before applying.'
    } : methodSummary(entry)),
    matched_terms: matched,
    applicability: 'lexical lead only; prerequisites and available capabilities must be checked'
  }));
}
