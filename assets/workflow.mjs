// Executed only by the sandbox Node runtime. No host-side attachment parsing.
import { readFile, realpath, stat, mkdir, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { join, relative } from 'node:path';
const [pack, operation, path, descriptor] = process.argv.slice(2);
const spec = descriptor ? JSON.parse(descriptor) : {};
const root = path.startsWith('/work/.assets/') ? '/work/.assets' : '/challenge';
const target = await realpath(path.startsWith('/work/.assets/') ? path : join('/challenge', path));
if (relative(root, target).startsWith('..') || (await stat(target)).size > 32 * 1024 * 1024) throw new Error('Invalid input');
const data = await readFile(target);
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
if (spec.sha256 && hash(data) !== spec.sha256) throw new Error('Input integrity failure');
const outputs = [];
async function emit(bytes, label) {
  if (!spec.output_dir) return;
  if (bytes.length > 16 * 1024 * 1024 || !/^\/work\/\.workflow-output\/[a-f0-9-]+$/.test(spec.output_dir)) throw new Error('Invalid output size or directory');
  await mkdir(spec.output_dir, { recursive: true });
  const output = `${spec.output_dir}/${outputs.length}`;
  await writeFile(output, bytes, { flag: 'wx', mode: 0o600 });
  outputs.push({ path: output, label, bytes: bytes.length, sha256: hash(bytes) });
}
const out = {
  pack,
  operation,
  status: 'observed',
  outputs,
  input_sha256: hash(data),
  bytes: data.length,
  coverage: {
    path,
    filter: operation,
    truncated: false,
    completeness: 'operation-specific summary; omissions are not absence'
  }
};
const strings = () => {
  const all = [...data.toString('latin1').matchAll(/[ -~]{6,128}/g)];
  out.coverage.truncated ||= all.length > 100;
  return all.slice(0, 100).map(m => ({
    offset: m.index,
    text: m[0]
  }));
};
function gcd(a, b) {
  while (b) [a, b] = [b, a % b];
  return a;
}
function smallPrime(n) {
  if (n < 2n || n > 100000n) return false;
  for (let p = 2n; p * p <= n; p++) if (n % p === 0n) return false;
  return true;
}
function inverse(a, m) {
  let [b, x, y] = [m, 1n, 0n];
  while (b) {
    const q = a / b;
    [a, b] = [b, a - q * b];
    [x, y] = [y, x - q * y];
  }
  if (a !== 1n) throw new Error('No modular inverse');
  return (x % m + m) % m;
}
function power(a, e, n) {
  let r = 1n;
  while (e) {
    if (e & 1n) r = r * a % n;
    a = a * a % n;
    e >>= 1n;
  }
  return r;
}
function crc32(bytes) {
  let crc = 0xffffffff;
  for (const b of bytes) {
    crc ^= b;
    for (let j = 0; j < 8; j++) crc = crc >>> 1 ^ (crc & 1 ? 0xedb88320 : 0);
  }
  return (crc ^ 0xffffffff) >>> 0;
}
try {
if (pack === 'crypto' && operation === 'decode') {
  const text = data.toString('utf8').trim();
  out.candidates = [];
  if (['auto', 'hex'].includes(spec.parameters?.encoding ?? 'auto') && /^(?:[a-fA-F0-9]{2})+$/.test(text)) out.candidates.push({
    encoding: 'hex',
    bytes: Buffer.from(text, 'hex')
  });
  if (text.length && ['auto', 'base64'].includes(spec.parameters?.encoding ?? 'auto') && /^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(text)) out.candidates.push({
    encoding: 'base64',
    bytes: Buffer.from(text, 'base64')
  });
  out.candidates = out.candidates.filter(candidate => candidate.encoding !== 'base64' || candidate.bytes.toString('base64') === text);
  for (const candidate of out.candidates) await emit(candidate.bytes, candidate.encoding);
  out.candidates = out.candidates.map(({ encoding, bytes }) => ({
    encoding, bytes: bytes.length, sha256: hash(bytes),
    data_base64: bytes.subarray(0, 1024).toString('base64'), truncated: bytes.length > 1024
  }));
  if (!out.candidates.length) out.status = 'not_found';
  out.coverage.truncated = out.candidates.some(x => x.truncated);
} else if (pack === 'crypto' && operation === 'rsa') {
  const values = JSON.parse(data.toString('utf8'));
  const parse = value => {
    if (typeof value === 'number' && !Number.isSafeInteger(value)) throw new Error('Large RSA integers must be decimal strings');
    if (!/^\d{1,1234}$/.test(String(value))) throw new Error('Invalid RSA integer');
    return BigInt(value);
  };
  const [n, e, c] = ['n', 'e', 'c'].map(k => parse(values[k]));
  if (n <= 1n || n >= 2n ** 4096n || e <= 1n || e >= n || c < 0n || c >= n) throw new Error('RSA parameters out of bounds');
  out.modulus_bits = n.toString(2).length;
  out.factor_search_limit = 100000;
  for (let p = 2n; p <= 100000n && p * p <= n; p++) if (n % p === 0n) {
    const q = n / p,
      phi = (p - 1n) * (q - 1n);
    out.p = String(p);
    out.q = String(q);
    out.distinct_prime_factors_verified = p !== q && smallPrime(p) && smallPrime(q);
    if (out.distinct_prime_factors_verified && gcd(e, phi) === 1n) {
      let hex = power(c, inverse(e, phi), n).toString(16);
      if (hex.length % 2) hex = '0' + hex;
      const plaintext = Buffer.from(hex, 'hex');
      out.data_base64 = plaintext.toString('base64');
      out.reencryption_matches = power(BigInt('0x' + hex), e, n) === c;
      await emit(plaintext, 'rsa-plaintext');
    }
    out.limitation = 'Candidate factorization and plaintext; not a primality or correctness proof';
    break;
  }
  if (!out.data_base64) { out.status = 'inconclusive'; out.reason = out.p ? 'Factors need independent primality/structure checks or exponent is not invertible' : 'No small factor within search range; other structures are not excluded'; }
} else if (pack === 'forensics' && operation === 'triage') {
  out.signatures = [];
  for (const [format, hex] of [['png','89504e470d0a1a0a'],['pdf','255044462d'],['zip','504b0304'],['elf','7f454c46'],['jpeg','ffd8ff'],['gzip','1f8b08']]) {
    const marker = Buffer.from(hex, 'hex');
    let pos = data.indexOf(marker), count = 0;
    while (pos >= 0 && count++ < 8) { out.signatures.push({ format, offset: pos, validated: false }); pos = data.indexOf(marker, pos + 1); }
    if (pos >= 0) out.coverage.truncated = true;
  }
  out.magic_hex = data.subarray(0, 32).toString('hex');
  out.ascii_strings = strings();
} else if (pack === 'forensics' && operation === 'archive') {
  // Central directory listing only. Never decompress or extract untrusted archives.
  let end = -1;
  for (let i = data.length - 22; i >= Math.max(0, data.length - 65557); i--) if (data.readUInt32LE(i) === 0x06054b50 && i + 22 + data.readUInt16LE(i + 20) === data.length) {
    end = i;
    break;
  }
  if (end < 0) throw new Error('Requires a ZIP central directory');
  const count = data.readUInt16LE(end + 10),
    start = data.readUInt32LE(end + 16);
  const directoryEnd = start + data.readUInt32LE(end + 12);
  if (directoryEnd > end || data.readUInt16LE(end + 6) !== 0 || data.readUInt16LE(end + 8) !== count) throw new Error('Invalid or multi-disk ZIP directory');
  if (count === 65535 || start === 0xffffffff || data.readUInt16LE(end + 4) !== 0) throw new Error('ZIP64 and multi-disk ZIP require a separate tool');
  out.entries = [];
  let pos = start;
  for (let i = 0; i < Math.min(count, 1000); i++) {
    if (pos + 46 > directoryEnd || data.readUInt32LE(pos) !== 0x02014b50) throw new Error('Invalid ZIP directory');
    const name = data.readUInt16LE(pos + 28),
      extra = data.readUInt16LE(pos + 30),
      comment = data.readUInt16LE(pos + 32);
    if (pos + 46 + name + extra + comment > directoryEnd) throw new Error('Truncated ZIP directory');
    out.entries.push({
      index: i,
      name: data.subarray(pos + 46, pos + 46 + Math.min(name, 256)).toString('utf8'),
      name_offset: pos + 46, name_bytes: name, name_truncated: name > 256,
      bytes: data.readUInt32LE(pos + 24),
      compressed_bytes: data.readUInt32LE(pos + 20),
      encrypted: !!(data.readUInt16LE(pos + 8) & 1)
    });
    out.coverage.truncated ||= name > 256;
    pos += 46 + name + extra + comment;
  }
  out.coverage.truncated ||= count > 1000;
  out.extraction = 'disabled';
} else if (pack === 'stego' && operation === 'png') {
  if (data.subarray(0, 8).toString('hex') !== '89504e470d0a1a0a') throw new Error('Requires PNG');
  let offset = 8, textBudget = 16384;
  out.chunks = [];
  while (offset + 12 <= data.length && out.chunks.length < 1000) {
    const length = data.readUInt32BE(offset),
      end = offset + 12 + length;
    if (end > data.length) throw new Error('Truncated PNG chunk');
    const type = data.subarray(offset + 4, offset + 8).toString('ascii'),
      content = data.subarray(offset + 8, end - 4);
    const previewLength = Math.min(content.length, 512, textBudget);
    const textual = ['tEXt', 'iTXt'].includes(type);
    if (textual) { textBudget -= previewLength; out.coverage.truncated ||= previewLength < content.length; }
    out.chunks.push({
      type,
      bytes: length,
      crc_valid: crc32(data.subarray(offset + 4, end - 4)) === data.readUInt32BE(end - 4),
      ...(textual ? {
        text_preview: content.subarray(0, previewLength).toString('utf8'), text_truncated: previewLength < content.length
      } : {})
    });
    offset = end;
    if (type === 'IEND') break;
  }
  out.trailing_bytes = data.length - offset;
  out.has_iend = out.chunks.at(-1)?.type === 'IEND';
  out.coverage.truncated ||= out.chunks.length === 1000 || !out.has_iend;
} else if (pack === 'stego' && operation === 'ppm_lsb') {
  const match = /^P6\s+(\d+)\s+(\d+)\s+255\s/.exec(data.subarray(0, 1024).toString('latin1'));
  if (!match) throw new Error('Requires simple P6 PPM without comments');
  const pixels = data.subarray(match[0].length);
  if (Number(match[1]) * Number(match[2]) * 3 !== pixels.length || pixels.length < 32) throw new Error('Invalid PPM');
  const decode = count => {
    const result = Buffer.alloc(count);
    for (let i = 0; i < count; i++) for (let j = 0; j < 8; j++) result[i] |= (pixels[i * 8 + j] & 1) << 7 - j;
    return result;
  };
  const length = decode(4).readUInt32BE();
  if (length > 16384 || (length + 4) * 8 > pixels.length) throw new Error('Invalid LSB length header');
  out.data_base64 = decode(length + 4).subarray(4).toString('base64');
  const decoded = Buffer.from(out.data_base64, 'base64');
  await emit(decoded, 'ppm-lsb');
  out.data_base64 = decoded.subarray(0, 1024).toString('base64');
  out.coverage.truncated = decoded.length > 1024;
  out.hypothesis = 'RGB LSB, MSB bit order, 32-bit length header';
} else if (['reverse', 'pwn'].includes(pack) && operation === 'elf') {
  if (data.length < 52 || data.subarray(0, 4).toString('hex') !== '7f454c46' || ![1, 2].includes(data[4]) || ![1, 2].includes(data[5])) throw new Error('Requires ELF');
  const bits = data[4] === 1 ? 32 : 64,
    little = data[5] === 1;
  if (bits === 64 && data.length < 64) throw new Error('Truncated ELF64');
  const u16 = i => little ? data.readUInt16LE(i) : data.readUInt16BE(i),
    u32 = i => little ? data.readUInt32LE(i) : data.readUInt32BE(i);
  out.bits = bits;
  out.endian = little ? 'little' : 'big';
  out.elf_type = u16(16);
  out.machine = u16(18);
  out.pie = out.elf_type === 3 ? 'possible ET_DYN' : 'no ET_DYN';
  out.canary = data.includes(Buffer.from('__stack_chk_fail')) ? 'symbol present' : 'unknown';
  out.ascii_strings = strings();
  const offset = bits === 32 ? u32(28) : Number(little ? data.readBigUInt64LE(32) : data.readBigUInt64BE(32));
  const size = u16(bits === 32 ? 42 : 54),
    count = u16(bits === 32 ? 44 : 56);
  if (!Number.isSafeInteger(offset) || count > 4096 || size < (bits === 32 ? 32 : 56) || offset + size * count > data.length) throw new Error('Invalid ELF program headers');
  out.gnu_stack = 'unknown';
  for (let i = 0; i < count; i++) {
    const pos = offset + i * size;
    if (u32(pos) === 0x6474e551) out.gnu_stack = u32(pos + (bits === 32 ? 24 : 4)) & 1 ? 'executable' : 'non-executable';
  }
} else if (pack === 'web' && operation === 'response') {
  const separator = data.indexOf('\r\n\r\n');
  if (separator < 0 || separator > 16384 || !data.subarray(0, 5).equals(Buffer.from('HTTP/'))) throw new Error('Requires raw HTTP response');
  const body = data.subarray(separator + 4);
  out.headers = data.subarray(0, separator).toString('latin1');
  out.body_sha256 = hash(body);
  out.body_preview = body.subarray(0, 4096).toString('utf8');
  out.redirect_followed = false;
  out.coverage.truncated = body.length > 4096;
} else throw new Error('Unsupported workflow');
if (out.coverage.truncated && out.status === 'observed') out.status = 'partial';
} catch (error) {
  if (!descriptor) throw error;
  out.status = /Requires|require a separate|without comments/.test(error.message) ? 'unsupported' : 'failed';
  out.error = error.message;
  process.exitCode = 1;
}
console.log(JSON.stringify(out));
