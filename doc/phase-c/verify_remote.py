"""Opt-in C4 live acceptance subset: authored loopback endpoint and offline Docker.

Without --recovery-acceptance this produces a partial receipt. With matching
live recovery evidence it also checks temporary reviewed activation and actual
CLI/TUI entrypoints; it never installs the project default remote profile.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import socket
import socketserver
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from ctfbot.agent.loop import RunLimits
from ctfbot.application.service import LocalChallengeService
from ctfbot.application.remote_profile import REQUIRED_CASES, ReviewedRemoteFactory, approve_remote_profile
from ctfbot.challenge.remote import RemoteSpec
from ctfbot.challenge.remote_fixture import import_remote_fixture, SYNTHETIC_FLAG
from ctfbot.evidence.store import EvidenceStore
from ctfbot.model_adapters.protocol import ToolCall, TurnResult
from ctfbot.reporting import generate_basic_report
from ctfbot.reporting.replay import audit_run
from ctfbot.runtime.remote import CandidateRemoteFactory
from ctfbot.runtime.service_recovery import daemon_identity, read_private, unfinished, write_private
from ctfbot.runtime.supervised import REMOTE_NETWORK_PROFILE
from ctfbot.tools.registry import ToolRegistry


class Endpoint(socketserver.BaseRequestHandler):
    def handle(self):
        chunks = bytearray()
        self.request.settimeout(3)
        while len(chunks) < 20000:
            part = self.request.recv(20000-len(chunks))
            if not part: break
            chunks.extend(part)
        data = bytes(chunks)
        self.server.requests.append(data)
        if data == b'wait':
            self.server.waiting.set()
            self.server.release.wait(3)
            return
        if data == b'solve\n': response = SYNTHETIC_FLAG.encode()
        elif data.startswith(b'GET /redirect '):
            response = b'HTTP/1.1 302 Found\r\nLocation: http://outside.invalid/\r\nContent-Length: 0\r\n\r\n'
        elif data.startswith(b'GET / '):
            response = b'HTTP/1.1 200 OK\r\nContent-Length: 8\r\n\r\nauthored'
        else: response = b'x'*20000
        self.request.sendall(response)


def verify(image: str, output: Path, recovery_acceptance: Path | None = None):
    output.mkdir(mode=0o700)
    records, events, runtimes = {}, [], []
    def sink(details):
        events.append({key: (base64.b64encode(value).decode() if isinstance(value, bytes) else value)
                       for key, value in details.items()})
    def record(name, status, **details):
        records[name] = dict(status=status, evidence=details)
    with socketserver.ThreadingTCPServer(('127.0.0.1', 0), Endpoint) as endpoint:
        endpoint.daemon_threads = True
        endpoint.waiting, endpoint.release = threading.Event(), threading.Event()
        endpoint.requests = []
        thread = threading.Thread(target=endpoint.serve_forever, daemon=True)
        thread.start()
        now = datetime.now(timezone.utc)
        raw = dict(schema_version=1, host='authored.invalid', port=endpoint.server_address[1], protocol='tcp',
                   not_before=(now-timedelta(seconds=1)).isoformat().replace('+00:00','Z'),
                   not_after=(now+timedelta(minutes=10)).isoformat().replace('+00:00','Z'),
                   runtime_authorized=True, authorization_basis='self-owned loopback synthetic acceptance')
        spec = RemoteSpec.from_manifest(raw)
        workspace = import_remote_fixture(output / 'fixture', raw, authorize_model_data=True)
        grant = output / 'controller' / 'grant.json'
        write_private(grant, dict(schema_version=1, remote=raw, pinned_ip='127.0.0.1', solver_image=image,
                                  authorization_basis='explicit local synthetic acceptance'))
        candidate = CandidateRemoteFactory(grant)
        try: CandidateRemoteFactory(grant.parent/'missing.json').validate(spec,image)
        except FileNotFoundError: record('missing_grant','denied')
        else: raise AssertionError('missing grant allowed')
        mismatch={**raw,'host':'127.0.0.2'}
        wrong_grant=grant.parent/'wrong-pin.json'
        write_private(wrong_grant,dict(schema_version=1,remote=mismatch,pinned_ip='127.0.0.1',solver_image=image,authorization_basis='authored mismatch'))
        try: CandidateRemoteFactory(wrong_grant).validate(RemoteSpec.from_manifest(mismatch),image)
        except PermissionError: record('literal_ip_mismatch','denied')
        else: raise AssertionError('literal mismatch allowed')
        expired={**raw,'not_before':(now-timedelta(minutes=2)).isoformat().replace('+00:00','Z'),
                 'not_after':(now-timedelta(minutes=1)).isoformat().replace('+00:00','Z')}
        expired_grant=grant.parent/'expired.json'
        write_private(expired_grant,dict(schema_version=1,remote=expired,pinned_ip='127.0.0.1',solver_image=image,authorization_basis='authored expired scope'))
        try: CandidateRemoteFactory(expired_grant).validate(RemoteSpec.from_manifest(expired),image)
        except PermissionError: record('expired_grant','denied')
        else: raise AssertionError('expired scope allowed')
        def factory(root, selected, requested, selected_sink):
            runtime = candidate(root, selected, requested, selected_sink)
            runtimes.append(runtime)
            return runtime
        factory.validate = candidate.validate
        def fresh():
            runtime = factory(workspace / 'input', image, spec, sink)
            runtime.start()
            return runtime
        def exchange(runtime, data=b'solve\n', **kw):
            return runtime.remote_exchange(spec.host, spec.port, 'tcp', data,
                                           timeout=kw.get('timeout', 1), maximum_bytes=kw.get('maximum_bytes', 1024))
        try:
            runtime = fresh()
            try:
                for name, args in [('wrong_host', ('other.invalid', spec.port, 'tcp')),
                                   ('wrong_port', (spec.host, 1, 'tcp')),
                                   ('wrong_protocol', (spec.host, spec.port, 'udp'))]:
                    try: runtime.remote_exchange(*args, b'', timeout=1, maximum_bytes=100)
                    except PermissionError: record(name, 'denied')
                    else: raise AssertionError(name)
                try: exchange(runtime, b'x'*16385)
                except ValueError: record('input_limit', 'denied')
                else: raise AssertionError('input limit')
                response = exchange(runtime, b'large', maximum_bytes=100)
                assert response['status']=='output_limit' and len(response['data'])==100
                record('output_limit', 'bounded', bytes=len(response['data']))
                with patch.object(socket, 'getaddrinfo', side_effect=AssertionError('connector performed DNS')):
                    assert exchange(runtime)['data'].decode() == SYNTHETIC_FLAG
                record('dns_rebinding', 'pinned', pin='127.0.0.1', dns_lookup=False)
                # Verify shell and PTY paths in the real container have no external route.
                from verify_local_service import PROBE
                with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as discovery:
                    discovery.connect(('192.0.2.1',9))
                    host_ip=discovery.getsockname()[0]
                targets=json.dumps([('loopback','127.0.0.1',spec.port),('host',host_ip,spec.port),
                                    ('public_ipv4','1.1.1.1',443),('public_ipv6','2606:4700:4700::1111',443)])
                probe_argv=['python3','-c',PROBE,targets]
                result = runtime.execute(probe_argv, timeout=5)
                assert result.exit_code == 0 and b'blocked' in result.stdout, result
                record('shell_egress', 'blocked', stdout=result.stdout.decode())
                (output / 'tool-work').mkdir(mode=0o700)
                tools = ToolRegistry(workspace / 'input', output / 'tool-work', EvidenceStore(output / 'tool-evidence'), runtime)
                def tool(name, **args):
                    outcome = tools.invoke(ToolCall(name,args))
                    assert outcome.reply.success, outcome.reply.content
                    return json.loads(outcome.reply.content)
                session = tool('session_start', argv=probe_argv)['session_id']
                transcript = ''
                for _ in range(3):
                    transcript += tool('session_read',session_id=session,wait_seconds=1)['output']
                    if 'blocked' in transcript: break
                tool('session_close',session_id=session)
                assert 'blocked' in transcript
                record('session_egress','blocked',output=transcript)
                before = len(endpoint.requests)
                http = tool('http_request',method='GET',path='/redirect',body_base64='')
                assert http['status_line']=='HTTP/1.1 302 Found' and not http['redirect_followed']
                assert len(endpoint.requests)==before+1
                record('redirect','not_followed',response=http)
                import os
                with patch.dict(os.environ, {'HTTP_PROXY':'http://127.0.0.1:1','ALL_PROXY':'http://127.0.0.1:1'}):
                    http = tool('http_request',method='GET',path='/',body_base64='')
                assert http['body_preview']=='authored'
                record('proxy_environment','ignored',response=http)
                for path in ['//outside.invalid/', '/x\r\nHost: outside.invalid', '/\\outside.invalid']:
                    assert not tools.invoke(ToolCall('http_request',dict(method='GET',path=path,body_base64=''))).reply.success
                record('http_path_scope','denied')
                assert exchange(runtime,b'wait',timeout=.1)['status']=='timed_out'
                assert not runtime._remote_sockets
                record('tool_timeout','closed')
            finally: runtime.close()
            for name in ['run_timeout','scope_expiry','cancel']:
                endpoint.waiting.clear()
                runtime = fresh()
                try:
                    if name=='run_timeout': runtime._run_deadline = time.monotonic()+.1
                    if name=='scope_expiry':
                        old=runtime.remote_spec
                        runtime.remote_spec=RemoteSpec(old.host,old.port,old.protocol,old.not_before,
                                                      datetime.now(timezone.utc)+timedelta(seconds=.1),old.authorization_basis)
                    if name=='cancel':
                        failures=[]
                        def worker():
                            try: exchange(runtime,b'wait',timeout=2)
                            except (RuntimeError,OSError) as exc: failures.append(type(exc).__name__)
                        active=threading.Thread(target=worker)
                        active.start()
                        assert endpoint.waiting.wait(1)
                        runtime.cancel()
                        active.join(2)
                        assert not active.is_alive()
                    else:
                        try: response=exchange(runtime,b'wait',timeout=1)
                        except PermissionError: pass
                        else: assert response['status']=='timed_out'
                    assert not runtime._remote_sockets
                    record(name,'closed')
                finally: runtime.close()
            class Solver:
                def close(self): pass
                def run_turn(self,prompt,tools,on_tool_call,*,timeout):
                    reply=on_tool_call(ToolCall('remote_tcp_exchange',dict(host=spec.host,port=spec.port,protocol='tcp',data_base64=base64.b64encode(b'solve\n').decode())))
                    assert reply.success,reply.content
                    response=json.loads(reply.content)
                    flag=base64.b64decode(response['data_base64']).decode()
                    assert on_tool_call(ToolCall('candidate_submit',dict(candidate=flag))).success
                    assert on_tool_call(ToolCall('run_complete',dict(outcome='candidate_unverified',candidate_id='candidate-1',summary='Synthetic response recorded',unresolved=[]))).success
                    return TurnResult(text='authored TCP solve',tool_calls=2)
            service=LocalChallengeService(model_factory=Solver,model_metadata={'provider':'authored'},remote_runtime_factory=factory)
            result=service.run(workspace,image,output/'runs',RunLimits(max_turns=1,wall_time_seconds=30))
            assert result.status == 'candidate_unverified', result
            run=Path(result.run_dir)
            report=generate_basic_report(run)
            assert SYNTHETIC_FLAG not in report.read_text()
            record('solve','candidate_unverified',run_dir=str(run),audit=audit_run(run))
            def failure(): raise RuntimeError('authored provider startup failure')
            service=LocalChallengeService(model_factory=failure,model_metadata={'provider':'authored-failure'},remote_runtime_factory=factory)
            failed=service.run(workspace,image,output/'failed-runs',RunLimits(max_turns=1,wall_time_seconds=30))
            assert failed.status=='provider_error' and failed.stop_reason=='provider_initialization_error'
            assert json.loads((Path(failed.run_dir)/'run-state.json').read_text())['cleanup_status']=='complete'
            record('provider_failure','cleaned',run_dir=failed.run_dir)
            # The reserved socket has no listener, providing a deterministic refusal.
            with socket.socket() as reserved:
                reserved.bind(('127.0.0.1',0))
                runtime=fresh()
                try:
                    old=runtime.remote_spec
                    runtime.remote_spec=RemoteSpec(old.host,reserved.getsockname()[1],old.protocol,old.not_before,old.not_after,old.authorization_basis)
                    try: runtime.remote_exchange(old.host,runtime.remote_spec.port,'tcp',b'',timeout=1,maximum_bytes=100)
                    except OSError: pass
                    else: raise AssertionError('connection refusal missing')
                    assert not runtime._remote_sockets
                    record('connection_refused','closed')
                finally: runtime.close()
            async def tui_run(selected_service, target):
                from textual.widgets import Input
                from ctfbot.tui.app import CTFBotApp
                app=CTFBotApp(service=selected_service,runtime_image=image,runs_root=target,
                              limits=RunLimits(max_turns=1,wall_time_seconds=30))
                async with app.run_test(size=(120,40)) as pilot:
                    app.query_one('#workspace-path',Input).value=str(workspace)
                    await pilot.click('#preview')
                    await pilot.pause(.1)
                    assert not app.query_one('#run').disabled,app._preview_text
                    await pilot.click('#run')
                    for _ in range(150):
                        await pilot.pause(.1)
                        if app.last_result is not None: break
                    assert app.last_result and app.last_result.status == 'candidate_unverified',app.status_text
                    assert SYNTHETIC_FLAG in '\n'.join(app.displayed_events)
                    await pilot.click('#report')
                    await pilot.pause(.1)
                    assert app.last_report_path and SYNTHETIC_FLAG not in app.last_report_path.read_text()
                    return app.last_result.run_dir
            def entrypoints(selected_factory,label):
                selected_service=LocalChallengeService(model_factory=Solver,model_metadata={'provider':'authored'},remote_runtime_factory=selected_factory)
                from ctfbot.cli.main import main
                with patch('ctfbot.cli.main.create_codex_application_service',return_value=selected_service):
                    assert main(['solve','--workspace',str(workspace),
                                 '--runtime-image',image,'--runs-root',str(output/(label+'-cli')),
                                 '--max-turns','1','--wall-time','30','--confirm-model-usage'])==0
                return asyncio.run(tui_run(selected_service,output/(label+'-tui')))
            tui_dir=entrypoints(factory,'candidate')
            record('tui_solve_entrypoints','passed',tui_run=tui_dir,provider='deterministic injection; actual CLI and Textual pilot')
            regression=subprocess.run([__import__('sys').executable,'-m','pytest','-q','tests/test_stage_a_runner.py',
                                       'tests/test_stage_b_application.py','tests/test_stage_b_control_report.py',
                                       'tests/test_stage_b_tui.py','tests/test_stage_c_service.py','tests/test_stage_c_remote.py'],
                                      capture_output=True,timeout=60)
            assert regression.returncode==0,regression.stdout.decode()+regression.stderr.decode()
            record('stage_b_c_regression','passed',output=regression.stdout.decode())
            if recovery_acceptance is not None:
                recovery=read_private(recovery_acceptance)
                assert recovery['solver_image']==image and recovery['daemon']==daemon_identity() and recovery['resources_removed']
                for name,case in recovery['cases'].items():
                    assert name not in records and case['status']==REQUIRED_CASES[name] and case['resources_removed']
                    record(name,case['status'],receipt=str(recovery_acceptance),case=case)
                assert set(REQUIRED_CASES)<=set(records)
                assert not unfinished(grant.parent/'remote-state')
                for runtime in runtimes:
                    assert subprocess.run(['docker','container','inspect',runtime.container_name],capture_output=True).returncode!=0
                _,grant_hash,_=candidate._load(spec,image)
                boundary=output/'boundary-cases.json'
                write_private(boundary,records)
                acceptance=output/'activation-acceptance.json'
                write_private(acceptance,dict(schema_version=1,result='PASS',network_profile=REMOTE_NETWORK_PROFILE,
                                              daemon=daemon_identity(),solver_image=image,grant_sha256=grant_hash,
                                              cases={name:dict(status=records[name]['status'],resources_removed=True,
                                                               evidence=str(boundary)+'#'+name) for name in REQUIRED_CASES}))
                profile=approve_remote_profile(acceptance,grant,'reviewed authored loopback acceptance; temporary scope only',
                                                destination=grant.parent/'remote-profile.json')
                reviewed=ReviewedRemoteFactory(profile,grant)
                reviewed.validate(spec,image,workspace=workspace,runs_root=output/'reviewed-runs')
                reviewed_tui=entrypoints(reviewed,'reviewed')
                write_private(output/'activation-validation.json',dict(status='passed',profile=str(profile),
                                                                       tui_run=reviewed_tui,default_profile_installed=False))
        finally:
            endpoint.release.set()
            endpoint.shutdown()
            thread.join(2)
            for runtime in runtimes: runtime.close()
    assert not unfinished(grant.parent/'remote-state')
    for runtime in runtimes:
        assert subprocess.run(['docker','container','inspect',runtime.container_name],capture_output=True).returncode!=0
    write_private(output/'events.json',events)
    receipt=output/'acceptance.json'
    final_status='PASS' if not set(REQUIRED_CASES)-set(records) else 'PARTIAL'
    write_private(receipt,dict(schema_version=1,result=final_status,daemon=daemon_identity(),solver_image=image,
                               cases=records,resources_removed=True,real_model_used=False,
                               missing_required_cases=sorted(set(REQUIRED_CASES)-set(records))))
    print(json.dumps({'acceptance':str(receipt),'cases':len(records),'status':final_status}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image',required=True)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--recovery-acceptance',type=Path)
    args=parser.parse_args()
    verify(args.image,args.output,args.recovery_acceptance)
