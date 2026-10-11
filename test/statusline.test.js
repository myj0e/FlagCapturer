import test from 'node:test';
import assert from 'node:assert/strict';
import { elapsedTime, compactTokens, statusline } from '../src/ui/statusline.js';

test('status elapsed time changes units at minute and hour boundaries', () => {
  for (const [seconds, expected] of [[0, '0s'], [59, '59s'], [60, '1:00'], [61, '1:01'], [3599, '59:59'], [3600, '1:00:00'], [3661, '1:01:01'], [360000, '100:00:00']]) assert.equal(elapsedTime(seconds * 1000 + 999), expected);
});
test('token counts abbreviate K and M without misleading unit boundaries', () => {
  for (const [tokens, expected] of [[0, '0'], [999, '999'], [1000, '1K'], [12500, '12.5K'], [128000, '128K'], [999999, '1M'], [1000000, '1M'], [1250000, '1.3M'], [undefined, '—']]) assert.equal(compactTokens(tokens), expected);
});
test('context uses current Pi context instead of cumulative session tokens and preserves unknown usage', () => {
  const state = { elapsed: 61000, tools: 2, candidates: [], provider: 'fixture', contextWindow: 128000, usage: { tokens: { total: 1250000 }, cost: 0 }, context: { tokens: 32000, contextWindow: 128000, percent: 25 } };
  assert.match(statusline(state), /1:01 · 1.3M tokens · 上下文 25% \(32K\/128K\)/);
  state.context = { tokens: null, contextWindow: 128000, percent: null };
  assert.match(statusline(state), /上下文 待更新 \(—\/128K\)/);
  assert.doesNotMatch(statusline(state), /上下文 0%/);
  state.context = { tokens: 0, contextWindow: 200000 };
  assert.match(statusline(state), /上下文 0% \(0\/200K\)/);
  state.context = { tokens: 220000, contextWindow: 200000 };
  assert.match(statusline(state), /上下文 110%/);
});
