"""Actual TUI/Docker synthetic acceptance without a known-answer oracle or LLM."""
from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
from pathlib import Path

from textual.widgets import Input, Checkbox

from ctfbot.agent.loop import RunLimits
from ctfbot.application.runtime_images import resolve_runtime_image
from ctfbot.application.service import LocalChallengeService
from ctfbot.benchmark.evaluation import load_dataset
from ctfbot.challenge.domain_fixtures import create_domain_dataset
from ctfbot.reporting.replay import audit_run, replay_run
from ctfbot.runtime.service_recovery import daemon_identity, unfinished, write_private
from ctfbot.runtime.supervised import SupervisedOfflineRuntime
from ctfbot.tui.app import CTFBotApp
from verify_workflows import AuthoredProvider


async def verify(output: Path, single_file: bool = False):
    output.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    output.mkdir(mode=0o700)
    choice=resolve_runtime_image()
    assert choice.image,choice.message
    dataset=create_domain_dataset(output/'authored',authorize_model_data=True)
    category='reverse' if single_file else 'crypto'
    case=next(case for case in load_dataset(dataset)['cases'] if case['category']==category)
    # Remove the unused answer and turn this snapshot into an exploration case.
    # The dataset manifest is not used for evaluation after removing its oracle.
    case['oracle'].unlink()
    identity=daemon_identity()
    runtimes=[]
    def factory(root,image):
        runtime=SupervisedOfflineRuntime(root,image,recovery_root=output/'runtime-state',
                                         expected_daemon=identity,event_sink=lambda _:None)
        runtimes.append(runtime)
        return runtime
    service=LocalChallengeService(model_factory=lambda:AuthoredProvider(category),
                                  model_metadata={'provider':'authored-deterministic'},runtime_factory=factory)
    app=CTFBotApp(service=service,runs_root=output/'runs',imports_root=output/'imports',additional_prompt='Flag 前缀为 CTFBOT_SYNTHETIC',
                  limits=RunLimits(max_turns=1))
    async with app.run_test(size=(120,40)) as pilot:
        app.query_one('#workspace-path',Input).value=str(case['workspace']/'input'/'program.elf' if single_file else case['workspace'])
        app.query_one('#file-model-transfer',Checkbox).value=single_file
        for _ in range(50):
            await pilot.pause(.1)
            if app.query_one('#runtime-image',Input).value: break
        assert app.query_one('#runtime-image',Input).value==choice.image
        assert not app.query_one('#oracle-path',Input).value
        await pilot.click('#preview')
        await pilot.pause(.1)
        assert app._preview and app._preview.start_allowed,app.status_text
        prepared_workspace=Path(app._preview.workspace)
        await pilot.click('#run')
        for _ in range(150):
            await pilot.pause(.1)
            if app.last_result: break
        result=app.last_result
        assert result and result.status=='candidate_unverified' and not result.verified,app.status_text
        assert any('CTFBOT_SYNTHETIC{' in line for line in app.displayed_events)
        await pilot.click('#report')
        await pilot.pause(.1)
        assert app.last_report_path and f'CTFBOT_SYNTHETIC{{{category}-' not in app.last_report_path.read_text()
    run=Path(result.run_dir)
    replay=replay_run(run_dir=run,workspace=prepared_workspace,output=output/'replays',runtime_factory=factory)
    assert json.loads(replay.read_text())['status']=='matched'
    assert not unfinished(output/'runtime-state')
    for runtime in runtimes:
        assert subprocess.run(['docker','container','inspect',runtime.container_name],capture_output=True).returncode!=0
    receipt=output/'acceptance.json'
    write_private(receipt,dict(status='passed',runtime_image=choice.image,automatic_image=True,
                               single_file=single_file,workspace=str(prepared_workspace),
                               oracle_provided=False,additional_prompt='Flag 前缀为 CTFBOT_SYNTHETIC',
                               result_status=result.status,verified=result.verified,run_dir=str(run),
                               audit=audit_run(run),replay=str(replay),resources_removed=True,real_model_used=False))
    print(receipt)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--single-file',action='store_true',help='Import and solve only the authored reverse executable through TUI')
    args=parser.parse_args()
    asyncio.run(verify(args.output,args.single_file))
