"""Functional authored toolbox checks under the actual offline runtime profile."""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from ctfbot.runtime.service_recovery import daemon_identity, unfinished, write_private
from ctfbot.runtime.supervised import SupervisedOfflineRuntime


PROBE = r'''
import importlib, io, json, os, pathlib, shutil, subprocess
checked=[]
def run(argv, *, timeout=20, env=None):
    result=subprocess.run(argv,capture_output=True,text=True,timeout=timeout,env=env)
    assert result.returncode==0,(argv,result.returncode,result.stdout,result.stderr)
    return result.stdout+result.stderr
modules=['pwn','Crypto','sympy','gmpy2','z3','capstone','unicorn','elftools','pefile','PIL',
         'numpy','scipy','requests','bs4','lxml','scapy.all','magic','construct','bitstring','dpkt','pyshark']
for name in modules: importlib.import_module(name)
checked.append('all_python_modules_import')
from Crypto.Cipher import AES
key=b'0'*16
data=b'0123456789abcdef'
assert AES.new(key,AES.MODE_ECB).decrypt(AES.new(key,AES.MODE_ECB).encrypt(data))==data
import sympy,gmpy2,z3
assert sympy.factorint(3233)=={53:1,61:1} and int(gmpy2.invert(17,3120))==2753
x=z3.Int('x'); solver=z3.Solver(); solver.add(3*x+7==40)
assert solver.check()==z3.sat and solver.model()[x].as_long()==11
checked.append('aes_rsa_factorization_smt')
import capstone,unicorn
from unicorn.x86_const import UC_X86_REG_RAX
assert len(list(capstone.Cs(capstone.CS_ARCH_X86,capstone.CS_MODE_64).disasm(b'\x90\xc3',0x1000)))==2
emu=unicorn.Uc(unicorn.UC_ARCH_X86,unicorn.UC_MODE_64); emu.mem_map(0x1000,0x1000)
emu.mem_write(0x1000,b'\x48\xc7\xc0\x2a\x00\x00\x00'); emu.emu_start(0x1000,0x1007)
assert emu.reg_read(UC_X86_REG_RAX)==42
checked.append('disassembly_emulation')
root=pathlib.Path('/work')
(root/'probe.c').write_text('#include <stdio.h>\nint main(void) { puts("AUTHORED_TOOL_PROBE"); return 0; }\n')
for bits in ('64','32'):
    executable='/work/probe'+bits
    run(['gcc','-m'+bits,'-g','-O0','-o',executable,'/work/probe.c'])
    assert 'AUTHORED_TOOL_PROBE' in run([executable])
    assert 'ELF'+bits in run(['readelf','-h',executable])
    assert '<main>' in run(['objdump','-d',executable])
    assert ' main' in run(['nm',executable])
    assert 'ELF' in run(['file',executable])
assert 'AUTHORED_TOOL_PROBE' in run(['qemu-i386','/work/probe32'])
checked.append('compile_run_elf32_elf64')
assert run(['factor','3233']).strip()=='3233: 53 61'
checked.append('coreutils_factor')
run(['upx','--force','-q','-o','/work/probe64.packed','/work/probe64'])
run(['upx','-t','/work/probe64.packed'])
assert 'AUTHORED_TOOL_PROBE' in run(['/work/probe64.packed'])
run(['upx','-d','-q','-o','/work/probe64.unpacked','/work/probe64.packed'])
assert 'AUTHORED_TOOL_PROBE' in run(['/work/probe64.unpacked'])
checked.append('upx_pack_test_unpack_execute')
go_env=dict(os.environ,GOCACHE='/work/go-cache',GOPATH='/work/go-path',
            GOTMPDIR='/work/go-tmp',GOPROXY='off',GOSUMDB='off',GOENV='off',GOMAXPROCS='2')
(root/'go-tmp').mkdir()
(root/'probe.go').write_text('package main\nimport "fmt"\nfunc main() { fmt.Println("AUTHORED_GO_PROBE") }\n')
run(['go','version'])
run(['go','build','-p=2','-o','/work/probe-go','/work/probe.go'],timeout=60,env=go_env)
assert 'AUTHORED_GO_PROBE' in run(['/work/probe-go'])
checked.append('offline_go_build_execute')
assert 'VeraCrypt' in run(['veracrypt','--text','--non-interactive','--version'])
assert 'passed' in run(['veracrypt','--text','--non-interactive','--test'],timeout=30).lower()
(root/'volume.key').write_bytes(b'authored-header-probe-key')
with (root/'volume.luks').open('wb') as volume: volume.truncate(32*1024*1024)
run(['cryptsetup','--batch-mode','--disable-locks','--type','luks2',
     '--pbkdf','pbkdf2','--pbkdf-force-iterations','1000','--key-file','/work/volume.key',
     'luksFormat','/work/volume.luks'])
assert 'Version:' in run(['cryptsetup','--disable-locks','luksDump','/work/volume.luks'])
checked.append('veracrypt_selftest_cryptsetup_userspace_luks_header')
gdb=run(['gdb','--batch','-nx','-ex','set debuginfod enabled off','-ex','set disable-randomization off',
         '-ex','break main','-ex','run','-ex','print 6*7','-ex','continue','/work/probe64'])
assert 'Breakpoint 1,' in gdb and '= 42' in gdb and 'exited normally' in gdb,gdb
run(['strace','-o','/work/strace.txt','/work/probe64'])
assert 'execve(' in (root/'strace.txt').read_text()
run(['patchelf','--print-interpreter','/work/probe64'])
checked.append('unprivileged_gdb_child_debug_and_strace')
from pwn import ELF,cyclic,cyclic_find
assert 'main' in ELF('/work/probe64',checksec=False).symbols
assert cyclic_find(cyclic(64)[12:16])==12
import magic
assert 'ELF' in magic.from_file('/work/probe64')
checked.append('pwntools_elf_and_cyclic')
from PIL import Image
buffer=io.BytesIO(); Image.new('RGB',(2,2),(1,2,3)).save(buffer,format='PNG')
(root/'image.png').write_bytes(buffer.getvalue())
assert Image.open(io.BytesIO(buffer.getvalue())).getpixel((0,0))==(1,2,3)
run(['exiftool','/work/image.png'])
assert 'ELF' in run(['binwalk','/work/probe64'])
Image.new('RGB',(128,128),(80,90,100)).save('/work/carrier.bmp')
(root/'note.txt').write_text('authored stego probe')
run(['steghide','embed','-cf','/work/carrier.bmp','-ef','/work/note.txt',
     '-sf','/work/sealed.bmp','-p','authored','-f'])
run(['steghide','extract','-sf','/work/sealed.bmp','-xf','/work/recovered.txt','-p','authored','-f'])
assert (root/'recovered.txt').read_text()=='authored stego probe'
from scapy.all import Ether,IP,UDP,Raw,wrpcap,rdpcap
packet=Ether()/IP(src='192.0.2.1',dst='192.0.2.2')/UDP(sport=1234,dport=4321)/Raw(load=b'authored')
wrpcap('/work/probe.pcap',[packet]); assert bytes(rdpcap('/work/probe.pcap')[0][Raw])==b'authored'
assert '617574686f726564' in run(['tshark','-r','/work/probe.pcap','-T','fields','-e','udp.payload'])
checked.append('png_metadata_binwalk_steghide_pcap_analysis')
import zipfile
with zipfile.ZipFile('/work/probe.zip','w') as archive: archive.writestr('note.txt','authored')
assert 'authored' in run(['unzip','-p','/work/probe.zip','note.txt'])
run(['7z','t','/work/probe.zip'])
checked.append('archive_tools')

assert '42' in run(['node','-e','console.log(6*7)'])
assert '42' in run(['ruby','-e','puts 6*7'])
assert '42' in run(['perl','-e','print 6*7'])
run(['java','-version']); run(['bash','-c','test "$((6*7))" = 42'])
checked.append('script_language_runtimes')
inventory=json.loads(pathlib.Path('/opt/ctfbot/inventory.json').read_text())
assert inventory['profile']=='general-v3'
assert all(inventory['binaries'].values()),inventory['binaries']
assert {'objdump','factor','upx','go','veracrypt','cryptsetup'} <= set(inventory['binaries'])
assert {'bkcrack','fcrackzip','john','zip2john','hashcat','zsteg','sox','sage'} <= set(inventory['binaries'])
assert inventory['external_tools']['upx']['version']=='5.2.0'
assert inventory['external_tools']['veracrypt']['version']=='1.26.29'
assert 'no mount' in inventory['runtime_constraints']['disk_encryption']
run(['python3','-m','pip','check'])
assert (pathlib.Path('/challenge')/'probe.txt').read_text()=='authored read-only input'
for target in ('/challenge/probe.txt','/opt/ctfbot/should-not-write'):
    try: pathlib.Path(target).write_text('forbidden')
    except OSError: pass
    else: raise AssertionError('read-only boundary failed: '+target)
checked.append('inventory_dependencies_readonly_boundaries')
status=dict(line.split(':',1) for line in pathlib.Path('/proc/self/status').read_text().splitlines() if ':' in line)
assert os.geteuid()!=0 and int(status['CapEff'].strip(),16)==0 and status['NoNewPrivs'].strip()=='1'
assert all(line.split()[0]=='lo' for line in pathlib.Path('/proc/net/route').read_text().splitlines()[1:])
checked.append('nonroot_no_capabilities_no_new_privileges_no_network_routes')
print(json.dumps({'status':'passed','checks':checked,'python':__import__('sys').version}))
'''


