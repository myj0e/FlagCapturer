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
