"""Build the general CTF toolbox candidate; acceptance precedes promotion.

Network is used only at build time for Debian packages and hash-locked wheels.
The previous host-only exporter is preserved as export_local_tool_image.py.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from ctfbot.runtime.docker import validate_image_reference


BASE = "python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258"


def build(output: Path, tag: str, base: str = BASE):
    validate_image_reference(base)
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    context = Path(__file__).resolve().parents[2] / "docker" / "tooling"
    subprocess.run(["docker", "build", "--pull=false", "--build-arg", f"BASE_IMAGE={base}",
                    "--progress=plain", "-t", tag, str(context)], check=True, timeout=1800)
    image = subprocess.run(["docker", "image", "inspect", "--format", "{{.Id}}", tag],
                           check=True, capture_output=True, text=True).stdout.strip()
    result = subprocess.run(["docker", "run", "--rm", "--pull=never", "--network=none",
                             "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges",
                             "--pids-limit=64", "--memory=256m", "--entrypoint=python3", image,
                             "-c", "from pathlib import Path; print(Path('/opt/ctfbot/inventory.json').read_text())"],
                            check=True, capture_output=True, text=True, timeout=30)
    inventory = json.loads(result.stdout)
    (output / "inventory.json").write_text(json.dumps(inventory, indent=2) + "\n")
    receipt = dict(schema_version=2, image=image, tag=tag, base=base,
                   context=str(context), sources={name: hashlib.sha256((context/name).read_bytes()).hexdigest()
                   for name in ("Dockerfile", "requirements.in", "requirements.lock", "inventory.py")},
                   scope="candidate build only; run verify_tool_image.py before promotion",
                   reproducibility="base digest and Python hashes pinned; Debian package versions recorded, repositories not frozen")
    (output / "build.json").write_text(json.dumps(receipt, indent=2) + "\n")
    for name in ("build.json", "inventory.json"):
        (output/name).chmod(0o600)
    print(json.dumps(receipt))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--tag", default="ctfbot-tools:candidate")
    parser.add_argument("--base", default=BASE)
    args = parser.parse_args()
    build(args.output, args.tag, args.base)
