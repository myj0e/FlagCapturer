"""Build a synthetic-only scratch image from local Linux CPython and static BusyBox.

No image pull or package download. Requires a trusted dynamically linked Linux
CPython installation, its standard library, ldd, Docker and /usr/bin/busybox.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import sysconfig
import tempfile
from pathlib import Path


def copy_file(source: Path, root: Path, destination: Path | None = None) -> None:
    target = root / (destination or source).relative_to("/")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target, follow_symlinks=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", default="ctfbot-c3-fixture:local")
    args = parser.parse_args()
    busybox = Path("/usr/bin/busybox")
    description = subprocess.run(["file", str(busybox)], capture_output=True, text=True, check=True).stdout
    if "statically linked" not in description:
        raise RuntimeError("fixture requires static BusyBox")
    executable = Path(sys.executable).resolve()
    stdlib = Path(sysconfig.get_path("stdlib")).resolve()
    with tempfile.TemporaryDirectory(prefix="ctfbot-c3-image-") as scratch:
        context = Path(scratch)
        root = context / "rootfs"
        copy_file(executable, root)
        shutil.copytree(stdlib, root / stdlib.relative_to("/"),
                        ignore=shutil.ignore_patterns("__pycache__", "site-packages", "test", "tests"))
        dependencies: set[Path] = set()
        for binary in [executable, *stdlib.glob("lib-dynload/*.so")]:
            result = subprocess.run(["ldd", str(binary)], capture_output=True, text=True, check=True)
            if "not found" in result.stdout:
                raise RuntimeError(f"missing shared dependency for {binary}")
            dependencies.update(Path(value) for value in re.findall(r"(/[^\s()]+)", result.stdout))
        for library in dependencies:
            copy_file(library, root)
        copy_file(busybox, root, Path("/bin/busybox"))
        for name in ("sh", "sleep"):
            (root / "bin" / name).symlink_to("busybox")
        (root / "usr/local/bin").mkdir(parents=True, exist_ok=True)
        (root / "usr/local/bin/python3").symlink_to(str(executable))
        (root / "usr/bin/python3").symlink_to(str(executable))
        copy_file(Path(__file__).resolve().parent / "fixtures/local-service/server.py", root,
                  Path("/opt/ctfbot-fixture/server.py"))
        (context / "Dockerfile").write_text(
            "FROM scratch\nCOPY rootfs/ /\nUSER 65532:65532\n"
            'ENTRYPOINT ["/usr/local/bin/python3", "/opt/ctfbot-fixture/server.py"]\n',
            encoding="utf-8",
        )
        subprocess.run(["docker", "build", "--pull=false", "--network=none", "-t", args.tag, str(context)],
                       check=True, timeout=120)
    subprocess.run(["docker", "image", "inspect", "--format", "{{.Id}}", args.tag], check=True, timeout=10)


if __name__ == "__main__":
    main()