# Keep cold OpenCL JIT compilation separate from the existing toolbox probe.
EXTRA_TOOL_PROBE = r'''
import json, os, pathlib, subprocess
from PIL import Image
checked=[]
root=pathlib.Path('/work')
(root/'note.txt').write_text('authored stego probe')
def run(argv, *, timeout=20, env=None):
    result=subprocess.run(argv,capture_output=True,text=True,timeout=timeout,env=env)
    assert result.returncode==0,(argv,result.returncode,result.stdout,result.stderr)
    return result.stdout+result.stderr
# Authored encrypted archive: exercise converters and recovery, not just PATH.
(root/'passwords.txt').write_text('wrong\nauthored-password\n')
run(['zip','-j','-P','authored-password','/work/encrypted.zip','/work/note.txt'])
assert 'authored-password' in run(['fcrackzip','-D','-u','-p','/work/passwords.txt','/work/encrypted.zip'])
hashes=run(['zip2john','/work/encrypted.zip'])
(root/'zip.hash').write_text('\n'.join(line for line in hashes.splitlines() if '$pkzip' in line)+'\n')
john_env=dict(os.environ,OMP_NUM_THREADS='2')
run(['john','--format=pkzip','--wordlist=/work/passwords.txt',
     '--session=/work/john-probe','--pot=/work/john-probe.pot','/work/zip.hash'],env=john_env,timeout=60)
assert 'authored-password' in run(['john','--show','--format=pkzip',
                                '--pot=/work/john-probe.pot','/work/zip.hash'])
assert 'bkcrack' in run(['bkcrack','--version']).lower()
run(['bkcrack','-C','/work/encrypted.zip','--password','authored-password',
     '-D','/work/bkcrack-clear.zip'])
assert 'authored stego probe' in run(['unzip','-p','/work/bkcrack-clear.zip','note.txt'])
hashcat_env=dict(os.environ,POCL_MAX_PTHREAD_COUNT='2')
assert 'opencl' in run(['hashcat','-I'],timeout=60,env=hashcat_env).lower()
(root/'md5.hash').write_text('900150983cd24fb0d6963f7d28e17f72\n')
(root/'md5.words').write_text('wrong\nabc\n')
run(['hashcat','-m','0','-a','0','-O','-D','1','--session=hashcat-probe','--restore-file-path=/work/hashcat-probe.restore',
     '--potfile-path=/work/hashcat-probe.pot','/work/md5.hash','/work/md5.words'],
    timeout=100,env=hashcat_env)
assert ':abc' in (root/'hashcat-probe.pot').read_text()
payload=b'AUTHORED_STEGO_PROBE'
carrier=Image.new('RGB',(32,32),(0,0,0))
for bit in range(len(payload)*8):
    carrier.putpixel((bit%32,bit//32),((payload[bit//8]>>(7-bit%8))&1,0,0))
carrier.save('/work/lsb.png')
assert run(['zsteg','-E','b1,r,lsb,xy','/work/lsb.png']).startswith(payload.decode())
run(['sox','-n','-r','44100','/work/tone.wav','synth','0.1','sine','440'])
assert run(['soxi','-s','/work/tone.wav']).strip()=='4410'
assert '53 * 61' in run(['sage','-c','print(factor(3233))'],timeout=60)
checked.append('zip_recovery_jumbo_converters_hashcat_cpu_stego_audio_sage')
print(json.dumps({'status':'passed','checks':checked}))
'''


