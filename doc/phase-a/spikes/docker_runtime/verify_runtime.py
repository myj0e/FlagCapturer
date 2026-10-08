"""Run benign Docker checks using a local static BusyBox image."""

from __future__ import annotations

import os
import pty
import select
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
IMAGE = f"ctfbot-stage-a-runtime:{uuid.uuid4().hex[:10]}"


def docker(args: list[str], *, timeout: float = 30, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)
    if check and result.returncode:
        raise RuntimeError(f"docker {args[:3]} failed: {result.stderr[-1000:]}")
    return result


def pty_round_trip(uid: int, gid: int) -> str:
    master, slave = pty.openpty()
    process = subprocess.Popen(
        ["docker", "run", "--rm", "--pull=never", "--network", "none", "--read-only",
         "--user", f"{uid}:{gid}", "-e", "TERM=dumb", "-i", "-t", IMAGE,
         "sh", "-c", "read value; echo PTY=$value"],
        stdin=slave, stdout=slave, stderr=slave, close_fds=True,
    )
    os.close(slave)
    output = bytearray()
    try:
        time.sleep(0.2)
        os.write(master, b"stage-a\n")
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            readable, _, _ = select.select([master], [], [], 0.1)
            if readable:
                try:
                    output.extend(os.read(master, 4096))
                except OSError:
                    break
            if process.poll() is not None:
                break
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)
            rendered = output.decode("utf-8", errors="replace")
            raise RuntimeError(f"PTY session timed out: {rendered[-500:]!r}")
        rendered = output.decode("utf-8", errors="replace")
        if process.returncode or "PTY=stage-a" not in rendered:
            raise RuntimeError(f"PTY round-trip failed: {rendered[-500:]!r}")
        return rendered
    finally:
        os.close(master)
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)


def kill_cleanup(uid: int, gid: int) -> None:
    name = f"ctfbot-stage-a-{uuid.uuid4().hex[:10]}"
    process = subprocess.Popen(
        ["docker", "run", "--rm", "--pull=never", "--name", name,
         "--network", "none", "--read-only", "--user", f"{uid}:{gid}", IMAGE,
         "sleep", "60"],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
    )
    try:
        time.sleep(0.5)
        if process.poll() is not None:
            raise RuntimeError("sleep container exited before forced stop")
        docker(["kill", name], timeout=10)
        process.wait(timeout=10)
        remaining = docker(["ps", "-a", "--filter", f"name=^{name}$", "--format", "{{.Names}}"])
        if remaining.stdout.strip():
            raise RuntimeError(f"container remains after kill: {remaining.stdout.strip()}")
    finally:
        if process.poll() is None:
            docker(["rm", "--force", name], check=False)
            process.kill()
            process.wait(timeout=3)


def main() -> None:
    busybox = Path(shutil.which("busybox") or "/usr/bin/busybox").resolve()
    info = subprocess.run(["file", str(busybox)], capture_output=True, text=True, check=True).stdout
    if "statically linked" not in info:
        raise RuntimeError(f"BusyBox is not static: {info.strip()}")

    with tempfile.TemporaryDirectory(prefix="ctfbot-stage-a-docker-") as temp:
        base = Path(temp)
        context, input_dir, work_dir = (base / name for name in ("context", "input", "work"))
        for directory in (context, input_dir, work_dir):
            directory.mkdir()
        shutil.copy2(busybox, context / "busybox")
        shutil.copyfile(ROOT / "doc/phase-a/spikes/docker_runtime/Dockerfile", context / "Dockerfile")
        (input_dir / "fixture.txt").write_text("synthetic-stage-a-input\n", encoding="utf-8")
        uid, gid = os.getuid(), os.getgid()

        try:
            docker(["build", "--pull=false", "--tag", IMAGE, str(context)], timeout=90)
            script = "\n".join([
                "set -eu",
                'test "$(cat /input/fixture.txt)" = "synthetic-stage-a-input"',
                "if printf blocked > /input/deny.txt 2>/dev/null; then exit 10; fi",
                "if printf blocked > /rootfs-deny.txt 2>/dev/null; then exit 11; fi",
                'printf "workspace-write-ok\\n" > /work/result.txt',
                'test "$(cat /work/result.txt)" = "workspace-write-ok"',
                "read pid_limit < /sys/fs/cgroup/pids.max",
                "read memory_limit < /sys/fs/cgroup/memory.max",
                "read cpu_limit < /sys/fs/cgroup/cpu.max",
                '[ "$pid_limit" = "16" ]',
                '[ "$memory_limit" = "67108864" ]',
                '[ "$cpu_limit" = "25000 100000" ]',
                "if busybox wget -T 2 -q -O /dev/null http://1.1.1.1/ 2>/dev/null; then exit 12; fi",
                'echo "mounts=pass pids=$pid_limit memory=$memory_limit cpu=$cpu_limit egress=blocked"',
            ])
            result = docker([
                "run", "--rm", "--pull=never", "--network", "none", "--read-only",
                "--memory=64m", "--cpus=0.25", "--pids-limit=16", "--user", f"{uid}:{gid}",
                "--cap-drop=ALL", "--security-opt=no-new-privileges",
                "--mount", f"type=bind,src={input_dir},dst=/input,readonly",
                "--mount", f"type=bind,src={work_dir},dst=/work", IMAGE, "sh", "-c", script,
            ], timeout=20)

            pids_pressure = "\n".join([
                "i=0", 'while [ "$i" -lt 12 ]; do /bin/busybox sleep 2 & i=$((i + 1)); done',
                "read current < /sys/fs/cgroup/pids.current", 'echo "pids.current=$current"',
                '[ "$current" -le 16 ]', "wait",
            ])
            pids = docker([
                "run", "--rm", "--pull=never", "--network", "none", "--read-only",
                "--pids-limit=16", "--user", f"{uid}:{gid}", IMAGE, "sh", "-c", pids_pressure,
            ], timeout=12)
            try:
                observed_pids = int(pids.stdout.strip().split("=")[-1])
            except ValueError as exc:
                raise RuntimeError(f"could not parse pids.current: {pids.stdout!r}") from exc
            if observed_pids <= 1 or observed_pids > 16:
                raise RuntimeError(f"unexpected pids.current under load: {observed_pids}")

            memory = docker([
                "run", "--rm", "--pull=never", "--network", "none", "--read-only",
                "--memory=64m", "--user", f"{uid}:{gid}", IMAGE,
                "dd", "if=/dev/zero", "of=/dev/null", "bs=128M", "count=1",
            ], timeout=15, check=False)
            if memory.returncode == 0:
                raise RuntimeError("memory pressure command unexpectedly succeeded")

            pty_round_trip(uid, gid)
            kill_cleanup(uid, gid)
            artifact = (work_dir / "result.txt").read_text(encoding="utf-8").strip()
            if artifact != "workspace-write-ok":
                raise RuntimeError("host could not read the workdir artifact")
            print(result.stdout.strip())
            print(pids.stdout.strip())
            print(f"memory pressure exit={memory.returncode} (expected nonzero)")
            print("PTY input/output and forced-stop cleanup: pass")
            print(f"host artifact: {artifact}")
        finally:
            docker(["image", "rm", IMAGE], timeout=20, check=False)


if __name__ == "__main__":
    main()
