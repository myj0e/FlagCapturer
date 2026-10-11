"""Fetch versioned upstream distributions, verifying every pinned SHA-256."""
import concurrent.futures
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def fetch(item):
    name, spec = item
    target = cache / name
    if target.exists() and hashlib.file_digest(target.open('rb'), 'sha256').hexdigest() == spec['sha256']:
        return name
    temporary = target.with_suffix('.partial')
    subprocess.run(['curl', '-fL', '--retry', '3', '--connect-timeout', '30', '--max-time', '1800', '--continue-at', '-',
                    '--silent', '--show-error', spec['url'], '-o', str(temporary)], check=True)
    with temporary.open('rb') as stream:
        actual = hashlib.file_digest(stream, 'sha256').hexdigest()
    if actual != spec['sha256']:
        temporary.unlink()
        raise RuntimeError(f'{name}: upstream digest mismatch ({actual})')
    temporary.replace(target)
    print(f'Verified {name} {spec["version"]}', flush=True)
    return name


if __name__ == '__main__':
    cache = Path(sys.argv[1])
    cache.mkdir(parents=True, exist_ok=True)
    lock = json.loads(Path(sys.argv[2]).read_text())
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(fetch, lock.items()))
