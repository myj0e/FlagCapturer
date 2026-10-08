"""Real Docker recovery acceptance with authored data; no model calls or secrets."""
from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
import time
from pathlib import Path

from ctfbot.evidence.store import EvidenceStore
from ctfbot.model_adapters.protocol import ToolCall
from ctfbot.runtime.service_recovery import daemon_identity, unfinished, write_private
from ctfbot.runtime.supervised import SupervisedOfflineRuntime
from ctfbot.tools.registry import ToolRegistry


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--image', required=True, help='Immutable local image ID or repository digest')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(mode=0o700, parents=True, exist_ok=False)
    checks = {}
    with tempfile.TemporaryDirectory(prefix='ctfbot-context-') as temp:
        root = Path(temp)
        challenge, work = root/'challenge', root/'work'
        challenge.mkdir(); work.mkdir()
        (challenge/'input.txt').write_text('authored-input\n')
        runtime = SupervisedOfflineRuntime(challenge, args.image, recovery_root=args.output/'runtime-state',
                                           expected_daemon=daemon_identity(), event_sink=lambda _: None)
        with runtime:
            tools = ToolRegistry(challenge, work, EvidenceStore(args.output/'run'), runtime)
            tools.run_deadline = time.monotonic() + 60
            tools.reliability.context = {'run_id':'context-acceptance'}
            def call(name, **arguments):
                outcome = tools.invoke(ToolCall(name, arguments))
                assert outcome.reply.success, outcome.reply.content
                return json.loads(outcome.reply.content)
            call('script_save',path='solve.py',source=(
                "import time\nfrom pathlib import Path\n"
                "Path('/work/checkpoint.txt').write_text('batch-1')\n"
                "print('READY',flush=True)\ntime.sleep(30)\n"))
            sid = call('script_start',path='solve.py',argv=[])['session_id']
            # Enumeration has no session_read side effect: first read still sees READY.
            sessions = call('state_read',section='sessions')['records']
            checks['session_id_recovered'] = sessions[0]['id'] == sid and sessions[0]['freshness'] == 'unknown'
            read = call('session_read',session_id=sid,wait_seconds=5)
            checks['enumeration_preserves_output'] = 'READY' in read['output'] and read['status'] == 'running'
            call('artifact_export',path='checkpoint.txt',parents=['input.txt'])
            script = call('state_read',section='scripts')['records'][0]
            checkpoint = call('state_read',section='checkpoints')['records'][0]
            checks['script_execution_recovered'] = script['last_execution']['session_id'] == sid
            data = call('artifact_read',artifact=checkpoint['artifact']['artifact'],offset=0,length=64,encoding='utf8')
            checks['explicit_checkpoint_recovered'] = data['data'] == 'batch-1'
            call('session_close',session_id=sid)
            closed = call('state_read',section='sessions',id=sid)['records'][0]
            checks['closed_session_is_historical'] = closed['freshness'] == 'historical'
            output = call('command_run',argv=['python3','-c',"import sys; print('x'*30000+'FINAL'); sys.stderr.write('y'*30000+'ERROR-END')"])
            checks['output_tails_visible'] = (output['previews']['stdout']['tail'].rstrip().endswith('FINAL')
                                             and output['previews']['stderr']['tail'].endswith('ERROR-END'))
            snapshot = tools.context_state.snapshot(reason='acceptance',constraints={'network':'disabled'})
            checks['snapshot_bounded'] = len(snapshot.encode()) <= 8192
        checks['journal_cleanup_complete'] = not unfinished(args.output/'runtime-state')
        checks['container_removed'] = subprocess.run(['docker','inspect',runtime.container_name],capture_output=True).returncode != 0
    receipt = {'passed':all(checks.values()),'image':args.image,'checks':checks,
               'real_model_used':False,'private_challenge':False}
    write_private(args.output/'acceptance.json',receipt)
    print(json.dumps(receipt,indent=2))
    assert receipt['passed']


if __name__ == '__main__':
    main()
