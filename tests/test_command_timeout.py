from __future__ import annotations

import json
import subprocess
import sys
import time
import pytest
from pathlib import Path
from unittest.mock import patch

from ctfbot.agent.loop import AgentLoop
from ctfbot.evidence.store import EvidenceStore
from ctfbot.model_adapters.protocol import FakeModelSession, ScriptedTurn, ToolCall
from ctfbot.runtime.docker import DockerRuntime
from ctfbot.runtime.docker import RuntimeErrorSafe
from ctfbot.runtime.sessions import InteractiveReadResult
from ctfbot.tools.registry import CommandResult, ToolRegistry


IMAGE = "sha256:" + "a" * 64


def test_command_timeout_preserves_work_and_cleans_detached_descendants(tmp_path: Path):
    runtime = DockerRuntime(tmp_path, IMAGE)
    runtime._started = True
    popen = subprocess.Popen

    def local_exec(command, **kwargs):
        return popen(command[5:], cwd=tmp_path, **kwargs)

    script = (
        "import os,time,pathlib; pathlib.Path('checkpoint').write_text('saved'); "
        "pid=os.fork(); "
        "\nif pid == 0:\n os.setsid(); time.sleep(1); pathlib.Path('escaped').write_text('bad')"
        "\nelse:\n print('progress 1/10',flush=True); time.sleep(10)"
    )
    with patch("ctfbot.runtime.docker.subprocess.Popen", side_effect=local_exec):
        result = runtime.execute([sys.executable, "-c", script], timeout=.2)
        assert result.timed_out and result.exit_code == 124
        assert result.stdout == b"progress 1/10\n"
        assert result.stderr == b""
        assert not runtime.closed
        followup = runtime.execute([sys.executable, "-c", "from pathlib import Path; print(Path('checkpoint').read_text())"], timeout=2)
        assert followup.exit_code == 0 and followup.stdout == b"saved\n"
        assert not followup.timed_out
        time.sleep(1)
        assert not (tmp_path / "escaped").exists()


def test_output_does_not_extend_deadline_and_truncation_keeps_timeout_metadata(tmp_path: Path):
    runtime = DockerRuntime(tmp_path, IMAGE)
    runtime._started = True
    popen = subprocess.Popen
    def local_exec(command, **kwargs):
        return popen(command[5:], cwd=tmp_path, **kwargs)
    with patch("ctfbot.runtime.docker.subprocess.Popen", side_effect=local_exec):
        started = time.monotonic()
        result = runtime.execute([sys.executable, "-u", "-c",
            "import sys,time; sys.stderr.write('x'*1100000); sys.stderr.flush(); "
            "\nwhile True: print('progress',flush=True); time.sleep(.02)"], timeout=.2)
        assert result.timed_out and result.truncated
        assert time.monotonic() - started < 3
        assert not runtime.closed
        explicit_exit = runtime.execute([sys.executable, "-c", "raise SystemExit(124)"], timeout=1)
        assert explicit_exit.exit_code == 124 and not explicit_exit.timed_out


def test_lost_sandbox_ends_run_without_more_tools_or_provider_error(tmp_path: Path):
    challenge, work = tmp_path / "challenge", tmp_path / "work"
    challenge.mkdir(); work.mkdir()
    class LostRuntime:
        closed = False
        calls = 0
        def execute(self, argv, *, timeout):
            self.calls += 1
            self.closed = True
            return CommandResult(124, b"", b"", timed_out=True)
    class Model(FakeModelSession):
        cancelled = False
        def cancel(self):
            self.cancelled = True
    model = Model([ScriptedTurn(tool_calls=(
        ToolCall("command_run", {"argv": ["sleep", "100"]}),
        ToolCall("command_run", {"argv": ["echo", "must not run"]}),
    ))])
    runtime = LostRuntime()
    evidence = EvidenceStore(tmp_path / "run")
    tools = ToolRegistry(challenge, work, evidence, runtime)
    loop = AgentLoop(model, tools, evidence)
    prompt = loop._initial_prompt("synthetic task")
    assert "flush=True" in prompt and "checkpoints" in prompt
    assert "Progress does not extend the deadline" in prompt
    result = loop.run("synthetic task")
    assert result.status == "error" and result.stop_reason == "sandbox_closed"
    assert runtime.calls == 1 and result.tool_calls == 1
    assert model.cancelled and model.closed
    events = [json.loads(line) for line in (evidence.run_dir / "events.jsonl").read_text().splitlines()]
    assert any(e["event_type"] == "sandbox_error" for e in events)
    assert not any(e["event_type"] == "provider_error" for e in events)


