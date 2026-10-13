import { ContextMemory } from '../context/memory.js';
import { budgets, textTokens } from '../context/format.js';
import { maintenanceFailure } from '../context/response.js';
import { publicMessages } from '../context/format.js';
import { randomUUID } from 'node:crypto';

/** Public Pi entry identities survive repeated text and history compaction. */
export function identifyPublicMessages(messages, manager) {
  const native = (manager?.buildSessionProjection?.().entries ?? []).flatMap(({ sourceEntry, messages }) =>
    messages.flatMap((message, index) => {
      const record = publicMessages([message])[0];
      return record ? [{ hash: record.message_id.split(':')[0], timestamp: message.timestamp,
        identity: `pi:${sourceEntry.id}:${index}` }] : [];
    }));
  const originals = messages.filter(m => ['user', 'assistant', 'toolResult'].includes(m.role));
  const records = publicMessages(originals);
  const byContent = new Map();
  native.forEach((item, index) => {
    const key = `${item.hash}:${item.timestamp}`;
    if (!byContent.has(key)) byContent.set(key, []);
    byContent.get(key).push({ ...item, index });
  });
  let cursor = 0;
  const identities = records.map((record, i) => {
    const key = `${record.message_id.split(':')[0]}:${originals[i].timestamp}`;
    const matches = (byContent.get(key) ?? []).filter(item => item.index >= cursor);
    if (matches.length !== 1) return undefined; // Unknown identity stays uncovered conservatively.
    cursor = matches[0].index + 1;
    return matches[0].identity;
  });
  return publicMessages(originals, identities);
}

/** Pi owns scheduling, cuts, protocol messages and retries. This adapter supplies only memory. */
export function contextExtension({ state, evidence, model, modelRuntime, signal, task, guidance, onEvent, getSession, estimateTokens }) {
  let fatalError;
  const pendingUsage = [];
  const flushUsage = () => {
    const manager = getSession()?.sessionManager;
    if (!manager) return;
    while (pendingUsage.length) {
      const receipt = pendingUsage[0];
      manager.appendUsage('ctf-context-maintenance', receipt.provider, receipt.model, receipt.usage, `${receipt.kind}:${receipt.id}`);
      pendingUsage.shift();
    }
  };
  const fail = error => {
    fatalError ??= error;
    getSession()?.abort().catch(() => {});
  };
  const memory = new ContextMemory({ state, evidence, model, task, guidance, signal, notify: async event => { await evidence.append('context_event', { event }); onEvent(event); },
    contextTextTokens: text => estimateTokens({ role: 'custom', content: text }),
    identifyMessages: messages => identifyPublicMessages(messages, getSession()?.sessionManager),
    complete: (context, options) => modelRuntime.completeSimple(model, context, options),
    recordUsage: async (usage, kind, response) => {
      if (!usage) return;
      const receipt = { id: randomUUID(), provider: response.provider ?? model.provider, model: response.model ?? model.id,
        usage: structuredClone(usage), kind };
      pendingUsage.push(receipt);
      await evidence.append('context_usage', receipt);
    }
  });
  const factory = pi => {
    pi.on('turn_end', event => memory.endTurn(event));
    pi.on('context', async event => {
      if (fatalError) { fail(fatalError); return { messages: event.messages }; }
      try { flushUsage(); const messages = await memory.project(event.messages); flushUsage(); return { messages }; }
      catch (error) { fail(error); return { messages: event.messages }; }
    });
    pi.on('session_before_compact', async event => {
      try {
        memory.beginCompaction();
        flushUsage();
        const draft = await memory.summarize({ kind: 'compaction',
          messages: [...event.preparation.messagesToSummarize, ...event.preparation.turnPrefixMessages],
          previousHistory: event.preparation.previousSummary ?? '', signal: event.signal });
        return { compaction: {
          summary: draft.history, firstKeptEntryId: event.preparation.firstKeptEntryId,
          tokensBefore: event.preparation.tokensBefore,
          // Usage receipts are persisted independently, then flushed at safe boundaries.
          details: { ctf_context_version: 1, draft }
        } };
      } catch (error) {
        // Failed compaction is also a settled Pi boundary: retain billed repair
        // responses even when no CompactionEntry will be written.
        memory.maintenance = { ...memory.maintenance, state: 'failed', operation: 'compaction', ...maintenanceFailure(error) };
        fail(error);
        try { flushUsage(); } catch (usageError) { fail(usageError); }
        return { cancel: true };
      }
    });
    pi.on('session_compact', async event => {
      const draft = event.compactionEntry.details?.draft;
      if (!draft || event.compactionEntry.details.ctf_context_version !== 1) return;
      try { await memory.commit(draft, event.compactionEntry.id); flushUsage(); }
      catch (error) { fail(error); }
    });
    // Pi owns whole-request accounting, threshold compaction and overflow recovery.
    // Never count transport JSON: image Base64 and reasoning signatures are not text tokens.
  };
  return {
    factory, memory, flushUsage,
    usageStats: stats => {
      const tokens = { ...stats.tokens };
      let cost = stats.cost ?? 0;
      for (const { usage } of pendingUsage) {
        for (const key of ['input', 'output', 'cacheRead', 'cacheWrite']) tokens[key] = (tokens[key] ?? 0) + (usage[key] ?? 0);
        tokens.total = (tokens.total ?? 0) + (usage.input ?? 0) + (usage.output ?? 0) + (usage.cacheRead ?? 0) + (usage.cacheWrite ?? 0);
        cost += usage.cost?.total ?? 0;
      }
      return { ...stats, tokens, cost };
    },
    get failed() { return Boolean(fatalError); },
    check: () => { if (fatalError) throw fatalError; },
    settings: () => {
      const budget = budgets(model);
      return { enabled: true, reserveTokens: Math.min(Math.floor(model.contextWindow * 0.7), budget.reserve + textTokens(JSON.stringify({ task, guidance }))), keepRecentTokens: budget.keepRecent };
    }
  };
}
