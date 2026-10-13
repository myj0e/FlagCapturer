// Only provider-returned display text. Signatures, redacted blocks and opaque data stay private.
export function assistantBlocks(message) {
  return (Array.isArray(message?.content) ? message.content : []).flatMap((block, index) => {
    const text = block.type === 'text' ? block.text : block.type === 'thinking' ? block.thinking : undefined;
    return typeof text === 'string' && text ? [{ index, kind: block.type === 'thinking' ? '思考' : 'Agent', text }] : [];
  });
}
export function assistantDelta(event) {
  const delta = event.assistantMessageEvent;
  if (event.type !== 'message_update' || !['text_delta', 'thinking_delta'].includes(delta?.type) || typeof delta.delta !== 'string') return null;
  const kind = delta.type === 'thinking_delta' ? '思考' : 'Agent';
  return { index: delta.contentIndex ?? kind, kind, text: delta.delta };
}

// Bounded presentation copy, separate from Pi's original messages and context.
export class ThinkingBuffer {
  constructor() { this.parts = new Map(); this.size = 0; this.truncated = false; }
  add(index, text) {
    const kept = text.slice(0, Math.max(0, 32768 - this.size));
    this.parts.set(index, (this.parts.get(index) ?? '') + kept);
    this.size += kept.length;
    this.truncated ||= kept.length < text.length;
  }
  replace(blocks) {
    this.parts.clear(); this.size = 0; this.truncated = false;
    for (const block of blocks) this.add(block.index, block.text);
  }
  get count() { return this.parts.size; }
  get text() {
    return [...this.parts.values()].join('\n\n') + (this.truncated ? '\n\n（显示副本已截断，完整原文保留在 Pi 会话中。）' : '');
  }
}
