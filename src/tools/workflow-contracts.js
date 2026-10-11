import { s } from './schema.js';
const operation = (parameters, description, assumptions = []) => ({ version: '2.0.0', parameters, description, assumptions, binaries: ['node'], modules: [] });
export const operationContracts = {
  'crypto/decode': operation(s.object({ encoding: s.enum(['auto', 'hex', 'base64']) }, []), 'Strict hex/base64 decoding into full derived assets.', ['auto tries independent interpretations; ambiguity is retained']),
  'crypto/rsa': operation(s.object(), 'Bounded small-factor RSA inspection.', ['Only distinct proven-small prime factors enable the two-prime totient formula; no factor is inconclusive']),
  'forensics/triage': operation(s.object(), 'Signature offsets and bounded printable strings.', ['A signature is a lead, not a validated embedded format']),
  'forensics/archive': operation(s.object(), 'ZIP central directory listing; no extraction.'),
  'forensics/extract': operation(s.object({ entry_index: s.int(0, 999) }), 'Extract one ZIP member to a generated asset; validate size and CRC.', ['ZIP64, encrypted, symlink and unsupported compression entries are rejected; output <=16 MiB']),
  'forensics/slice': operation(s.object({ offset: s.int(0, 32 * 1024 * 1024), length: s.int(1, 16 * 1024 * 1024) }), 'Capture an exact byte range as an asset.'),
  'forensics/reassemble': operation(s.object({ positions: s.array(s.int(0, 31), 32), expected_parts: s.int(1, 32) }), 'Concatenate asset_ids according to explicit zero-based positions.', ['Every position must occur once; semantic ordering remains a hypothesis; output <=16 MiB']),
  'stego/png': operation(s.object(), 'PNG chunk, CRC and trailing-data inspection.'),
  'stego/ppm_lsb': operation(s.object(), 'Extract a specific length-prefixed RGB LSB hypothesis.', ['P6 without comments, RGB byte LSB, MSB-first bits, 32-bit big-endian length']),
  'reverse/elf': operation(s.object(), 'Bounded ELF header and program-header inspection.', ['This is not a complete checksec implementation']),
  'pwn/elf': operation(s.object(), 'Bounded ELF header and program-header inspection.', ['This is not a complete checksec implementation']),
  'reverse/disassemble': { ...operation(s.object(), 'objdump disassembly; full output remains evidence.'), binaries: ['python3', 'objdump'] },
  'web/response': operation(s.object(), 'Inspect a saved raw HTTP response.', ['No redirects or network requests; body preview can be incomplete'])
};
