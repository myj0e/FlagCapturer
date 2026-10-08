"""Live synthetic smoke for ctfbot.runtime.DockerRuntime; never uses challenge data."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

from ctfbot.runtime.docker import DockerRuntime


def checked(argv: list[str], *, timeout: int = 60) -> subprocess.CompletedProcess[bytes]:
    result = subprocess.run(argv, capture_output=True, timeout=timeout, check=False)
    if result.returncode:
        raise RuntimeError(
            f"command failed ({result.returncode}): {argv[0]} "
            + result.stderr[-1500:].decode("utf-8", errors="replace")
        )
    return result


def main() -> None:
    busybox = Path("/usr/bin/busybox").resolve(strict=True)
    tag = f"ctfbot-adapter-smoke:{uuid.uuid4().hex}"
    runtime: DockerRuntime | None = None
    image_id: str | None = None
    try:
        with tempfile.TemporaryDirectory(prefix="ctfbot-docker-adapter-smoke-") as scratch:
            root = Path(scratch)
            context = root / "context"
            bin_dir = context / "bin"
            challenge = root / "challenge"
            bin_dir.mkdir(parents=True, mode=0o700)
            challenge.mkdir(mode=0o700)
            busybox_copy = bin_dir / "busybox"
            shutil.copyfile(busybox, busybox_copy)
            os.chmod(busybox_copy, 0o755)
            os.link(busybox_copy, bin_dir / "sh")
            os.link(busybox_copy, bin_dir / "sleep")
            (context / "Dockerfile").write_text(
                "FROM scratch\nCOPY bin/ /bin/\n",
                encoding="utf-8",
            )
            (challenge / "TASK.md").write_text("Synthetic fixture only.\n", encoding="utf-8")
            input_path = challenge / "input.txt"
            input_path.write_text("adapter-smoke-input\n", encoding="utf-8")
            os.chmod(input_path, 0o444)
            os.chmod(challenge / "TASK.md", 0o444)

            checked(["docker", "build", "--pull=false", "--network=none", "-t", tag, str(context)])
            image_id = checked(["docker", "image", "inspect", "--format", "{{.Id}}", tag]).stdout.decode().strip()
            runtime = DockerRuntime(challenge, image_id)
            runtime.start()

            output = runtime.execute(["/bin/busybox", "echo", "adapter-smoke-ok"], timeout=10)
            assert output.exit_code == 0 and b"adapter-smoke-ok" in output.stdout
            read_input = runtime.execute(["/bin/busybox", "cat", "/challenge/input.txt"], timeout=10)
            assert read_input.exit_code == 0 and b"adapter-smoke-input" in read_input.stdout
            write_work = runtime.execute(["/bin/sh", "-c", "printf work-ok > /work/probe"], timeout=10)
            assert write_work.exit_code == 0
            read_work = runtime.execute(["/bin/busybox", "cat", "/work/probe"], timeout=10)
            assert read_work.exit_code == 0 and b"work-ok" in read_work.stdout
            denied = runtime.execute(["/bin/sh", "-c", "printf no > /challenge/forbidden"], timeout=10)
            assert denied.exit_code != 0
            rootfs_write = runtime.execute(["/bin/sh", "-c", "printf no > /bin/forbidden"], timeout=10)
            assert rootfs_write.exit_code != 0
            identity = runtime.execute(["/bin/busybox", "id", "-u"], timeout=10)
            assert identity.exit_code == 0 and identity.stdout.strip() == str(os.getuid()).encode()

            inspect = json.loads(checked(["docker", "inspect", runtime.container_name]).stdout)[0]
            host = inspect["HostConfig"]
            assert host["NetworkMode"] == "none"
            assert host["ReadonlyRootfs"] is True
            assert inspect["Config"]["User"] == f"{os.getuid()}:{os.getgid()}"
            assert host["PidsLimit"] == 256
            assert host["Memory"] == 4 * 1024 * 1024 * 1024
            assert host["NanoCpus"] == 2_000_000_000
            assert host["CapDrop"] == ["ALL"]
            assert "no-new-privileges:true" in host["SecurityOpt"]
            assert not any("docker.sock" in mount.get("Source", "") for mount in inspect.get("Mounts", []))

            name = runtime.container_name
            timed_out = runtime.execute(["/bin/sleep", "3"], timeout=0.2)
            assert timed_out.timed_out and timed_out.exit_code == 124
            runtime.close()
            runtime = None
            gone = subprocess.run(["docker", "inspect", name], capture_output=True, timeout=10, check=False)
            assert gone.returncode != 0
            print("ctfbot DockerRuntime synthetic smoke: PASS")
    finally:
        if runtime is not None:
            runtime.close()
        if image_id:
            subprocess.run(["docker", "image", "rm", "-f", tag], capture_output=True, timeout=30, check=False)


if __name__ == "__main__":
    main()
