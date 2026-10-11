import { ContextMemory } from '../context/memory.js';
import { budgets, textTokens } from '../context/format.js';

/** Pi owns scheduling, cuts, protocol messages and retries. This adapter supplies only memory. */
export function contextExtension({ state, evidence, model, modelRuntime, signal, task, guidance, onEvent, getSession, estimateTokens }) {
  let fatalError;
  const fail = error => {
    fatalError ??= error;
    getSession()?.abort().catch(() => {});
  };
  const memory = new ContextMemory({ state, evidence, model, task, guidance, signal, notify: async event => { await evidence.append('context_event', { event }); onEvent(event); },
    contextTextTokens: text => estimateTokens({ role: 'custom', content: text }),
    complete: (context, options) => modelRuntime.completeSimple(model, context, options),
    recordUsage: (usage, kind, response) => {
      if (usage) getSession()?.sessionManager.appendUsage('ctf-context-maintenance', response.provider ?? model.provider, response.model ?? model.id, usage, kind);
    }
  });
  const factory = pi => {
    pi.on('turn_end', event => memory.endTurn(event));
    pi.on('context', async event => {
      if (fatalError) { fail(fatalError); return { messages: event.messages }; }
      try { return { messages: await memory.project(event.messages) }; }
      catch (error) { fail(error); return { messages: event.messages }; }
    });
    pi.on('session_before_compact', async event => {
      try {
        const draft = await memory.summarize({ kind: 'compaction',
          messages: [...event.preparation.messagesToSummarize, ...event.preparation.turnPrefixMessages],
          previousHistory: event.preparation.previousSummary ?? '', signal: event.signal });
        return { compaction: {
          summary: draft.history, firstKeptEntryId: event.preparation.firstKeptEntryId,
          tokensBefore: event.preparation.tokensBefore,
          // Each request's usage is recorded immediately via appendUsage, including failed ones.
          details: { ctf_context_version: 1, draft }
        } };
      } catch (error) {
        fail(error);
        return { cancel: true };
      }
    });
    pi.on('session_compact', async event => {
      const draft = event.compactionEntry.details?.draft;
      if (!draft || event.compactionEntry.details.ctf_context_version !== 1) return;
      try { await memory.commit(draft, event.compactionEntry.id); }
      catch (error) { fail(error); }
    });
    // Pi owns whole-request accounting, threshold compaction and overflow recovery.
    // Never count transport JSON: image Base64 and reasoning signatures are not text tokens.
  };
  return {
    factory, memory,
    get failed() { return Boolean(fatalError); },
    check: () => { if (fatalError) throw fatalError; },
    settings: () => {
      const budget = budgets(model);
      return { enabled: true, reserveTokens: Math.min(Math.floor(model.contextWindow * 0.7), budget.reserve + textTokens(JSON.stringify({ task, guidance }))), keepRecentTokens: budget.keepRecent };
    }
  };
}