def verify(image: str, output: Path):
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    challenge=output/'input'
    challenge.mkdir(mode=0o700)
    (challenge/'probe.txt').write_text('authored read-only input')
    state=output/'runtime-state'
    runtime=SupervisedOfflineRuntime(challenge,image,recovery_root=state,
                                     expected_daemon=daemon_identity(),event_sink=lambda _:None)
    with runtime:
        result=runtime.execute(['python3','-c',PROBE],timeout=120)
        write_private(output/'probe-output.json',dict(exit_code=result.exit_code,
                      stdout=result.stdout.decode(errors='replace'),stderr=result.stderr.decode(errors='replace'),
                      timed_out=result.timed_out,truncated=result.truncated))
        assert result.exit_code==0 and not result.timed_out and not result.truncated, (result.stdout,result.stderr)
        probe=json.loads(result.stdout)
        extra=runtime.execute(['python3','-c',EXTRA_TOOL_PROBE],timeout=120)
        write_private(output/'extra-probe-output.json',dict(exit_code=extra.exit_code,
                      stdout=extra.stdout.decode(errors='replace'),stderr=extra.stderr.decode(errors='replace'),
                      timed_out=extra.timed_out,truncated=extra.truncated))
        assert extra.exit_code==0 and not extra.timed_out and not extra.truncated,(extra.stdout,extra.stderr)
        probe['checks'].extend(json.loads(extra.stdout)['checks'])
        metadata=subprocess.run(['docker','inspect',runtime.container_name],check=True,capture_output=True,text=True)
        config=json.loads(metadata.stdout)[0]
        host=config['HostConfig']
        assert host['NetworkMode']=='none' and host['ReadonlyRootfs'] and host['CapDrop']==['ALL']
        assert 'no-new-privileges:true' in host['SecurityOpt']
    assert not unfinished(state)
    assert subprocess.run(['docker','container','inspect',runtime.container_name],capture_output=True).returncode!=0
    write_private(output/'acceptance.json',dict(schema_version=1,status='passed',image=image,
                  probe=probe,network='none',cap_drop=['ALL'],read_only_root=True,
                  resources_removed=True,real_model_used=False))
    print(output/'acceptance.json')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image',required=True)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    verify(args.image,args.output)