def test_recoverable_timeout_allows_model_to_continue(tmp_path: Path):
    challenge, work = tmp_path / "challenge", tmp_path / "work"
    challenge.mkdir(); work.mkdir()
    class Runtime:
        closed = False
        calls = 0
        def execute(self, argv, *, timeout):
            self.calls += 1
            if self.calls == 1:
                return CommandResult(124, b"checkpoint saved", b"", timed_out=True)
            return CommandResult(0, b"resumed batch", b"")
    runtime = Runtime()
    model = FakeModelSession([ScriptedTurn(tool_calls=(
        ToolCall("command_run", {"argv": ["batch1"]}),
        ToolCall("command_run", {"argv": ["batch2"]}),
    ))])
    evidence = EvidenceStore(tmp_path / "run")
    result = AgentLoop(model, ToolRegistry(challenge, work, evidence, runtime), evidence).run("synthetic")
    assert runtime.calls == 2 and result.status == "unverified"
    events = [json.loads(line) for line in (evidence.run_dir / "events.jsonl").read_text().splitlines()]
    assert [e['result']['status'] for e in events if e['event_type'] == 'tool_result'] == ['timeout', 'ok']
    assert not any(e['event_type'] == 'sandbox_error' for e in events)


def test_supervisor_failure_closes_sandbox(tmp_path: Path):
    runtime = DockerRuntime(tmp_path, IMAGE)
    runtime._started = True
    popen = subprocess.Popen
    def local_exec(command, **kwargs):
        return popen(command[5:], cwd=tmp_path, **kwargs)
    def kill_container():
        runtime._closed = True
    with patch("ctfbot.runtime.docker.subprocess.Popen", side_effect=local_exec), patch.object(
        runtime, "_kill_container", side_effect=kill_container
    ), patch("ctfbot.runtime.docker.SUPERVISOR", "raise SystemExit(1)"):
        with pytest.raises(RuntimeErrorSafe, match="did not confirm completion"):
            runtime.execute([sys.executable, "-c", "print('must not run')"], timeout=1)
        assert runtime.closed


def test_background_script_tool_reports_silence_and_uses_remaining_budget(tmp_path: Path):
    challenge, work = tmp_path / "challenge", tmp_path / "work"
    challenge.mkdir(); work.mkdir()
    class Runtime:
        closed = False
        def execute(self, argv, *, timeout):
            return CommandResult(0, b"", b"")
        def start_interactive(self, *args, **kwargs):
            raise AssertionError("long script must use the background path")
        def start_script_session(self, session_id, argv, **kwargs):
            self.argv, self.options = argv, kwargs
        def send_interactive(self, *args): pass
        def read_interactive(self, *args, **kwargs):
            return InteractiveReadResult(b"progress", "running", None,
                                         elapsed_seconds=140, output_idle_seconds=12)
        def close_interactive(self, *args):
            return InteractiveReadResult(b"", "closed", 130)
    runtime = Runtime()
    tools = ToolRegistry(challenge, work, EvidenceStore(tmp_path / "run"), runtime)
    tools.run_deadline = time.monotonic() + 150
    saved = tools.invoke(ToolCall("script_save", {"path": "solve.py", "source": "print('progress',flush=True)"}))
    assert saved.reply.success
    started = tools.invoke(ToolCall("script_start", {"path": "solve.py", "argv": []}))
    assert started.reply.success
    assert 145 < runtime.options['total_timeout'] <= 150
    assert runtime.argv[:3] == ['python3', '-u', '-c']
    assert 'hashlib.sha256(data)' in runtime.argv[3]
    sid = json.loads(started.reply.content)['session_id']
    read = tools.invoke(ToolCall('session_read', {'session_id': sid, 'wait_seconds': 0}))
    payload = json.loads(read.reply.content)
    assert payload['elapsed_seconds'] == 140 and payload['output_idle_seconds'] == 12
    assert read.result['output_idle_seconds'] == 12
    assert tools.invoke(ToolCall('session_close', {'session_id': sid})).reply.success


def test_coalesced_session_read_combines_heartbeats_and_terminal_state(tmp_path):
    challenge, work = tmp_path/'challenge', tmp_path/'work'
    challenge.mkdir(); work.mkdir()
    class Runtime:
        closed = False
        def start_interactive(self, *args, **kwargs): pass
        def send_interactive(self, *args, **kwargs): pass
        def close_interactive(self, *args, **kwargs):
            return InteractiveReadResult(b'', 'closed', 0)
        def read_interactive(self, *args, **kwargs):
            return next(self.results)
    runtime = Runtime()
    runtime.results = iter([
        InteractiveReadResult(b'progress 1\n', 'running', None, elapsed_seconds=10, output_idle_seconds=0),
        InteractiveReadResult(b'progress 2\n', 'running', None, elapsed_seconds=30, output_idle_seconds=0),
        InteractiveReadResult(b'done\n', 'exited', 0, elapsed_seconds=40, output_idle_seconds=1),
    ])
    tools = ToolRegistry(challenge, work, EvidenceStore(tmp_path/'run'), runtime)
    started = tools.invoke(ToolCall('session_start', {'argv':['authored']}))
    assert started.reply.success
    sid = json.loads(started.reply.content)['session_id']
    read = tools.invoke(ToolCall('session_read', {'session_id':sid,'wait_seconds':0,'collect_seconds':60}))
    reply = json.loads(read.reply.content)
    assert reply['output']=='progress 1\nprogress 2\ndone\n'
    assert reply['execution_state']=='exited' and reply['output_idle_seconds']==1
    assert read.result['bytes']==len(reply['output'])
    assert not tools.invoke(ToolCall('session_read',{'session_id':sid,'collect_seconds':61})).reply.success
