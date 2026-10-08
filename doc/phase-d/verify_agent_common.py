"""Cross-format Docker acceptance for common reliability, without a real model."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import subprocess
from pathlib import Path

from ctfbot.agent.loop import RunLimits
from ctfbot.application.runtime_images import resolve_runtime_image
from ctfbot.application.service import LocalChallengeService
from ctfbot.benchmark.evaluation import _run_metrics
from ctfbot.challenge.single_file import import_single_file
from ctfbot.model_adapters.protocol import ToolCall, TurnResult
from ctfbot.reporting import generate_basic_report
from ctfbot.reporting.diagnosis import diagnose_run
from ctfbot.reporting.replay import audit_run, replay_run
from ctfbot.runtime.service_recovery import daemon_identity, unfinished, write_private
from ctfbot.runtime.supervised import SupervisedOfflineRuntime


class Provider:
    """Authored deterministic trajectory; not evidence of model solve ability."""
    def __init__(self, mode, filename, source, exercise_unlimited=False):
        self.mode, self.filename, self.source = mode, filename, source
        self.exercise_unlimited = exercise_unlimited

    def close(self): pass

    def run_turn(self, prompt, tools, on_tool_call, *, timeout):
        count = 0
        def call(name, **args):
            nonlocal count
            count += 1
            reply = on_tool_call(ToolCall(name,args))
            assert reply.success,reply.content
            return json.loads(reply.content)
        if self.exercise_unlimited and self.mode == 'crypto':
            for _ in range(65):
                call('challenge_list', path='.')
        call('workflow_discover')
        source = call('challenge_read_bytes',path=self.filename,offset=0,length=min(4096,len(self.source)))
        if self.mode == 'unsolved':
            call('claim_record',statement='Input alone does not justify another experiment',state='hypothesis',
                 observation_ids=[source['observation']['id']],experiment_ids=[],unknowns=['method'])
            call('run_complete',outcome='unsolved',summary='No justified next experiment in this authored attempt',unresolved=['method'])
            return TurnResult(text='Explicitly unsolved',tool_calls=count)
        operation = {'crypto':'base64_equals','json':'json_field_equals','http':'http_body_equals'}[self.mode]
        selector = '/payload/flag' if self.mode=='json' else ''
        wrong = call('candidate_submit',candidate='flag{authored-decoy}',unchecked_reason='Deliberate negative fixture')
        failed = call('candidate_check',candidate_id=wrong['candidate_id'],operation=operation,path=self.filename,selector=selector)
        assert not failed['passed'] and not failed['verified']
        expressions = {
            'crypto': "base64.b64decode(data).decode()",
            'json': "json.loads(data)['payload']['flag']",
            'http': "data.split(b'\\r\\n\\r\\n',1)[1].decode()",
        }
        script = ("import base64,json,pathlib\n"
                  f"data=pathlib.Path('/challenge/{self.filename}').read_bytes()\n"
                  f"print({expressions[self.mode]},flush=True)\n")
        call('script_save',path='solve.py',source=script)
        execution = call('script_run',path='solve.py',argv=[])
        value = execution['stdout'].strip()
        assert value.startswith('flag{')
        # Deliberately copy a parameter incorrectly while retaining the correct
        # source hash. Literal parameter binding must expose the mismatch.
        segment = self.source[:8]
        experiment = call('experiment_record',execution_observation_id=execution['observation']['id'],
            observation_ids=[source['observation']['id']],parameters={'copied':'WRONG'},
            transformations=['literal copy'],purpose='negative copy-consistency fixture',reported_result='supports',
            bindings=[{'observation_id':source['observation']['id'],'artifact_path':source['evidence']['artifact'],
                       'offset':0,'length':len(segment),'sha256':hashlib.sha256(segment).hexdigest(),
                       'parameter':'copied','representation':'utf8','locator':'file byte prefix'}])
        assert not experiment['source_consistent']
        claim = call('claim_record',statement='The copied parameter is established',state='supported',
                     observation_ids=[source['observation']['id']],experiment_ids=[experiment['experiment_id']],unknowns=['copy conflict'])
        assert claim['state']=='hypothesis'
        candidate = call('candidate_submit',candidate=value,observation_ids=[execution['observation']['id']],
                         derivation='Derived by the saved input-reading script',unchecked_reason='Await competition confirmation')
        checked = call('candidate_check',candidate_id=candidate['candidate_id'],operation=operation,path=self.filename,selector=selector)
        assert checked['passed'] and not checked['verified']
        call('run_complete',outcome='candidate_unverified',candidate_id=candidate['candidate_id'],
             observation_ids=[execution['observation']['id']],summary='Retain locally checked candidate',
             unresolved=['No trusted answer verification'])
        return TurnResult(text='Authored trajectory completed',tool_calls=count)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--exercise-unlimited', action='store_true', help='Exercise more than 60 requests with the default unlimited count policy')
    args=parser.parse_args()
    output=args.output
    output.mkdir(parents=True,mode=0o700,exist_ok=False)
    choice=resolve_runtime_image()
    assert choice.image,choice.message
    identity=daemon_identity()
    runtimes=[]
    def factory(root,image):
        runtime=SupervisedOfflineRuntime(root,image,recovery_root=output/'runtime-state',
            expected_daemon=identity,event_sink=lambda _:None)
        runtimes.append(runtime)
        return runtime
    sources={
        'crypto':('cipher.b64',base64.b64encode(b'flag{authored-crypto}')),
        'json':('record.json',json.dumps({'padding':'x'*6000,'payload':{'flag':'flag{authored-json}'}}).encode()),
        'http':('response.http',b'HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n\r\nflag{authored-http}'),
        'unsolved':('unknown.txt',b'authored unknown input'),
    }
    rows=[]
    for mode,(filename,data) in sources.items():
        original=output/filename
        original.write_bytes(data)
        workspace=import_single_file(original,output/'imports',authorize_model_data=True)
        service=LocalChallengeService(model_factory=lambda m=mode,f=filename,d=data:Provider(m,f,d,args.exercise_unlimited),
            model_metadata={'provider':'authored-deterministic'},runtime_factory=factory)
        result=service.run(workspace,choice.image,output/'runs',RunLimits(max_turns=2,wall_time_seconds=120))
        if args.exercise_unlimited and mode == 'crypto':
            assert result.tool_calls > 60
        assert result.status == ('unsolved' if mode=='unsolved' else 'candidate_unverified'),result
        run=Path(result.run_dir)
        audit=audit_run(run)
        report=generate_basic_report(run)
        assert 'flag{authored-' not in report.read_text()
        diagnostic=diagnose_run(run)
        assert all(t['controller_prompt'] for t in diagnostic['controller_prompts'])
        assert 'flag{authored-' not in json.dumps(diagnostic)
        if mode!='unsolved':
            assert diagnostic['summary']['local_checks_passed']==1
            assert diagnostic['summary']['source_mismatch_experiments']
            replay=replay_run(run_dir=run,workspace=workspace,output=output/'replays',runtime_factory=factory)
            assert json.loads(replay.read_text())['status']=='matched'
        rows.append({'case':mode,'run_dir':str(run),'status':result.status,'verified':False,'tool_calls':result.tool_calls,
                     'audit':audit,'metrics':_run_metrics(run)})
    assert not unfinished(output/'runtime-state')
    assert all(subprocess.run(['docker','inspect',rt.container_name],capture_output=True).returncode!=0 for rt in runtimes)
    receipt=output/'acceptance.json'
    write_private(receipt,{'status':'passed','result_contract_version':2,'runtime_image':choice.image,
        'unlimited_count_exercised': args.exercise_unlimited,
        'cases':rows,'resources_removed':True,'real_model_used':False,'private_challenge_used':False,
        'claim':'Engineering contracts on authored inputs only; not measured model solve-rate improvement.'})
    print(receipt)


if __name__=='__main__': main()
