import test from 'node:test';
import assert from 'node:assert/strict';
import { ThinkingBuffer, assistantBlocks } from '../src/ui/assistant-output.js';

test('thinking buffer counts blocks rather than deltas, bounds memory and replaces final content', () => {
  const buffer = new ThinkingBuffer();
  buffer.add(0, 'Searching ');
  buffer.add(0, 'candidate');
  buffer.add(1, 'Testing method');
  assert.equal(buffer.count, 2);
  assert.equal(buffer.text, 'Searching candidate\n\nTesting method');
  buffer.add(2, 'x'.repeat(40000));
  assert.equal(buffer.size, 32768);
  assert.match(buffer.text, /已截断/);
  buffer.replace(assistantBlocks({ content: [{ type: 'thinking', thinking: 'Final text', thinkingSignature: 'PRIVATE_SIGNATURE' }] }));
  assert.equal(buffer.count, 1);
  assert.equal(buffer.text, 'Final text');
  assert.equal(buffer.truncated, false);
});
