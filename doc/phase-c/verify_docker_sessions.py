"""Opt-in real Docker C2 acceptance using a local, immutable BusyBox image.

No model, challenge data, image pull, or container networking is used.
Run with PYTHONPATH=src .venv/bin/python doc/phase-c/verify_docker_sessions.py
--image sha256:<reviewed-local-image-id>.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from ctfbot.runtime.docker import DockerRuntime, validate_image_reference


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def read_until(runtime: DockerRuntime, session_id: str, expected: bytes) -> bytes:
    output = bytearray()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        result = runtime.read_interactive(session_id, timeout=0.5, maximum_bytes=16384)
        output.extend(result.data)
        if expected in output:
            return bytes(output)
        require(result.status == "running", f"session ended before expected output: {result.status}")
    raise RuntimeError("interactive output deadline exceeded")


def confirm_removed(name: str) -> None:
    # A daemon error must not be mistaken for successful removal.
    result = subprocess.run(
        ["docker", "container", "ls", "--all", "--filter", f"name=^/{name}$", "--format", "{{.Names}}"],
        capture_output=True, timeout=10, check=True,
    )
    require(not result.stdout.strip(), f"container remained after cleanup: {name}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, type=validate_image_reference)
    args = parser.parse_args()
    image = subprocess.run(
        ["docker", "image", "inspect", args.image], capture_output=True, timeout=10, check=True,
    )
    require(not json.loads(image.stdout)[0]["Config"].get("Volumes"), "image must not declare volumes")
    records = []
    with tempfile.TemporaryDirectory(prefix="ctfbot-c2-smoke-") as scratch:
        root = Path(scratch)
        (root / "TASK.md").write_text("Synthetic interactive acceptance only.\n", encoding="utf-8")
        for case in ("round_trip", "cancel", "idle_timeout", "total_timeout"):
            runtime = DockerRuntime(root, args.image)
            transcript = bytearray()
            closed = []
            session_id = str(uuid.uuid4())
            try:
                runtime.start_interactive(
                    session_id,
                    ["/bin/sh", "-c", "test -t 0 && test -t 1 || exit 9; printf 'READY\\n'; "
                     "while IFS= read -r line; do printf 'REPLY:%s\\n' \"$line\"; done"],
                    on_output=transcript.extend,
                    on_closed=lambda status, code: closed.append((status, code)),
                )
                read_until(runtime, session_id, b"READY")
                if case == "round_trip":
                    for text in ("first", "second"):
                        runtime.send_interactive(session_id, (text + "\n").encode())
                        read_until(runtime, session_id, ("REPLY:" + text).encode())
                    result = runtime.close_interactive(session_id)
                    require(result.status in {"closed", "exited"}, f"unexpected normal close: {result.status}")
                elif case == "cancel":
                    runtime.cancel()
                    result = runtime.read_interactive(session_id, timeout=0, maximum_bytes=16384)
                    require(result.status == "cancelled", f"unexpected cancellation: {result.status}")
                else:
                    # Shorten only this smoke's watchdog, retaining the real PTY,
                    # Docker daemon, callbacks and container-kill path. Production
                    # limits remain 60 seconds idle and 600 seconds total.
                    session = runtime._interactive(session_id)
                    with session._condition:
                        if case == "idle_timeout":
                            session.idle_timeout = 0.5
                        else:
                            session.total_timeout = 0.5
                        session._condition.notify_all()
                    deadline = time.monotonic() + 10
                    # Reading counts as activity, so wait on the condition instead.
                    with session._condition:
                        while not session._finished:
                            remaining = deadline - time.monotonic()
                            require(remaining > 0, "watchdog did not retire the session")
                            session._condition.wait(remaining)
                    result = runtime.read_interactive(session_id, timeout=0, maximum_bytes=16384)
                    require(result.status == case and result.timed_out, f"unexpected watchdog result: {result.status}")
            finally:
                runtime.close()
                confirm_removed(runtime.container_name)
            require(bool(closed), "missing session-close callback")
            require(b"READY" in transcript, "missing output callback transcript")
            records.append({"case": case, "status": result.status, "container": runtime.container_name,
                            "transcript_bytes": len(transcript), "removed": True})
    print(json.dumps({"image": args.image, "cases": records, "result": "PASS"}, indent=2))


if __name__ == "__main__":
    main()
