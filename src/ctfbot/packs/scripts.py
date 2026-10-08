"""Small authored analysis programs. Execute these only inside the sandbox."""

ANALYZE = r'''
import base64, hashlib, json, math, pathlib, re, struct, sys, zipfile, zlib
pack, operation, relative = sys.argv[1:4]
root = pathlib.Path('/challenge')
path = root / relative
if path.is_symlink() or not path.resolve().is_relative_to(root):
    raise ValueError('input path escapes challenge')
if path.stat().st_size > 32 * 1024 * 1024:
    raise ValueError('input exceeds 32 MiB')
data = path.read_bytes()
out = {'pack': pack, 'operation': operation, 'input_sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}
out['coverage'] = {'source_kind':'file','path':relative,'input_sha256':out['input_sha256'],
                   'input_bytes':len(data),'operation':operation,'completeness':'operation-specific summary only',
                   'filter':'see operation contract; absence from summary is not absence from input', 'truncated':False}
if pack == 'crypto' and operation == 'decode':
    decoded = []
    for name, decoder in [('hex', bytes.fromhex), ('base64', lambda s: base64.b64decode(s, validate=True))]:
        try:
            value = decoder(data.decode().strip())
            decoded.append({'encoding': name, 'data_base64': base64.b64encode(value[:16384]).decode(), 'truncated': len(value)>16384})
        except (ValueError, UnicodeError):
            pass
    out['candidates'] = decoded
    out['coverage'].update(filter='hex/base64 decoding candidates; each decoded preview capped at 16384 bytes',
                           truncated=any(item['truncated'] for item in decoded))
elif pack == 'crypto' and operation == 'rsa':
    try:
        values = json.loads(data)
        if not isinstance(values,dict) or not all(key in values for key in ('n','e','c')):
            raise ValueError('missing n/e/c object fields')
        n, e, c = (int(values[key]) for key in ('n', 'e', 'c'))
    except (ValueError,TypeError,UnicodeError) as exc:
        raise ValueError('ctfbot input format mismatch: crypto/rsa requires UTF-8 JSON integer fields n/e/c') from exc
    if not 1 < n < 2**4096 or not 1 < e < n or not 0 <= c < n:
        raise ValueError('RSA parameters exceed bounded workflow')
    out.update(modulus_bits=n.bit_length(), exponent=e, factor_search_limit=100000)
    p = next((p for p in range(2, min(math.isqrt(n), 100000)+1) if n%p == 0), None)
    if p:
        q = n//p
        phi = (p-1)*(q-1)
        try:
            m = pow(c, pow(e, -1, phi), n)
            out.update(p=str(p), q=str(q), data_base64=base64.b64encode(m.to_bytes(max(1,(m.bit_length()+7)//8),'big')).decode(), factorization_status='candidate; verify plaintext independently')
        except ValueError:
            out['status'] = 'factor found; inverse unavailable'
    else:
        out['status'] = 'no small factor; use a separate bounded hypothesis'
elif pack == 'forensics' and operation == 'archive':
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        out['entries'] = [{'name': item.filename, 'bytes': item.file_size, 'compressed_bytes': item.compress_size,
                           'encrypted': bool(item.flag_bits & 1)} for item in entries[:1000]]
        out['truncated'] = len(entries)>1000
        out['coverage'].update(filter='first 1000 archive entries; extraction disabled',truncated=len(entries)>1000)
        out['extraction'] = 'disabled; use a bounded script and artifact_export with lineage'
elif pack == 'forensics' and operation == 'triage':
    out.update(magic_hex=data[:32].hex(), ascii_strings=[m.decode('ascii') for m in re.findall(rb'[ -~]{6,128}',data)[:100]])
    out['coverage'].update(filter='magic: first 32 bytes; ASCII chunks length 6..128, first 100 only',
                           truncated=len(re.findall(rb'[ -~]{6,128}',data))>100)
elif pack == 'stego' and operation == 'png':
    if not data.startswith(b'\x89PNG\r\n\x1a\n'):
        raise ValueError('not a PNG')
    offset, chunks = 8, []
    while offset+12<=len(data) and len(chunks)<1000:
        size = int.from_bytes(data[offset:offset+4],'big')
        end = offset+12+size
        if end>len(data): raise ValueError('truncated PNG chunk')
        kind, content = data[offset+4:offset+8], data[offset+8:offset+8+size]
        item={'type':kind.decode('ascii',errors='replace'),'bytes':size,
              'crc_valid': zlib.crc32(kind+content)&0xffffffff == int.from_bytes(data[end-4:end],'big')}
        if kind in (b'tEXt',b'iTXt'):
            item['text_preview'] = content[:4096].decode('utf-8',errors='replace')
        chunks.append(item); offset=end
        if kind==b'IEND': break
    out['chunks']=chunks
elif pack == 'stego' and operation == 'ppm_lsb':
    header = re.match(rb'P6\s+(\d+)\s+(\d+)\s+255\s', data)
    if not header: raise ValueError('requires simple P6 PPM without comments')
    pixels=data[header.end():]
    width,height=map(int,header.groups())
    if width*height*3 != len(pixels): raise ValueError('PPM dimensions do not match payload')
    def decode(count):
        return bytes(sum((pixels[i*8+j]&1)<<(7-j) for j in range(8)) for i in range(count))
    if len(pixels)<32: raise ValueError('not enough pixels for length header')
    length=int.from_bytes(decode(4),'big')
    if length>16384 or (length+4)*8>len(pixels): raise ValueError('LSB length header exceeds cap')
    out.update(data_base64=base64.b64encode(decode(length+4)[4:]).decode(), hypothesis='RGB LSB, MSB bit order, 32-bit length header; fixture format only')
elif pack in ('reverse','pwn') and operation == 'elf':
    if len(data)<52 or not data.startswith(b'\x7fELF') or data[4] not in (1,2) or data[5] not in (1,2):
        raise ValueError('requires ELF32/ELF64')
    bits=32 if data[4]==1 else 64; endian='<' if data[5]==1 else '>'
    kind,machine=struct.unpack_from(endian+'HH',data,16)
    out.update(bits=bits,endian='little' if endian=='<' else 'big',elf_type=kind,machine=machine,
               pie='possible ET_DYN' if kind==3 else 'no ET_DYN',canary='symbol present' if b'__stack_chk_fail' in data else 'unknown',
               ascii_strings=[m.decode('ascii') for m in re.findall(rb'[ -~]{6,128}',data)[:100]])
    out['coverage'].update(filter='ELF headers and ASCII chunks length 6..128, first 100 only; not full disassembly',
                           truncated=len(re.findall(rb'[ -~]{6,128}',data))>100)
    offset=struct.unpack_from(endian+('I' if bits==32 else 'Q'),data,28 if bits==32 else 32)[0]
    size,count=struct.unpack_from(endian+'HH',data,42 if bits==32 else 54)
    if count>4096 or size<(32 if bits==32 else 56) or offset+size*count>len(data):
        raise ValueError('invalid ELF program header table')
    stack='unknown'
    for i in range(count):
        pos=offset+i*size; typ=struct.unpack_from(endian+'I',data,pos)[0]
        flags=struct.unpack_from(endian+'I',data,pos+(24 if bits==32 else 4))[0]
        if typ==0x6474e551: stack='executable' if flags&1 else 'non-executable'
    out['gnu_stack']=stack
elif pack == 'web' and operation == 'response':
    head, separator, body=data.partition(b'\r\n\r\n')
    if not separator or not head.startswith(b'HTTP/') or len(head)>16384:
        raise ValueError('requires bounded raw HTTP response')
    out.update(status_line=head.split(b'\r\n')[0].decode('latin1'),headers=head.decode('latin1'),
               body_sha256=hashlib.sha256(body).hexdigest(),body_preview=body[:4096].decode('utf-8',errors='replace'),
               redirect_followed=False)
    out['coverage'].update(filter='raw HTTP headers; first 4096 body bytes, no transfer/compression decoding',
                           truncated=len(body)>4096)
else:
    raise ValueError('unsupported workflow')
print(json.dumps(out,ensure_ascii=True))
'''

ENVIRONMENT = r'''
import importlib.util,json,pathlib,shutil,sys
inventory_path=pathlib.Path('/opt/ctfbot/inventory.json')
inventory=json.loads(inventory_path.read_text()) if inventory_path.is_file() else {}
print(json.dumps({'python':sys.version,'binaries':{name:shutil.which(name) for name in sys.argv[1:]},
                  'optional_modules':{name:importlib.util.find_spec(name) is not None for name in
                  ('PIL','pwn','sympy','Crypto','gmpy2','z3','capstone','unicorn','elftools','pefile',
                   'numpy','scipy','requests','bs4','lxml','scapy','magic','construct','bitstring','dpkt','pyshark')},
                  'tooling_profile':inventory.get('profile','unspecified'),
                  'inventory_path':str(inventory_path) if inventory else None}))
'''
