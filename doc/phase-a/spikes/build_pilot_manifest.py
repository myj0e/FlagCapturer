"""Create a hash-only inventory for the Stage A CTFTiny pilot candidates.

The script never copies or prints challenge contents. Challenge metadata may
contain the gold flag, so the manifest records only its hash and field names.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from datetime import date
from pathlib import Path


PILOT = {
    "cry-babycrypto": "ctftiny/cry/babycrypto",
    "cry-collision-course": "ctftiny/cry/Collision-Course",
    "cry-ecxor": "ctftiny/cry/ECXOR",
    "cry-perfect-secrecy": "ctftiny/cry/perfect_secrecy",
    "cry-super-curve": "ctftiny/cry/super_curve",
    "cry-the-lengths-we-extend-ourselves": "ctftiny/cry/The Lengths we Extend Ourselves",
    "for-1black0white": "ctftiny/for/1black0white",
    "for-whyos": "ctftiny/for/whyOS",
    "pwn-puffin": "ctftiny/pwn/puffin",
    "pwn-bigboy": "ctftiny/pwn/bigboy",
    "pwn-roppity": "ctftiny/pwn/roppity",
    "rev-checker": "ctftiny/rev/checker",
    "rev-tablez": "ctftiny/rev/tablez",
    "rev-baby-mult": "ctftiny/rev/baby_mult",
    "rev-dockreleakage": "ctftiny/rev/dockREleakage",
    "rev-rap": "ctftiny/rev/rap",
    "rev-whataxor": "ctftiny/rev/whataxor",
    "rev-maze": "ctftiny/rev/maze",
    "web-poem-collection": "ctftiny/web/poem-collection",
    "web-smug-dino": "ctftiny/web/smug-dino",
    "web-shreeramquest": "ctftiny/web/ShreeRamQuest",
    "msc-weak-password": "ctftiny/msc/Weak-Password",
    "msc-ezmaze": "ctftiny/msc/ezMaze",
    "msc-showdown": "ctftiny/msc/showdown",
    "msc-quantum-leap": "ctftiny/msc/quantum-leap",
    "rev-ezbreezy": "ctftiny/rev/ezbreezy",
}

# Proposed offline artifact shortlist. This is a candidate selection, not a
# claim that licensing, artifact review, or runtime admission has passed.
PROPOSED_OFFLINE_PILOT = {
    "cry-babycrypto": "static_files_only",
    "cry-ecxor": "static_files_only",
    "cry-perfect-secrecy": "offline_artifact_only",
    "for-1black0white": "static_files_only",
    "rev-baby-mult": "offline_artifact_only",
    "rev-rap": "offline_artifact_only",
    "rev-tablez": "static_files_only",
    "rev-ezbreezy": "offline_artifact_only",
    "msc-ezmaze": "offline_artifact_only",
    "msc-quantum-leap": "offline_artifact_only",
}

PRIVATE_NAME = re.compile(
    r"(^|[._-])(flag|answer|solution|solver|solve|writeup|plaintext)([._-]|$)",
    re.IGNORECASE,
)
SETUP_NAMES = {
    "dockerfile",
    "docker-compose.yml",
    "compose.yml",
    "compose.yaml",
    "makefile",
    "package.json",
    "requirements.txt",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def contains_bytes(path: Path, needle: bytes) -> bool:
    """Scan for an exact oracle value without exposing it in the manifest."""
    overlap = b""
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            data = overlap + block
            if needle in data:
                return True
            overlap = data[-(len(needle) - 1):] if len(needle) > 1 else b""
    return False


def file_role(relative: Path, declared_inputs: set[str]) -> str:
    parts = {part.lower() for part in relative.parts}
    name = relative.name.lower()
    if "__pycache__" in parts or name.endswith((".pyc", ".pyo")):
        return "exclude_generated"
    if "test_solver" in parts or "hints" in parts or PRIVATE_NAME.search(name):
        return "oracle_or_solution_private"
    if name == "challenge.json":
        return "oracle_metadata_private"
    if name in SETUP_NAMES or name.endswith((".sh", ".service")):
        return "environment_setup_review_required"
    if name.startswith("readme") or "writeup" in name:
        return "prompt_or_document_review_required"
    if relative.as_posix() in declared_inputs:
        return "challenge_input_candidate"
    return "not_available_to_agent_until_review"


def document_has_solution_markers(path: Path) -> bool:
    """Classify likely answer documents without copying or printing their contents."""
    try:
        with path.open("rb") as stream:
            sample = stream.read(2 * 1024 * 1024).decode("utf-8", errors="replace")
    except OSError:
        return False
    return bool(
        re.search(r"(?im)^\s{0,3}#{0,3}\s*(solution|answer|writeup|flag)\b", sample)
        or re.search(r"(?i)flag\s*\{[^\r\n}]{1,200}\}", sample)
    )


def inspect_challenge(source_root: Path, challenge_id: str, source_path: str) -> dict:
    challenge_root = source_root / source_path
    metadata_path = challenge_root / "challenge.json"
    if not metadata_path.is_file():
        return {
            "id": challenge_id,
            "path": source_path,
            "review_status": "candidate_missing_metadata",
            "files": [],
        }

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    declared_inputs = {
        (Path(source_path) / str(name)).as_posix()
        for name in metadata.get("files", [])
        if isinstance(name, str)
    }
    exact_oracle_input_paths: set[str] = set()
    flag = metadata.get("flag")
    if isinstance(flag, str) and len(flag.encode("utf-8")) >= 8:
        for name in metadata.get("files", []):
            if not isinstance(name, str):
                continue
            relative_name = Path(name)
            if (
                relative_name.is_absolute()
                or "\\" in name
                or "\x00" in name
                or any(part in {".", ".."} for part in relative_name.parts)
            ):
                continue
            source_file = challenge_root / relative_name
            cursor = challenge_root
            has_symlink = False
            for part in relative_name.parts:
                cursor = cursor / part
                if cursor.is_symlink():
                    has_symlink = True
                    break
            if (
                not has_symlink
                and source_file.is_file()
                and source_file.resolve(strict=True).is_relative_to(challenge_root.resolve(strict=True))
                and contains_bytes(source_file, flag.encode("utf-8"))
            ):
                exact_oracle_input_paths.add(relative_name.as_posix())
    source_files: list[dict] = []
    findings: list[dict[str, str]] = []
    for path in sorted(challenge_root.rglob("*")):
        relative_in_repo = path.relative_to(source_root)
        relative_in_challenge = path.relative_to(challenge_root)
        if path.is_symlink():
            source_files.append({
                "path": relative_in_challenge.as_posix(),
                "role": "symlink_review_required",
            })
            continue
        if path.is_dir():
            continue
        role = file_role(relative_in_repo, declared_inputs)
        if relative_in_challenge.as_posix() in exact_oracle_input_paths:
            role = "declared_input_contains_exact_oracle_private"
            findings.append({
                "path": relative_in_challenge.as_posix(),
                "finding": "declared input contains exact oracle value; exclude from agent input",
            })
        if path.name.lower().startswith("readme") and document_has_solution_markers(path):
            role = "oracle_or_solution_private"
            findings.append({
                "path": relative_in_challenge.as_posix(),
                "finding": "document_contains_solution_or_flag_markers; exclude from agent input",
            })
        source_files.append({
            "path": relative_in_challenge.as_posix(),
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
            "role": role,
        })

    missing_inputs = sorted(
        item for item in declared_inputs
        if not (source_root / item).is_file() or (source_root / item).is_symlink()
    )
    env_kind = (
        "compose_local_review_required"
        if metadata.get("compose")
        else "remote_endpoint_do_not_connect"
        if metadata.get("box") or metadata.get("type") == "dynamic"
        else "static_file_reset_review_required"
    )
    return {
        "id": challenge_id,
        "category": metadata.get("category"),
        "name": metadata.get("name"),
        "source_path": source_path,
        "upstream_reference": metadata.get("reference"),
        "environment_kind": env_kind,
        "compose_declared": bool(metadata.get("compose")),
        "remote_box_declared": bool(metadata.get("box")),
        "internal_port": metadata.get("internal_port"),
        "declared_input_paths": sorted(str(Path(source_path) / name) for name in metadata.get("files", []) if isinstance(name, str)),
        "declared_inputs_missing": missing_inputs,
        "declared_inputs_containing_exact_oracle": sorted(exact_oracle_input_paths),
        "oracle_metadata_has_flag_field": "flag" in metadata,
        "oracle_metadata_sha256": sha256(metadata_path),
        "review_status": "not_admitted_runtime_and_license_review_pending",
        "document_review_findings": findings,
        "files": source_files,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source_root", type=Path, help="Local CTFTiny clone")
    parser.add_argument("output", type=Path, help="Manifest JSON output path")
    parser.add_argument(
        "--attachment-review", type=Path,
        help="Optional sanitized result from review_candidate_attachments.py",
    )
    args = parser.parse_args()

    source_root = args.source_root.resolve()
    commit = subprocess.check_output(
        ["git", "-C", str(source_root), "rev-parse", "HEAD"], text=True
    ).strip()
    attachment_reviews: dict[str, dict] = {}
    if args.attachment_review:
        review_data = json.loads(args.attachment_review.read_text(encoding="utf-8"))
        if review_data.get("source_commit") != commit:
            raise SystemExit("attachment review source commit does not match the CTFTiny checkout")
        attachment_reviews = {
            item["challenge_id"]: item
            for item in review_data.get("challenges", [])
            if isinstance(item, dict) and isinstance(item.get("challenge_id"), str)
        }
        expected_ids = set(PILOT)
        if set(attachment_reviews) != expected_ids:
            raise SystemExit("attachment review does not contain exactly the manifest challenge IDs")
    manifest = {
        "schema_version": 1,
        "created_date": date.today().isoformat(),
        "source_repository": "https://github.com/NYU-LLM-CTF/CTFTiny",
        "source_commit": commit,
        "source_root_license": "GPL-2.0 (as declared by upstream repository)",
        "per_challenge_asset_license": "not independently evidenced by repository root license; user attested local evaluation authorization",
        "storage_policy": {
            "mode": "external_local_checkout",
            "submodule": False,
            "source_checkout": "/tmp/ctfbot-ctftiny-stage-a",
            "challenge_assets_committed_to_ctfbot": False,
            "redistribution": "not authorized",
        },
        "authorization": {
            "basis": "User confirmed local private evaluation on 2026-09-29 and approved Docker-isolated execution of all existing challenge attachments on 2026-09-30",
            "scope": "private local evaluation only; do not redistribute challenge assets",
            "docker_attachment_execution": True,
            "llm_data_transfer": False,
        },
        "gold_oracle_policy": "provisionally accepted by user; reopen detailed oracle/verifier review if an actual evaluation fails",
        "agent_input_policy": (
            "No upstream file is copied to an agent workspace automatically. Only individually reviewed and hash-pinned "
            "challenge artifacts plus a curated prompt may be exposed. Never expose challenge.json, README files, "
            "test_solver, hints, solution/writeup files, or flag artifacts."
        ),
        "challenges": [],
    }
    for challenge_id, source_path in PILOT.items():
        record = inspect_challenge(source_root, challenge_id, source_path)
        record["proposed_pilot_mode"] = PROPOSED_OFFLINE_PILOT.get(challenge_id)
        record["admission_status"] = "not_admitted"
        record["answer_leak_review_status"] = "not_reviewed"
        record["oracle_acceptance_status"] = "provisionally_accepted_by_user_reopen_on_evaluation_failure"
        record["model_data_authorized"] = False
        record["model_data_authorization_basis"] = None
        review = attachment_reviews.get(challenge_id)
        if review:
            findings = []
            for item in review.get("files", []):
                signals = {
                    "path": item.get("path"),
                    "oracle_forms_found": item.get("oracle_forms_found", []),
                    "flag_shaped_strings": item.get("flag_shaped_strings", 0),
                    "normalized_oracle_match": item.get("normalized_oracle_match", False),
                    "archive_content_status": item.get("archive_content_status"),
                    "archive_has_solution_named_member": item.get("archive_has_solution_named_member", False),
                    "scan_status": item.get("scan_status", {}),
                }
                if (
                    signals["oracle_forms_found"] or signals["flag_shaped_strings"]
                    or signals["normalized_oracle_match"] or signals["archive_has_solution_named_member"]
                    or any(value not in {"complete", "scanned", "raw_input"} for value in signals["scan_status"].values())
                ):
                    findings.append(signals)
            execution_findings = [
                {
                    "path": item.get("path"),
                    "kind": item.get("kind"),
                    "oracle_forms_found": item.get("oracle_forms_found", []),
                    "flag_shaped_strings": item.get("flag_shaped_strings", 0),
                    "normalized_oracle_match": item.get("normalized_oracle_match", False),
                    "exit_code": item.get("exit_code"),
                    "timed_out": item.get("timed_out", False),
                    "truncated": item.get("truncated", False),
                }
                for item in review.get("execution_smokes", [])
                if (
                    item.get("oracle_forms_found") or item.get("flag_shaped_strings")
                    or item.get("normalized_oracle_match") or item.get("timed_out") or item.get("truncated")
                )
            ]
            record["answer_leak_review_status"] = review.get("status", "review_error")
            record["attachment_review_summary"] = {
                "declared_files_reviewed": len(review.get("files", [])),
                "execution_smokes": len(review.get("execution_smokes", [])),
                "findings": findings,
                "execution_findings": execution_findings,
            }
        manifest["challenges"].append(record)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {len(manifest['challenges'])} challenge records to {args.output}")
    print(f"Source commit: {commit}")


if __name__ == "__main__":
    main()
