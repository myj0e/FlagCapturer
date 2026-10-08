"""Independent acknowledgement worker for a single controller Docker mutation.

The caller may time out without killing this worker or its Docker client. The
durable result distinguishes acknowledged completion from lost communication.
"""

from __future__ import annotations

import argparse
import subprocess
import time
from pathlib import Path

from ctfbot.runtime.service_recovery import read_private, recover, write_private


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request", type=Path)
    args = parser.parse_args()
    record = read_private(args.request)
    argv = record["argv"]
    if (not isinstance(argv, list) or argv[0] != "docker" or
            not (argv[1] in {"create", "start"} or argv[1:3] == ["network", "create"])):
        raise ValueError("unsupported supervised Docker request")
    try:
        # No client timeout: wait for the daemon's acknowledgement independently
        # of the foreground run deadline. Output is only Docker IDs/status/errors.
        result = subprocess.run(argv, capture_output=True)
        record.update(returncode=result.returncode,
                      stdout=result.stdout[-4096:].decode("utf-8", errors="replace"),
                      stderr=result.stderr[-4096:].decode("utf-8", errors="replace"))
        acknowledged = result.returncode == 0 or b"Error response from daemon:" in result.stderr
        record["status"] = "acknowledged" if acknowledged else "uncertain"
    except OSError as exc:
        # Popen failed before a Docker client was started.
        record.update(status="acknowledged", returncode=125, stdout="", stderr=type(exc).__name__)
    write_private(args.request, record)
    state = read_private(args.request.parent / "state.json")
    if record["status"] == "acknowledged" and state.get("recovery_required"):
        # Wait briefly for foreground teardown to release its admission lock.
        # A later `ctfbot service recover` handles a slow/stuck caller.
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                recover(args.request.parent.parent)
                break
            except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
                time.sleep(0.1)


if __name__ == "__main__":
    main()
