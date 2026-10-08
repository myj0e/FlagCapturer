"""Explicit local Docker acceptance using authored data and a deterministic provider.

Run with .venv/bin/python doc/phase-d/verify_workflows.py --image sha256:... --output /tmp/unique-dir
No real model, downloaded challenge or oracle access in the provider.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

from ctfbot.agent.loop import RunLimits
from ctfbot.application.service import LocalChallengeService
from ctfbot.application.memory import MemoryView, propose, review, revoke
from ctfbot.benchmark.evaluation import audit_separation, load_dataset, run_evaluation
from ctfbot.challenge.domain_fixtures import create_domain_dataset
from ctfbot.model_adapters.protocol import ToolCall, TurnResult
from ctfbot.reporting import generate_basic_report
from ctfbot.reporting.replay import audit_run, replay_run
from ctfbot.runtime.service_recovery import daemon_identity, unfinished, write_private
from ctfbot.runtime.supervised import SupervisedOfflineRuntime


OPERATIONS = {
    "crypto": ("decode", "cipher.b64"), "forensics": ("archive", "evidence.zip"),
    "stego": ("ppm_lsb", "carrier.ppm"), "reverse": ("elf", "program.elf"),
    "pwn": ("elf", "program.elf"), "web": ("response", "response.http"),
}
SCRIPTS = {
    "crypto": "import base64\nfrom pathlib import Path\nprint(base64.b64decode(Path('/challenge/cipher.b64').read_bytes()).decode())",
    "forensics": "import zipfile\nfrom pathlib import Path\nwith zipfile.ZipFile('/challenge/evidence.zip') as z:\n data=z.read('notes/message.txt')\n assert len(data)<16384\n Path('/work/extracted.txt').write_bytes(data)\n print(data.decode())",
    "stego": "from pathlib import Path\np=Path('/challenge/carrier.ppm').read_bytes().split(b'\\n',3)[3]\nbits=''.join(str(v&1) for v in p)\nn=int(bits[:32],2)\nassert n<16384\nprint(bytes(int(bits[i:i+8],2) for i in range(32,32+8*n,8)).decode())",
    "reverse": "import subprocess\nr=subprocess.run(['/challenge/program.elf'],input=b'unlock\\n',capture_output=True,timeout=3,check=True)\nprint(r.stdout.decode(),end='')",
    "pwn": "import subprocess\nr=subprocess.run(['/challenge/program.elf'],input=b'A'*16+b'DCBA',capture_output=True,timeout=3,check=True)\nprint(r.stdout.decode(),end='')",
    "web": "import base64,re\nfrom pathlib import Path\np=Path('/challenge/response.http').read_bytes()\nprint(base64.b64decode(re.search(rb'token:([A-Za-z0-9+/=]+)',p)[1]).decode())",
}


class AuthoredProvider:
    def __init__(self, category):
        self.category = category

    def close(self):
        pass

    def run_turn(self, prompt, tools, on_tool_call, *, timeout):
        count = 0
        def call(name, **args):
            nonlocal count
            count += 1
            reply = on_tool_call(ToolCall(name, args))
            if not reply.success:
                raise RuntimeError(f"{name}: {reply.content}")
            return json.loads(reply.content)
        category = self.category
        if any(tool.name == "memory_search" for tool in tools):
            matches = call("memory_search", query="bounded")["matches"]
            assert len(matches) == 1
            entry = call("memory_read", id=matches[0]["id"])["entry"]
            assert entry["contamination"] == "generic" and entry["namespace"] == "manuals"
        operation, path = OPERATIONS[category]
        call("workflow_discover")
        call("workflow_read", pack=category)
        call("workflow_environment", pack=category)
        call("workflow_run", pack=category, operation=operation, path=path)
        call("script_save", path="solve.py", source=SCRIPTS[category])
        output = call("script_run", path="solve.py", argv=[])
        if category == "forensics":
            call("artifact_export", path="extracted.txt", parents=[path])
        if category in {"reverse", "pwn"}:
            session = call("session_start", argv=["/challenge/program.elf"])["session_id"]
            call("session_send", session_id=session, input="unlock\n" if category == "reverse" else "A"*16+"DCBA\n")
            transcript = ""
            for _ in range(3):
                transcript += call("session_read", session_id=session, wait_seconds=1)["output"]
                if "CTFBOT_SYNTHETIC{" in transcript:
                    break
            call("session_close", session_id=session)
            assert "CTFBOT_SYNTHETIC{" in transcript, "PTY probe produced no candidate"
        candidate = re.search(r"CTFBOT_SYNTHETIC\{[^}\r\n]+\}", output["stdout"])
        if not candidate:
            raise RuntimeError("authored solver produced no candidate")
        recorded = call("candidate_submit", candidate=candidate.group())
        if recorded.get('status') != 'verified':
            call('run_complete', outcome='candidate_unverified', candidate_id=recorded['candidate_id'],
                 summary='Authored solver produced an unverified candidate', unresolved=['No trusted oracle supplied'])
        return TurnResult(text="authored deterministic workflow", tool_calls=count)


def verify(image: str, output: Path):
    output.mkdir(mode=0o700)
    development = create_domain_dataset(output / "development", authorize_model_data=True)
    holdout = create_domain_dataset(output / "holdout", authorize_model_data=True, split="holdout")
    identity = daemon_identity()
    note=output/'authored-manual.md'
    note.write_text('Use bounded local experiments and retain evidence hashes.\n')
    memory=output/'memory'
    proposal=propose(memory,note,namespace='manuals',source_url='repo:authored-acceptance-note',license='MIT',tags=['bounded'])
    version=review(memory,proposal,reviewer='authored synthetic acceptance',basis='fixed generic note above; no challenge constants or answers',
                   decision='approve',contamination='generic')
    runtimes = []
    def factory(root, selected_image):
        runtime = SupervisedOfflineRuntime(root, selected_image, recovery_root=output / "runtime-state",
                                          expected_daemon=identity, event_sink=lambda details: None)
        runtimes.append(runtime)
        return runtime
    records = []
    cases = load_dataset(development)["cases"]
    limits = RunLimits(max_turns=1, max_tool_calls=16, wall_time_seconds=90)
    for case in cases:
        service = LocalChallengeService(model_factory=lambda c=case["category"]: AuthoredProvider(c),
                                        model_metadata={"provider": "authored-deterministic"}, runtime_factory=factory,
                                        memory_root=memory,memory_namespaces=('manuals',))
        result = service.run(case["workspace"], case["oracle"], image, output / "runs", limits)
        if not result.verified:
            raise RuntimeError(f"{case['category']} not verified: {result}")
        run = Path(result.run_dir)
        report = generate_basic_report(run)
        assert "CTFBOT_SYNTHETIC{" not in report.read_text()
        audit = audit_run(run)
        replay = replay_run(run_dir=run, workspace=case["workspace"], oracle=case["oracle"],
                            output=output / "replays", runtime_factory=factory)
        replay_status = json.loads(replay.read_text())["status"]
        assert replay_status == "matched", replay.read_text()
        records.append({"category": case["category"], "verified": True, "run_dir": str(run),
                        "audit": audit, "replay": str(replay), "replay_status": replay_status})
    queue = iter(case["category"] for case in load_dataset(holdout)["cases"])
    service = LocalChallengeService(model_factory=lambda: AuthoredProvider(next(queue)),
                                    model_metadata={"provider": "authored-deterministic"}, runtime_factory=factory,
                                    memory_root=memory,memory_namespaces=('manuals',))
    summary = run_evaluation(dataset_path=holdout, development_reference=development, service=service,
                             runtime_image=image, output=output / "evaluation", limits=limits,
                             max_total_turns=6, max_total_tool_calls=96, max_total_wall_seconds=600)
    assert json.loads(summary.read_text())["aggregate"]["verified_over_scheduled"] == [6, 6]
    assert not unfinished(output / "runtime-state")
    frozen=MemoryView(memory,namespaces=('manuals',))
    revoke(memory,version,basis='synthetic acceptance revocation after completed runs')
    assert version in frozen.entries and not MemoryView(memory,namespaces=('manuals',)).entries
    for runtime in runtimes:
        result = subprocess.run(["docker", "container", "inspect", runtime.container_name], capture_output=True)
        assert result.returncode != 0, runtime.container_name
    receipt = output / "acceptance.json"
    write_private(receipt, {"schema_version": 1, "status": "passed", "solver_image": image,
                           "daemon": identity, "cases": records, "holdout_summary": str(summary),
                           "separation": audit_separation(development, holdout),
                           "memory":dict(approved_version=version,namespace='manuals',read_in_all_runs=True,
                                          revoked_for_future_runs=True,frozen_snapshot_preserved=True),
                           "resources_removed": True, "real_model_used": False,
                           "limitations": ["one authored mechanism per category", "byte separation only",
                                           "offline Web response; C4 acceptance separate",
                                           "command replay only; no interactive session replay"]})
    print(json.dumps({"acceptance": str(receipt), "verified": len(records), "holdout_verified": 6}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    verify(args.image, args.output)
