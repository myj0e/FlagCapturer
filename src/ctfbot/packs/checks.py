"""Small source-bound checks; all attachment parsing happens in the sandbox."""

CHECK = r'''
import base64, hashlib, json, pathlib, sys
operation, relative, expected_hash, encoded, selector = sys.argv[1:6]
root = pathlib.Path('/challenge')
path = root / relative
out = {'passed': False, 'verified': False, 'validation_level': 'local_check',
       'checker': operation + ':v1', 'source_path': relative,
       'limitation': 'Only the specified relation is checked; this is not a proof of flag correctness.'}
try:
    if path.is_symlink() or not path.resolve().is_relative_to(root):
        raise ValueError('input path escapes challenge')
    if path.stat().st_size > 1024*1024:
        raise ValueError('check input exceeds 1 MiB')
    data = path.read_bytes()
    out['source_sha256'] = hashlib.sha256(data).hexdigest()
    if out['source_sha256'] != expected_hash:
        out['error_kind'] = 'source_mismatch'
    else:
        candidate = base64.b64decode(encoded, validate=True)
        if operation == 'base64_equals':
            actual = base64.b64decode(data.strip(), validate=True)
            out['passed'] = actual == candidate
        elif operation == 'json_field_equals':
            actual = json.loads(data)
            if selector and not selector.startswith('/'):
                raise ValueError('JSON selector must be an RFC6901 pointer, e.g. /payload/flag')
            for part in selector.split('/')[1:]:
                part = part.replace('~1','/').replace('~0','~')
                actual = actual[int(part)] if isinstance(actual,list) else actual[part]
            out['passed'] = isinstance(actual,str) and actual.encode() == candidate
        elif operation == 'http_body_equals':
            header, sep, body = data.partition(b'\r\n\r\n')
            if not sep or not header.startswith(b'HTTP/'):
                raise ValueError('requires a raw HTTP response; status code alone is not a check')
            out['passed'] = body == candidate
        else:
            raise ValueError('unsupported local checker')
except (ValueError, UnicodeError, KeyError, IndexError, TypeError) as exc:
    out.update(error_kind='input_format_mismatch', message=str(exc)[:500])
print(json.dumps(out,ensure_ascii=True))
'''
