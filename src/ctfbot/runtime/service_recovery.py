"""Private durable ownership and acknowledgement records for local services."""

from __future__ import annotations

import fcntl
import json
import os
import stat
import subprocess
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ctfbot.evidence.store import require_private_regular_file
from ctfbot.runtime.docker import RuntimeErrorSafe


NETWORK_PROFILE = "isolated-ipv4-no-upstream-dns-v1"


def private_directory(path: Path) -> Path:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink() or path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o077:
        raise ValueError("service state directory must be owned by the controller with mode 0700")
    return path.resolve(strict=True)


def read_private(path: Path) -> dict[str, Any]:
    checked = require_private_regular_file(path)
    if checked.stat().st_uid != os.getuid():
        raise ValueError("service configuration must be owned by the controller")
    value = json.loads(checked.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("service state must be a JSON object")
    return value


def write_private(path: Path, value: dict[str, Any]) -> None:
    private_directory(path.parent)
    fd, temporary = tempfile.mkstemp(prefix=".service-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextmanager
def state_lock(root: Path, *, wait: float = 0):
    root = private_directory(root)
    fd = os.open(root / "admission.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    deadline = time.monotonic() + wait
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise RuntimeErrorSafe("another local service run or recovery is active")
                time.sleep(0.05)
        yield
    finally:
        os.close(fd)


def _daemon_identity() -> dict[str, Any]:
    context = json.loads(subprocess.run(["docker", "context", "inspect"], capture_output=True,
                                        timeout=5, check=True).stdout)[0]
    endpoint = (os.environ.get("DOCKER_HOST") if not os.environ.get("DOCKER_CONTEXT") else None)
    endpoint = endpoint or context["Endpoints"]["docker"]["Host"]
    if not endpoint.startswith("unix:///"):
        raise ValueError("local service profiles require a local Unix Docker socket")
    socket_path = Path(endpoint.removeprefix("unix://"))
    info = socket_path.stat()
    if not stat.S_ISSOCK(info.st_mode):
        raise ValueError("Docker endpoint must be a Unix socket")
    details = json.loads(subprocess.run(["docker", "info", "--format", "{{json .}}"],
                                        capture_output=True, timeout=5, check=True).stdout)
    if details.get("OSType") != "linux" or not details.get("ID"):
        raise ValueError("local service profiles require an identifiable Linux Docker daemon")
    return {"daemon_id": details["ID"], "version": details["ServerVersion"],
            "kernel": details["KernelVersion"], "os": details["OSType"],
            "socket": str(socket_path.resolve()), "socket_device": info.st_dev,
            "socket_inode": info.st_ino,
            "host_boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip()}


def daemon_identity() -> dict[str, Any]:
    try:
        return _daemon_identity()
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, IndexError) as exc:
        raise RuntimeErrorSafe("could not confirm the local Docker daemon identity") from exc


def unfinished(root: Path) -> list[Path]:
    private_directory(root)
    return [path for path in sorted(root.glob("*/state.json"))
            if read_private(path).get("cleanup_complete") is not True]


def requests_settled(owner_dir: Path) -> bool:
    return all(read_private(path).get("status") == "acknowledged"
               for path in owner_dir.glob("request-*.json"))


def process_start_ticks(pid: int) -> str | None:
    """Disambiguate a live Linux process from a reused PID."""
    if type(pid) is not int or pid <= 0:
        raise ValueError("invalid controller PID")
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
    except FileNotFoundError:
        return None


def remove_owned(state: dict[str, Any]) -> None:
    owner = state.get("owner")
    if not isinstance(owner, str) or str(uuid.UUID(owner)) != owner:
        raise ValueError("invalid recovery owner")
    resources = state.get("resources")
    offline = (state.get("schema_version") == 2 and state.get("runtime_profile") == "offline-remote-tcp-v1"
               and resources == [["container", f"ctfbot-{owner}"]])
    if not offline and (not isinstance(resources, list) or len(resources) != 3
            or any(not isinstance(entry, list) or len(entry) != 2
                   or any(not isinstance(value, str) for value in entry) for entry in resources)
            or [entry[0] for entry in resources] != ["container", "container", "network"]
            or resources[2][1] != f"ctfbot-net-{owner}"):
        raise ValueError("invalid recovery resource set")
    for kind, name in resources:
        if kind == "container" and (not name.startswith("ctfbot-") or
                str(uuid.UUID(name.removeprefix("ctfbot-"))) != name.removeprefix("ctfbot-")):
            raise ValueError("invalid recovery container name")
        template = "{{json .Labels}}" if kind == "network" else "{{json .Config.Labels}}"
        inspected = subprocess.run(["docker", kind, "inspect", "--format", template, name],
                                   capture_output=True, timeout=5)
        if inspected.returncode:
            message = inspected.stderr.decode(errors="replace").lower()
            if any(value in message for value in (f"no such container: {name}", f"no such object: {name}",
                                                   f"no such network: {name}", f"network {name} not found")):
                continue
            raise RuntimeErrorSafe("could not confirm Docker resource absence")
        labels = json.loads(inspected.stdout)
        if not isinstance(labels, dict) or labels.get("ctfbot.owner") != owner:
            raise RuntimeErrorSafe("refusing recovery of a resource owned by another run")
        subprocess.run(["docker", kind, "rm", *(["-f"] if kind == "container" else []), name],
                       capture_output=True, timeout=10, check=True)
        listed = subprocess.run(["docker", kind, "ls", *(["--all"] if kind == "container" else []),
                                 "--filter", f"name=^{'/' if kind == 'container' else ''}{name}$",
                                 "--format", "{{.Name}}" if kind == "network" else "{{.Names}}"],
                                capture_output=True, timeout=5, check=True)
        if listed.stdout.strip():
            raise RuntimeErrorSafe("resource remained after recovery")


def recover(root: Path) -> list[dict[str, Any]]:
    results = []
    with state_lock(root):
        identity = daemon_identity()
        for path in unfinished(root):
            state = read_private(path)
            recorded = state.get("daemon", {})
            if any(recorded.get(key) != identity.get(key) for key in ("daemon_id", "socket", "os")):
                raise RuntimeErrorSafe("recovery requires the recorded Docker daemon and host identity")
            # Absence cannot clear unfinished creation intent. Existing owned
            # resources can still be removed while acknowledgement is pending.
            remove_owned(state)
            if not requests_settled(path.parent):
                results.append({"owner": state["owner"], "status": "pending_or_uncertain"})
                continue
            if state.get("controller_connections_pending") is True:
                if (recorded.get("host_boot_id") == identity.get("host_boot_id")
                        and process_start_ticks(state.get("controller_pid")) == state.get("controller_start_ticks")):
                    results.append({"owner": state["owner"], "status": "controller_connections_pending"})
                    continue
                # Process exit/host reboot closes its controller-owned sockets.
                state["controller_connections_pending"] = False
            state["cleanup_complete"] = True
            state["recovery_required"] = False
            write_private(path, state)
            results.append({"owner": state["owner"], "status": "cleaned"})
    return results
