"""Import an explicitly synthetic local TCP challenge without starting dependencies."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from ctfbot.challenge.local_service import LocalServiceSpec


SYNTHETIC_FLAG = "CTFBOT_SYNTHETIC{local-service}"


def import_service_fixture(output: Path, image: str, *, authorize_model_data: bool = False) -> tuple[Path, Path]:
    service = {
        "schema_version": 1, "image": image,
        "argv": ["/usr/local/bin/python3", "/opt/ctfbot-fixture/server.py"],
        "port": 31337, "startup_timeout_seconds": 15,
        "runtime_authorized": True,
        "authorization_basis": "ctfbot authored synthetic local fixture; reviewed local execution profile required",
    }
    LocalServiceSpec.from_manifest(service)
    output.mkdir(mode=0o700)  # never overwrite an earlier snapshot/oracle
    workspace = output / "workspace"
    workspace.mkdir(mode=0o700)
    inputs = workspace / "input"
    inputs.mkdir(mode=0o700)
    protocol = b"Connect to the approved TCP endpoint and send solve followed by a newline.\n"
    task = "Solve the synthetic local TCP service using the attached protocol description.\n"
    metadata_hash = hashlib.sha256(protocol).hexdigest()
    provenance = {
        "challenge_id": "stage-c-service-synthetic", "source_commit": "synthetic-service-v1",
        "challenge_metadata_sha256": metadata_hash, "category": "misc",
        "formal_admission": "admitted", "import_mode": "local_service",
        "authorization_scope": "private local evaluation; do not redistribute challenge assets",
        "model_data_authorized": authorize_model_data,
        "model_data_authorization_basis": "explicit synthetic fixture authorization" if authorize_model_data else None,
        "task_sha256": hashlib.sha256(task.encode()).hexdigest(),
        "input_files": [{"path": "protocol.txt", "bytes": len(protocol), "sha256": metadata_hash}],
        "local_service": service,
    }
    for path, data in (
        (inputs / "protocol.txt", protocol),
        (workspace / "TASK.md", task.encode()),
        (workspace / "provenance.json", json.dumps(provenance, indent=2).encode()),
    ):
        path.write_bytes(data)
        path.chmod(0o444)
    inputs.chmod(0o555)
    oracle = output / "oracle.json"
    oracle.write_text(json.dumps({
        "challenge_id": provenance["challenge_id"], "source_commit": provenance["source_commit"],
        "challenge_metadata_sha256": metadata_hash, "flag": SYNTHETIC_FLAG,
    }), encoding="utf-8")
    oracle.chmod(0o600)
    return workspace, oracle


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--image", required=True, help="immutable locally available synthetic service image")
    parser.add_argument("--authorize-model-data", action="store_true")
    args = parser.parse_args()
    workspace, oracle = import_service_fixture(args.output, args.image, authorize_model_data=args.authorize_model_data)
    print(f"Workspace: {workspace}\nController-only oracle: {oracle}")


if __name__ == "__main__":
    main()
