"""Export a pinned local tool image context with file/package/license inventory.

Trusted host CPython, static BusyBox, GDB and binutils only. No downloads or
image pulls. Rebuilding the exported context needs Docker but not host tools.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import sysconfig
from pathlib import Path


def capture(argv):
    return subprocess.run(argv,capture_output=True,text=True,timeout=15,check=True).stdout


def file_manifest(root):
    records=[]
    for path in sorted(root.rglob('*')):
        relative=path.relative_to(root).as_posix()
        if path.is_symlink(): records.append(dict(path=relative,symlink=os.readlink(path)))
        elif path.is_file(): records.append(dict(path=relative,bytes=path.stat().st_size,
                                                 mode=stat.S_IMODE(path.stat().st_mode),sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    return records


def build(output: Path, tag: str):
    output.mkdir(mode=0o700)
    context=output/'context'
    context.mkdir(mode=0o700)
    root=context/'rootfs'
    root.mkdir()
    executable=Path(sys.executable).resolve()
    stdlib=Path(sysconfig.get_path('stdlib')).resolve()
    selected={name:Path(shutil.which(name) or '/missing/'+name) for name in ('busybox','gdb','objdump','readelf')}
    for name,path in selected.items():
        if not path.is_file(): raise RuntimeError('required trusted host binary missing: '+name)
    if 'statically linked' not in capture(['file',str(selected['busybox'])]): raise RuntimeError('BusyBox must be static')
    def copy(source,destination=None):
        target=root/(destination or source).relative_to('/')
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(source,target,follow_symlinks=True)
    copy(executable)
    shutil.copytree(stdlib,root/stdlib.relative_to('/'),
                    ignore=shutil.ignore_patterns('__pycache__','site-packages','dist-packages','test','tests'))
    for name,path in selected.items(): copy(path,Path('/bin/busybox') if name=='busybox' else Path('/usr/bin')/name)
    for name in ('sh','sleep'): (root/'bin'/name).symlink_to('busybox')
    for destination in ('usr/local/bin/python3','usr/bin/python3'):
        path=root/destination
        path.parent.mkdir(parents=True,exist_ok=True)
        path.symlink_to(str(executable))
    dependencies=set()
    for binary in [executable,*stdlib.glob('lib-dynload/*.so'),*[p for n,p in selected.items() if n!='busybox']]:
        linked=capture(['ldd',str(binary)])
        if 'not found' in linked: raise RuntimeError('missing shared library: '+str(binary))
        dependencies.update(Path(item) for item in re.findall(r'(/[^\s()]+)',linked))
    for library in sorted(dependencies): copy(library)
    gdb_data=Path('/usr/share/gdb')
    if gdb_data.is_dir(): shutil.copytree(gdb_data,root/'usr/share/gdb',symlinks=True)
    # GDB loads optional Python support from its data directory. All shared
    # libraries are copied from trusted host binaries, never challenge files.
    packages={}
    sources=[executable,*selected.values(),*sorted(dependencies)]
    components=[]
    for source in sources:
        owners=set()
        for path in {source,source.resolve()}:
            result=subprocess.run(['dpkg-query','-S',str(path)],capture_output=True,text=True,timeout=5)
            if result.returncode==0:
                owners.update(owner for line in result.stdout.splitlines() if ': ' in line
                              if re.fullmatch(r'[a-z0-9][a-z0-9+.-]*(?::[a-z0-9]+)?',owner:=line.rsplit(': ',1)[0]))
        for owner in sorted(owners):
            if owner not in packages:
                version=capture(['dpkg-query','-W','--showformat=${Version}',owner]).strip()
                copyright=Path('/usr/share/doc')/owner.split(':')[0]/'copyright'
                ref=None
                if copyright.is_file():
                    destination=Path('/opt/ctfbot/licenses')/(owner.replace(':','_')+'.copyright')
                    copy(copyright,destination)
                    ref=str(destination)
                packages[owner]=dict(version=version,license_file=ref,license_status='recorded' if ref else 'unknown')
        components.append(dict(host_path=str(source),sha256=hashlib.sha256(source.read_bytes()).hexdigest(),packages=sorted(owners)))
    copy(stdlib/'LICENSE.txt',Path('/opt/ctfbot/licenses/CPython-LICENSE.txt'))
    versions={'python3':sys.version}
    for name,path in selected.items():
        result=subprocess.run([str(path),*(['--help'] if name=='busybox' else ['--version'])],capture_output=True,text=True,timeout=10)
        versions[name]=(result.stdout or result.stderr).splitlines()[0]
    inventory=dict(schema_version=1,profile='local-python-binutils-gdb-v1',versions=versions,
                   packages=packages,components=components,network='none',privilege_changes=False,
                   license_scope='local authored acceptance; package copyright files recorded, no redistribution audit',
                   optional_python_modules={'PIL':False,'pwn':False,'sympy':False})
    inventory_path=root/'opt/ctfbot/inventory.json'
    inventory_path.parent.mkdir(parents=True,exist_ok=True)
    inventory_path.write_text(json.dumps(inventory,sort_keys=True,indent=2)+'\n')
    manifest=file_manifest(root)
    payload=json.dumps(manifest,sort_keys=True,separators=(',',':')).encode()
    (output/'files.json').write_bytes(payload)
    (output/'inventory.json').write_text(json.dumps(inventory,sort_keys=True,indent=2)+'\n')
    dockerfile='FROM scratch\nARG SOURCE_DATE_EPOCH=0\nCOPY rootfs/ /\nUSER 65532:65532\nENTRYPOINT ["/bin/sh"]\n'
    (context/'Dockerfile').write_text(dockerfile)
    # Freeze the exported context's mtimes; context plus file inventory is the
    # reproducible input. A changed host export intentionally gets a new hash.
    for path in sorted(context.rglob('*'),reverse=True): os.utime(path,(0,0),follow_symlinks=False)
    os.utime(context,(0,0))
    subprocess.run(['docker','build','--pull=false','--network=none','--build-arg','SOURCE_DATE_EPOCH=0','-t',tag,str(context)],check=True,timeout=120)
    image=capture(['docker','image','inspect','--format','{{.Id}}',tag]).strip()
    receipt=dict(schema_version=1,image=image,tag=tag,context=str(context.resolve()),
                 rootfs_manifest_sha256=hashlib.sha256(payload).hexdigest(),
                 dockerfile_sha256=hashlib.sha256(dockerfile.encode()).hexdigest(),
                 builder_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                 inventory_sha256=hashlib.sha256(inventory_path.read_bytes()).hexdigest())
    (output/'build.json').write_text(json.dumps(receipt,sort_keys=True,indent=2)+'\n')
    for filename in ('files.json','inventory.json','build.json'): (output/filename).chmod(0o600)
    print(json.dumps(receipt))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--tag',default='ctfbot-tools:legacy-local')
    args=parser.parse_args()
    build(args.output,args.tag)
