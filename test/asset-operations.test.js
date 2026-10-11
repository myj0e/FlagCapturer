import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { deflateRawSync, inflateRawSync } from 'node:zlib';
const program = (await readFile(new URL('../assets/asset-operations.mjs', import.meta.url), 'utf8')).replace(/^import .*;\n/gm, '');
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
function crc32(bytes) {
  let crc = 0xffffffff;
  for (const b of bytes) { crc ^= b; for (let i = 0; i < 8; i++) crc = crc >>> 1 ^ (crc & 1 ? 0xedb88320 : 0); }
  return (crc ^ 0xffffffff) >>> 0;
}
// Authored ZIP structures exercise bounds and interpretation. No user attachments are parsed on host.
function zip(bytes, { mode = 0o100600, flags = 0, compression = 8, corrupt = false, advertised = bytes.length } = {}) {
  const name = Buffer.from('../untrusted/name'), payload = compression === 8 ? deflateRawSync(bytes) : bytes;
  const local = Buffer.alloc(30), central = Buffer.alloc(46), end = Buffer.alloc(22);
  local.writeUInt32LE(0x04034b50); local.writeUInt16LE(flags, 6); local.writeUInt16LE(compression, 8); local.writeUInt16LE(name.length, 26);
  central.writeUInt32LE(0x02014b50); central.writeUInt16LE(flags, 8); central.writeUInt16LE(compression, 10);
  central.writeUInt32LE(corrupt ? 0 : crc32(bytes), 16); central.writeUInt32LE(payload.length, 20); central.writeUInt32LE(advertised, 24);
  central.writeUInt16LE(name.length, 28); central.writeUInt32LE((mode << 16) >>> 0, 38);
  const start = local.length + name.length + payload.length;
  end.writeUInt32LE(0x06054b50); end.writeUInt16LE(1, 8); end.writeUInt16LE(1, 10); end.writeUInt32LE(central.length + name.length, 12); end.writeUInt32LE(start, 16);
  return Buffer.concat([local, name, payload, central, name, end]);
}
async function execute(bytes, parameters = { entry_index: 0 }) {
  const writes = new Map(), path = '/challenge/authored.zip';
  const spec = { inputs: [{ path, sha256: hash(bytes) }], operation: 'extract', parameters, output_dir: '/work/.workflow-output/123abc' };
  let result;
  const context = vm.createContext({ Buffer, createHash, inflateRawSync,
    process: { argv: ['node', 'asset-operations', JSON.stringify(spec)], exitCode: 0 },
    readFile: async () => bytes, realpath: async x => x,
    stat: async () => ({ size: bytes.length, isFile: () => true }), mkdir: async () => {},
    writeFile: async (path, data) => writes.set(path, data),
    console: { log: text => { result = JSON.parse(text); } }
  });
  await new vm.Script(`(async()=>{${program}\n})()`).runInContext(context);
  return { result, writes };
}
test('ZIP extraction validates content and always generates its own destination', async () => {
  for (const compression of [0, 8]) {
    const { result, writes } = await execute(zip(Buffer.from('authored'), { compression }));
    assert.equal(result.status, 'observed');
    assert.equal(result.entry.crc_valid, true);
    assert.equal(writes.get('/work/.workflow-output/123abc/0').toString(), 'authored');
    assert.equal(writes.size, 1);
  }
});
test('ZIP extraction rejects encrypted, symlink, corrupt, oversized and unsupported members before capture', async () => {
  for (const options of [{ flags: 1 }, { mode: 0o120777 }, { corrupt: true }, { advertised: 17 * 1024 * 1024 }, { compression: 12 }]) {
    const { result, writes } = await execute(zip(Buffer.from('authored'), options));
    assert.ok(['unsupported', 'failed'].includes(result.status));
    assert.equal(writes.size, 0);
  }
  const outside = await execute(zip(Buffer.from('x')), { entry_index: 1 });
  assert.equal(outside.result.status, 'failed');
  const truncated = await execute(zip(Buffer.from('x')).subarray(0, 40));
  assert.equal(truncated.result.status, 'unsupported');
});
