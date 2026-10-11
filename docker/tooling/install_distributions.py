"""Install already verified release archives. Runs only during image construction."""
from pathlib import Path
import os
import shutil
import subprocess

root = Path('/opt/ctfbot')
cache = Path('/tmp/tool-downloads')


def extract(name, destination, kind, strip=False):
    destination.mkdir(parents=True, exist_ok=True)
    if kind == 'zip':
        subprocess.run(['unzip', '-q', str(cache / name), '-d', str(destination)], check=True)
    else:
        subprocess.run(['tar', '-xf', str(cache / name), '-C', str(destination)] + (['--strip-components=1'] if strip else []), check=True)


def link(name, target):
    destination = Path('/usr/local/bin') / name
    if destination.is_symlink() or destination.exists():
        destination.unlink()
    destination.symlink_to(target)


def wrapper(name, body):
    path = Path('/usr/local/bin') / name
    path.write_text('#!/bin/sh\nset -eu\n' + body + '\n')
    path.chmod(0o755)


extract('jdk', root / 'jdk', 'tar', strip=True)
extract('ghidra', root / 'ghidra', 'zip')
ghidra = next((root / 'ghidra').glob('ghidra_*'))
link('analyzeHeadless', ghidra / 'support/analyzeHeadless')
link('ghidra', ghidra / 'ghidraRun')
extract('jadx', root / 'jadx', 'zip')
for name in ['jadx', 'jadx-gui']:
    (root / 'jadx/bin' / name).chmod(0o755)
    link(name, root / 'jadx/bin' / name)
extract('grpcurl', root / 'grpcurl', 'tar')
link('grpcurl', root / 'grpcurl/grpcurl')
extract('foundry', root / 'foundry', 'tar')
for name in ['forge', 'cast', 'anvil', 'chisel']:
    link(name, root / 'foundry' / name)
shutil.copyfile(cache / 'solc', '/usr/local/bin/solc')
os.chmod('/usr/local/bin/solc', 0o755)
extract('pwndbg', root / 'pwndbg', 'tar')
# The official portable archive carries its own Python and GDB runtime.
entry = root / 'pwndbg/pwndbg/bin/pwndbg'
assert entry.is_file(), 'Unexpected Pwndbg portable archive layout'
wrapper('pwndbg', f'exec "{entry}" "$@"')
extract('cyberchef', root / 'cyberchef', 'zip')
html = next((root / 'cyberchef').rglob('*.html'))
wrapper('cyberchef', f'exec chromium --no-sandbox --disable-dev-shm-usage --user-data-dir="${{XDG_CACHE_HOME:-/work/.cache}}/cyberchef" "$@" "file://{html}"')
extract('networkminer', root / 'networkminer', 'zip')
nm = next((root / 'networkminer').rglob('NetworkMiner.exe'))
# Free Edition writes extracted files beside its executable: copy to private /work.
wrapper('networkminer', f'''runtime="${{XDG_CACHE_HOME:-/work/.cache}}/networkminer"
if [ ! -f "$runtime/NetworkMiner.exe" ]; then
    mkdir -p "$runtime"
    cp -R "{nm.parent}/." "$runtime/"
    chmod -R u+rwX "$runtime"
fi
exec mono "$runtime/NetworkMiner.exe" --noupdatecheck "$@"''')
link('NetworkMiner', '/usr/local/bin/networkminer')
shutil.copyfile(cache / 'burpsuite', root / 'burpsuite.jar')
wrapper('burpsuite', 'exec "$JAVA_HOME/bin/java" -jar /opt/ctfbot/burpsuite.jar "$@"')
shutil.rmtree(cache)
