// Parses challenge formats exclusively inside the sandbox.
import { readFile, realpath, stat, mkdir, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { inflateRawSync } from 'node:zlib';
const spec = JSON.parse(process.argv[2]);
const MAX = 16 * 1024 * 1024;
const hash = b => createHash('sha256').update(b).digest('hex');
const need = (condition, message) => { if (!condition) throw new Error(message); };
const inputs = [];
for (const source of spec.inputs) {
  const path = await realpath(source.path);
  need(path.startsWith('/challenge/') || path.startsWith('/work/.assets/'), 'Invalid asset input');
  const info = await stat(path);
  need(info.isFile() && info.size <= 32 * 1024 * 1024, 'Invalid asset size');
  const bytes = await readFile(path);
  need(hash(bytes) === source.sha256, 'Asset input hash mismatch');
  inputs.push(bytes);
}
const out = { operation: spec.operation, status: 'observed', outputs: [], coverage: { truncated: false,
  completeness: 'exact transformation under declared parameters; interpretation remains a hypothesis' } };
async function emit(bytes, label) {
  need(bytes.length <= MAX, 'Output exceeds 16 MiB');
  need(/^\/work\/\.workflow-output\/[a-f0-9-]+$/.test(spec.output_dir), 'Invalid output directory');
  await mkdir(spec.output_dir, { recursive: true });
  const path = `${spec.output_dir}/${out.outputs.length}`;
  await writeFile(path, bytes, { flag: 'wx', mode: 0o600 });
  out.outputs.push({ path, label, bytes: bytes.length, sha256: hash(bytes) });
}
function crc32(bytes) {
  let crc = 0xffffffff;
  for (const b of bytes) { crc ^= b; for (let i = 0; i < 8; i++) crc = crc >>> 1 ^ (crc & 1 ? 0xedb88320 : 0); }
  return (crc ^ 0xffffffff) >>> 0;
}
try {
  const data = inputs[0], p = spec.parameters;
  if (spec.operation === 'slice') {
    need(p.offset + p.length <= data.length, 'Requested range exceeds input');
    await emit(data.subarray(p.offset, p.offset + p.length), 'byte-range');
    out.range = p;
  } else if (spec.operation === 'reassemble') {
    need(inputs.length === p.expected_parts && p.positions.length === inputs.length, 'Missing or extra fragments');
    need(new Set(p.positions).size === inputs.length && p.positions.every(i => i >= 0 && i < inputs.length), 'Positions must cover every fragment exactly once');
    need(inputs.reduce((sum, b) => sum + b.length, 0) <= MAX, 'Reassembly exceeds 16 MiB');
    const ordered = p.positions.map((position, index) => ({ position, bytes: inputs[index] })).sort((a, b) => a.position - b.position);
    out.duplicate_content = new Set(inputs.map(hash)).size < inputs.length;
    out.ordering = 'explicit caller-declared positions; not independently established';
    await emit(Buffer.concat(ordered.map(x => x.bytes)), 'reassembled');
  } else if (spec.operation === 'extract') {
    let end = -1;
    for (let i = data.length - 22; i >= Math.max(0, data.length - 65557); i--) {
      if (data.readUInt32LE(i) === 0x06054b50 && i + 22 + data.readUInt16LE(i + 20) === data.length) { end = i; break; }
    }
    need(end >= 0, 'Requires a complete ZIP central directory');
    const count = data.readUInt16LE(end + 10), start = data.readUInt32LE(end + 16), size = data.readUInt32LE(end + 12);
    need(data.readUInt16LE(end + 4) === 0 && data.readUInt16LE(end + 6) === 0 && data.readUInt16LE(end + 8) === count && count !== 65535 && start !== 0xffffffff && start + size <= end, 'ZIP64 or multi-disk ZIP is unsupported');
    need(p.entry_index < count, 'Entry index out of range');
    let pos = start;
    for (let i = 0; i <= p.entry_index; i++) {
      need(pos + 46 <= start + size && data.readUInt32LE(pos) === 0x02014b50, 'Invalid ZIP entry');
      const nameSize = data.readUInt16LE(pos + 28), extraSize = data.readUInt16LE(pos + 30), commentSize = data.readUInt16LE(pos + 32);
      const next = pos + 46 + nameSize + extraSize + commentSize;
      need(next <= start + size, 'Truncated ZIP entry');
      if (i === p.entry_index) {
        const flags = data.readUInt16LE(pos + 8), method = data.readUInt16LE(pos + 10), crc = data.readUInt32LE(pos + 16),
          compressed = data.readUInt32LE(pos + 20), bytes = data.readUInt32LE(pos + 24), offset = data.readUInt32LE(pos + 42);
        const name = data.subarray(pos + 46, pos + 46 + nameSize), mode = data.readUInt32LE(pos + 38) >>> 16;
        need(!(flags & 1) && !(flags & 64) && [0, 8].includes(method), 'Encrypted or unsupported compression entry');
        need((mode & 0xf000) !== 0xa000 && !name.toString().endsWith('/'), 'Symlink or directory entry is unsupported');
        need(bytes <= MAX && offset !== 0xffffffff && compressed !== 0xffffffff && offset + 30 <= start && data.readUInt32LE(offset) === 0x04034b50, 'Invalid ZIP local entry or output size');
        need(data.readUInt16LE(offset + 6) === flags && data.readUInt16LE(offset + 8) === method, 'ZIP header mismatch');
        const localName = data.readUInt16LE(offset + 26), content = offset + 30 + localName + data.readUInt16LE(offset + 28);
        need(content + compressed <= start && name.equals(data.subarray(offset + 30, offset + 30 + localName)), 'ZIP local name or bounds mismatch');
        const payload = data.subarray(content, content + compressed);
        const decoded = method === 0 ? payload : inflateRawSync(payload, { maxOutputLength: MAX });
        need(decoded.length === bytes && crc32(decoded) === crc, 'ZIP size or CRC mismatch');
        out.entry = { index: i, name: name.subarray(0, 256).toString('utf8'), name_truncated: name.length > 256, compression: method, crc_valid: true };
        // Untrusted archive names never become filesystem paths.
        await emit(decoded, 'zip-member');
      }
      pos = next;
    }
  } else throw new Error('Unsupported asset operation');
} catch (error) {
  out.status = /unsupported|Requires/.test(error.message) ? 'unsupported' : 'failed';
  out.error = error.message;
  process.exitCode = 1;
}
console.log(JSON.stringify(out));
