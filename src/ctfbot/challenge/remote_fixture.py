"""Create a private authored TCP fixture snapshot, without starting a connection."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from ctfbot.challenge.remote import RemoteSpec


SYNTHETIC_FLAG = "CTFBOT_SYNTHETIC{remote-tcp}"


def import_remote_fixture(output: Path, remote: dict[str, Any], *,
                          authorize_model_data: bool = False) -> Path:
    spec = RemoteSpec.from_manifest(remote)
    output.mkdir(mode=0o700)
    workspace = output / "workspace"
    workspace.mkdir(mode=0o700)
    inputs = workspace / "input"
    inputs.mkdir(mode=0o700)
    protocol = (f"Authored synthetic fixture at {spec.endpoint}. Send solve followed by newline "
                "using remote_tcp_exchange; the fixture returns a flag and closes.\n").encode()
    task = "Solve the authored synthetic TCP fixture using the attached protocol description.\n"
    metadata_hash = hashlib.sha256(protocol).hexdigest()
    provenance = {
        "challenge_id": "stage-c-remote-synthetic", "source_commit": "synthetic-remote-v1",
        "challenge_metadata_sha256": metadata_hash, "category": "misc",
        "formal_admission": "admitted", "import_mode": "remote",
        "authorization_scope": "private local evaluation; do not redistribute challenge assets",
        "model_data_authorized": authorize_model_data,
        "model_data_authorization_basis": "explicit synthetic fixture authorization" if authorize_model_data else None,
        "task_sha256": hashlib.sha256(task.encode()).hexdigest(),
        "input_files": [{"path": "protocol.txt", "bytes": len(protocol), "sha256": metadata_hash}],
        "remote": remote,
    }
    for path, data in (
        (inputs / "protocol.txt", protocol), (workspace / "TASK.md", task.encode()),
        (workspace / "provenance.json", json.dumps(provenance, indent=2).encode()),
    ):
        path.write_bytes(data)
        path.chmod(0o444)
    inputs.chmod(0o555)
    return workspace


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--remote-spec", type=Path, required=True, help="version-1 requested scope JSON; not a network grant")
    parser.add_argument("--authorize-model-data", action="store_true")
    args = parser.parse_args()
    workspace = import_remote_fixture(args.output, json.loads(args.remote_spec.read_text()),
                                              authorize_model_data=args.authorize_model_data)
    print(f"Workspace: {workspace}")


if __name__ == "__main__":
    main()
