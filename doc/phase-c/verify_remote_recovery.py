"""Opt-in real Docker acknowledgement/crash acceptance for C4's offline solver.

Lost acknowledgement intentionally retains an unfinished quarantine journal.
No remote connection, real model, or challenge data is used by this harness.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

from ctfbot.runtime.docker import RuntimeErrorSafe
from ctfbot.runtime.service_recovery import daemon_identity, read_private, recover, unfinished, write_private
from ctfbot.runtime.supervised import SupervisedOfflineRuntime


def verify(image: str, output: Path):
    output.mkdir(mode=0o700)
    inputs = output/'input'
    inputs.mkdir(mode=0o700)
    identity = daemon_identity()
    records, runtimes = {}, []
    def new(root):
        runtime=SupervisedOfflineRuntime(inputs,image,recovery_root=root,expected_daemon=identity,event_sink=lambda _:None)
        runtimes.append(runtime)
        return runtime
    def removed(runtime):
        assert subprocess.run(['docker','container','inspect',runtime.container_name],capture_output=True).returncode!=0
    wrapper_dir=output/'wrapper'
    wrapper_dir.mkdir(mode=0o700)
    wrapper=wrapper_dir/'docker'
    real=shutil.which('docker')
    assert real
    wrapper.write_text(f'#!{sys.executable}\nimport os,subprocess,sys,time\nreal={real!r}\n'
                       'args=sys.argv[1:]\n'
                       "if args[:1]==[os.environ.get('CTFBOT_RECOVERY_DELAY')]:\n"
                       ' result=subprocess.run([real,*args])\n time.sleep(4)\n sys.exit(result.returncode)\n'
                       'os.execv(real,[real,*args])\n')
    wrapper.chmod(0o700)
    try:
        for command in ['create','start']:
            root=output/('delayed-'+command)
            runtime=new(root)
            runtime._admit()
            argv=runtime._create_argv()[1:]
            argv[1:1]=['--label',f'ctfbot.owner={runtime.owner}']
            if command=='start':
                subprocess.run(['docker',*argv],check=True,capture_output=True)
                argv=['start',runtime.container_name]
            with patch.dict(os.environ,{'PATH':str(wrapper_dir)+os.pathsep+os.environ['PATH'],'CTFBOT_RECOVERY_DELAY':command}):
                try: runtime._mutation(argv,time.monotonic()+.1)
                except TimeoutError: pass
                else: raise AssertionError('delayed mutation did not time out')
                try: runtime.close()
                except RuntimeErrorSafe: pass
                assert unfinished(root)
                denied=new(root)
                try: denied._admit()
                except RuntimeErrorSafe: pass
                else: raise AssertionError('pending admission allowed')
                records['pending_admission']={'status':'blocked','journal':str(runtime._journal)}
                deadline=time.monotonic()+10
                while unfinished(root) and time.monotonic()<deadline:
                    recover(root)
                    time.sleep(.1)
                assert not unfinished(root)
                removed(runtime)
                records['container_'+command+'_recovery']={'status':'cleaned','journal':str(runtime._journal)}
        root=output/'cleanup-failure'
        runtime=new(root)
        runtime.start()
        with patch('ctfbot.runtime.supervised.remove_owned',side_effect=RuntimeErrorSafe('authored cleanup failure')):
            try: runtime.close()
            except RuntimeErrorSafe: pass
            else: raise AssertionError('cleanup failure did not propagate')
        assert unfinished(root)
        denied=new(root)
        try: denied._admit()
        except RuntimeErrorSafe: pass
        else: raise AssertionError('cleanup failure admission allowed')
        assert recover(root)[0]['status']=='cleaned'
        removed(runtime)
        records['cleanup_failure']={'status':'blocked','journal':str(runtime._journal),'failure_injection':'controller remove_owned raised; actual recovery used Docker'}
        # A real child controller exits without closing a running owned solver.
        root=output/'crash'
        ready=output/'child-ready.json'
        child=output/'crash_controller.py'
        child.write_text('import os,sys\nfrom pathlib import Path\n'
                         'from ctfbot.runtime.supervised import SupervisedOfflineRuntime\n'
                         'from ctfbot.runtime.service_recovery import daemon_identity,write_private\n'
                         'r=SupervisedOfflineRuntime(Path(sys.argv[1]),sys.argv[2],recovery_root=Path(sys.argv[3]),expected_daemon=daemon_identity(),event_sink=lambda _:None)\n'
                         'r.start()\nwrite_private(Path(sys.argv[4]),{"name":r.container_name,"journal":str(r._journal)})\nos._exit(17)\n')
        result=subprocess.run([sys.executable,str(child),str(inputs),image,str(root),str(ready)],capture_output=True,timeout=30)
        assert result.returncode==17,result.stderr.decode()
        child_state=read_private(ready)
        assert unfinished(root)
        assert recover(root)[0]['status']=='cleaned'
        assert subprocess.run(['docker','container','inspect',child_state['name']],capture_output=True).returncode!=0
        records['controller_crash_recovery']={'status':'cleaned',**child_state}
        # Real create acknowledgement worker is killed while the Docker wrapper
        # withholds its successful reply. Cleanup cannot infer acknowledgement.
        root=output/'lost-acknowledgement'
        runtime=new(root)
        runtime._admit()
        argv=runtime._create_argv()[1:]
        argv[1:1]=['--label',f'ctfbot.owner={runtime.owner}']
        with patch.dict(os.environ,{'PATH':str(wrapper_dir)+os.pathsep+os.environ['PATH'],'CTFBOT_RECOVERY_DELAY':'create'}):
            try: runtime._mutation(argv,time.monotonic()+.3)
            except TimeoutError: pass
            else: raise AssertionError('missing delayed acknowledgement')
            # Positive control: mutation took effect before terminating its waiter.
            assert subprocess.run([real,'container','inspect',runtime.container_name],capture_output=True).returncode==0
            for worker in runtime._workers:
                worker.kill()
                worker.wait(timeout=2)
            try: runtime.close()
            except RuntimeErrorSafe: pass
            assert recover(root)[0]['status']=='pending_or_uncertain'
            removed(runtime)
            denied=new(root)
            try: denied._admit()
            except RuntimeErrorSafe: pass
            else: raise AssertionError('lost acknowledgement admission allowed')
            time.sleep(2)  # Wrapper's already applied operation exits; no new mutation.
            records['lost_acknowledgement']={'status':'blocked','journal':str(runtime._journal),
                                             'quarantine_retained':True,'resources_removed':True}
        for name,record in records.items(): record['resources_removed']=True
        receipt=output/'acceptance.json'
        write_private(receipt,dict(schema_version=1,result='PARTIAL',solver_image=image,daemon=identity,cases=records,
                                   resources_removed=True,quarantine_retained=str(root),real_model_used=False))
        print(receipt)
    finally:
        for runtime in runtimes:
            try: runtime.close()
            except RuntimeErrorSafe: pass
            removed(runtime)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image',required=True)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    verify(args.image,args.output)
