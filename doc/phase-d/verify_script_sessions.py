"""Real Docker acceptance using authored scripts, never a model/private task."""
from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
import time
from pathlib import Path

from ctfbot.evidence.store import EvidenceStore
from ctfbot.model_adapters.protocol import ToolCall
from ctfbot.runtime.docker import DockerRuntime
from ctfbot.tools.registry import ToolRegistry


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    args.output.chmod(0o700)
    image = subprocess.check_output(
        ["docker", "image", "inspect", "ctfbot-tools:candidate", "--format", "{{.Id}}"], text=True,
    ).strip()
    checks: dict[str, bool] = {}
    with tempfile.TemporaryDirectory(prefix="ctfbot-script-session-") as tmp:
        root = Path(tmp)
        challenge, work = root / "challenge", root / "work"
        challenge.mkdir(); work.mkdir()
        with DockerRuntime(challenge, image) as runtime:
            tools = ToolRegistry(challenge, work, EvidenceStore(args.output / "run"),
                                 runtime, command_timeout=1)
            tools.run_deadline = time.monotonic() + 180

            def call(name, arguments):
                outcome = tools.invoke(ToolCall(name, arguments))
                assert outcome.reply.success, outcome.reply.content
                return json.loads(outcome.reply.content)

            call("script_save", {"path": "long.py", "source": (
                "import time\nfrom pathlib import Path\n"
                "for step in range(25):\n"
                " print(f'progress {step}/25',flush=True)\n"
                " Path('/work/checkpoint').write_text(str(step))\n"
                " time.sleep(5)\nprint('finished',flush=True)\n"
            )})
            start = time.monotonic()
            long_id = call("script_start", {"path": "long.py", "argv": []})["session_id"]
            checks["returns_immediately"] = time.monotonic() - start < 2
            call("script_save", {"path": "quiet.py", "source": (
                "import time,os\nfrom pathlib import Path\n"
                "if os.fork()==0:\n"
                " os.setsid();Path('/work/quiet-child.pid').write_text(str(os.getpid()));time.sleep(150)\n"
                "else:\n print('ready',flush=True);time.sleep(150)\n"
            )})
            quiet_id = call("script_start", {"path": "quiet.py", "argv": []})["session_id"]
            quiet_closed = False
            output, max_elapsed, silence = "", 0.0, 0.0
            while True:
                result = call("session_read", {"session_id": long_id, "wait_seconds": 10})
                output += result["output"]
                max_elapsed = max(max_elapsed, result["elapsed_seconds"])
                if not quiet_closed:
                    quiet = call("session_read", {"session_id": quiet_id, "wait_seconds": 0})
                    silence = quiet["output_idle_seconds"]
                    assert quiet["status"] == "running"
                    if silence > 61:
                        checks["silence_over_60s_keeps_running"] = not runtime.closed
                        stopped = call("session_close", {"session_id": quiet_id})
                        checks["model_requested_stop"] = stopped["exit_code"] == 130
                        checks["stop_preserves_other_session"] = not runtime.closed
                        quiet_closed = True
                print(json.dumps({"elapsed": max_elapsed, "status": result["status"],
                                  "quiet_output_idle": silence, "quiet_stopped": quiet_closed}), flush=True)
                if result["status"] != "running":
                    break
                assert time.monotonic() - start < 170
            checks["completed_beyond_120s"] = max_elapsed > 120 and result["exit_code"] == 0 and "finished" in output
            checks["progress_visible"] = "progress 24/25" in output
            checks["sandbox_preserved"] = not runtime.closed
            check = runtime.execute(["python3", "-c", (
                "from pathlib import Path;import os;"
                "assert Path('/work/checkpoint').read_text()=='24';"
                "pid=int(Path('/work/quiet-child.pid').read_text());"
                "assert not Path('/proc/'+str(pid)).exists();print('preserved')"
            )], timeout=2)
            checks["checkpoint_preserved_and_descendant_reaped"] = check.exit_code == 0 and check.stdout == b"preserved\n"
            call("session_close", {"session_id": long_id})
            name = runtime.container_name
        checks["container_removed"] = subprocess.run(["docker", "inspect", name], capture_output=True).returncode != 0
    receipt = {"passed": all(checks.values()), "image": image, "checks": checks,
               "elapsed_seconds": max_elapsed, "output_idle_seconds": silence,
               "real_model": False, "private_challenge": False}
    file = args.output / "acceptance.json"
    file.write_text(json.dumps(receipt, indent=2) + "\n")
    file.chmod(0o600)
    print(json.dumps(receipt, indent=2), flush=True)
    assert receipt["passed"]


if __name__ == "__main__":
    main()
