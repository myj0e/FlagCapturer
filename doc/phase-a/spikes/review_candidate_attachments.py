"""Private, no-network Stage A attachment review using the ctfbot Docker profile.

The script stages only manifest-declared inputs for one challenge at a time.
It never prints or writes oracle values, strings output, command output, or raw
challenge files. The JSON report contains findings and exit metadata only.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import tempfile
import urllib.parse
import zlib
from pathlib import Path, PurePosixPath
from typing import Any

from ctfbot.runtime.docker import DockerRuntime


DEFAULT_SOURCE = Path("/tmp/ctfbot-ctftiny-stage-a")
DEFAULT_IMAGE = "sha256:61066f13fb37f4849473646835763de8de96d750769caf0797e7aaa6220d7b60"
MAX_COMMAND_SECONDS = 20
MAX_ARCHIVE_SCAN_BYTES = 64 * 1024 * 1024
ARCHIVE_CHUNK_BYTES = 1024 * 1024
FLAG_SHAPE = re.compile(rb"(?i)(?:flag|ctf|picoctf|htb|csaw|ictf|utctf|dice|uiuctf)[-_a-z0-9]*\{[^}\r\n]{1,256}\}")
SUSPICIOUS_MEMBER = re.compile(r"(?i)(?:^|[/_.-])(flag|solution|writeup|answer|test_solver|solver|secret)(?:$|[/_.-])")


def _variants(flag: str) -> dict[str, bytes]:
    raw = flag.encode("utf-8")
    variants: dict[str, bytes] = {
        "exact": raw,
        "casefold": raw.lower(),
        "url_encoded": urllib.parse.quote(flag, safe="").encode(),
        "base64": base64.b64encode(raw),
        "base32": base64.b32encode(raw),
        "hex_lower": raw.hex().encode(),
        "hex_upper": raw.hex().upper().encode(),
        "reversed": raw[::-1],
    }
    if len(flag) > 2 and flag[1] == "{" and flag.endswith("}"):
        core = flag[2:-1].encode("utf-8")
        if core:
            variants.update({
                "flag_body": core,
                "body_base64": base64.b64encode(core),
                "body_hex": core.hex().encode(),
                "body_reversed": core[::-1],
            })
    for algorithm in ("md5", "sha1", "sha256"):
        variants[f"{algorithm}_hex"] = hashlib.new(algorithm, raw).hexdigest().encode()
    return {name: value for name, value in variants.items() if len(value) >= 6}


def _findings(data: bytes, variants: dict[str, bytes]) -> dict[str, Any]:
    found = sorted(name for name, value in variants.items() if value.lower() in data.lower())
    shapes = len(FLAG_SHAPE.findall(data))
    normalized_data = re.sub(rb"[^a-z0-9]", b"", data.lower())
    normalized = False
    exact = variants.get("exact", b"")
    if exact:
        normalized_flag = re.sub(rb"[^a-z0-9]", b"", exact.lower())
        normalized = len(normalized_flag) >= 8 and normalized_flag in normalized_data
    return {"oracle_forms": found, "flag_shaped_strings": shapes, "normalized_oracle_match": normalized}


def _safe_rel(value: str, challenge_path: str) -> str:
    path = PurePosixPath(value)
    prefix = PurePosixPath(challenge_path)
    if path.is_absolute() or ".." in path.parts or "\\" in value or "\x00" in value:
        raise ValueError("unsafe manifest input path")
    try:
        relative = path.relative_to(prefix)
    except ValueError as exc:
        raise ValueError("manifest input path is outside its challenge") from exc
    if not relative.parts:
        raise ValueError("manifest input path points to a directory")
    return relative.as_posix()


class ReviewSandbox:
    """Restart a per-challenge container after a timeout or runtime error."""

    def __init__(self, challenge_root: Path, image: str) -> None:
        self.challenge_root = challenge_root
        self.image = image
        self.runtime: DockerRuntime | None = None
        self.needs_restart = True

    def execute(self, argv: list[str], *, timeout: int) -> Any:
        if self.needs_restart:
            if self.runtime is not None:
                self.runtime.close()
            self.runtime = DockerRuntime(self.challenge_root, self.image)
            self.runtime.start()
            self.needs_restart = False
        assert self.runtime is not None
        try:
            result = self.runtime.execute(argv, timeout=timeout)
        except Exception:
            self.needs_restart = True
            raise
        if result.timed_out:
            self.needs_restart = True
        return result

    def close(self) -> None:
        if self.runtime is not None:
            self.runtime.close()
            self.runtime = None
        self.needs_restart = True

    def reset(self) -> None:
        """Discard a container before/after running an untrusted attachment."""
        self.close()


def _run(runtime: ReviewSandbox, argv: list[str], *, timeout: int = MAX_COMMAND_SECONDS) -> dict[str, Any]:
    try:
        result = runtime.execute(argv, timeout=timeout)
        return {
            "exit_code": result.exit_code,
            "timed_out": result.timed_out,
            "truncated": result.truncated,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
    except Exception as exc:
        return {"exit_code": None, "timed_out": False, "truncated": False,
                "stdout": b"", "stderr": b"", "error_type": type(exc).__name__}


def _command_status(result: dict[str, Any]) -> str:
    if result.get("error_type"):
        return "error"
    if result["timed_out"]:
        return "timed_out"
    if result["truncated"]:
        return "truncated"
    if result["exit_code"] != 0:
        return "command_failed"
    return "complete"


def _empty_findings() -> dict[str, Any]:
    return {"oracle_forms": [], "flag_shaped_strings": 0, "normalized_oracle_match": False}


def _merge_findings(target: dict[str, Any], found: dict[str, Any]) -> None:
    target["oracle_forms"] = sorted(set(target["oracle_forms"]) | set(found["oracle_forms"]))
    target["flag_shaped_strings"] += found["flag_shaped_strings"]
    target["normalized_oracle_match"] |= found["normalized_oracle_match"]


def _archive_payload(runtime: ReviewSandbox, path: str, suffix: str, file_type: str,
                     variants: dict[str, bytes]) -> tuple[dict[str, Any], str | None, bool, str | None]:
    detected_type = file_type.lower()
    archive_format = suffix
    if suffix in {".tar.gz", ".tgz", ".tar"}:
        if "tar archive" in detected_type and "gzip compressed" not in detected_type:
            archive_format = ".tar"
        elif "gzip compressed data" in detected_type:
            archive_format = ".tar.gz"
    suffix = archive_format
    if suffix == ".zip":
        listing = _run(runtime, ["unzip", "-Z1", path])
        extract = _run(runtime, ["/bin/sh", "-c", 'set -e; unzip -p "$1" > /work/ctfbot-review-archive.payload', "ctfbot-review", path])
    elif suffix in {".tar.gz", ".tgz"}:
        listing = _run(runtime, ["tar", "-tzf", path])
        extract = _run(runtime, ["/bin/sh", "-c", 'set -e; tar -xOzf "$1" > /work/ctfbot-review-archive.payload', "ctfbot-review", path])
    elif suffix == ".tar":
        listing = _run(runtime, ["tar", "-tf", path])
        extract = _run(runtime, ["/bin/sh", "-c", 'set -e; tar -xOf "$1" > /work/ctfbot-review-archive.payload', "ctfbot-review", path])
    elif suffix == ".deb":
        listing = _run(runtime, ["dpkg-deb", "--contents", path])
        extract = _run(runtime, ["/bin/sh", "-c", 'set -e; dpkg-deb --fsys-tarfile "$1" > /work/ctfbot-review-archive.tar; tar -xOf /work/ctfbot-review-archive.tar > /work/ctfbot-review-archive.payload', "ctfbot-review", path])
    else:
        return _empty_findings(), None, False, None
    names = listing["stdout"].decode("utf-8", errors="replace").splitlines()
    suspicious_names = [line[:300] for line in names if SUSPICIOUS_MEMBER.search(line)]
    findings = _empty_findings()
    _merge_findings(findings, _findings(listing["stdout"], variants))
    statuses = {_command_status(listing), _command_status(extract)}
    if "error" in statuses:
        scan_status = "error"
    elif "timed_out" in statuses:
        scan_status = "timed_out"
    elif "truncated" in statuses:
        scan_status = "truncated"
    elif "command_failed" in statuses:
        scan_status = "command_failed"
    else:
        size_result = _run(runtime, ["stat", "-c", "%s", "/work/ctfbot-review-archive.payload"])
        if _command_status(size_result) != "complete":
            return findings, _command_status(size_result), bool(suspicious_names), archive_format
        try:
            content_size = int(size_result["stdout"].strip())
        except ValueError:
            return findings, "invalid_size_result", bool(suspicious_names), archive_format
        if content_size > MAX_ARCHIVE_SCAN_BYTES:
            return findings, "size_cap_exceeded", bool(suspicious_names), archive_format
        carry = b""
        for offset in range(0, content_size, ARCHIVE_CHUNK_BYTES):
            chunk_result = _run(runtime, [
                "dd", "if=/work/ctfbot-review-archive.payload", "bs=1048576",
                f"skip={offset // ARCHIVE_CHUNK_BYTES}", "count=1", "status=none",
            ])
            if _command_status(chunk_result) != "complete":
                return findings, _command_status(chunk_result), bool(suspicious_names), archive_format
            chunk = chunk_result["stdout"]
            if len(chunk) > ARCHIVE_CHUNK_BYTES:
                return findings, "chunk_output_exceeded", bool(suspicious_names), archive_format
            data = carry + chunk
            _merge_findings(findings, _findings(data, variants))
            carry = data[-512:]
        scan_status = "scanned"
    return findings, scan_status, bool(suspicious_names), archive_format


def _png_text(runtime: ReviewSandbox, path: str) -> dict[str, Any]:
    script = r'''
import struct
import sys
import zlib

maximum_input = 32 * 1024 * 1024
maximum_output = 1024 * 1024
data = open(sys.argv[1], "rb").read(maximum_input + 1)
if len(data) > maximum_input or not data.startswith(b"\x89PNG\r\n\x1a\n"):
    raise SystemExit(2)
offset = 8
chunks = []
total = 0
while offset + 12 <= len(data):
    size = struct.unpack("!I", data[offset:offset + 4])[0]
    kind = data[offset + 4:offset + 8]
    start = offset + 8
    end = start + size
    if end + 4 > len(data):
        raise SystemExit(3)
    body = data[start:end]
    if kind == b"tEXt":
        chunk = body
    elif kind == b"zTXt":
        key, separator, rest = body.partition(b"\x00")
        if not separator or not rest or rest[0] != 0:
            raise SystemExit(4)
        decoded = zlib.decompressobj().decompress(rest[1:], maximum_output - total)
        chunk = key + b"\x00" + decoded
    elif kind == b"iTXt":
        key, separator, rest = body.partition(b"\x00")
        if not separator or len(rest) < 2:
            raise SystemExit(5)
        compressed, method, rest = rest[0], rest[1], rest[2:]
        language, separator, rest = rest.partition(b"\x00")
        if not separator:
            raise SystemExit(6)
        translated, separator, text = rest.partition(b"\x00")
        if not separator:
            raise SystemExit(7)
        if compressed == 1 and method == 0:
            text = zlib.decompressobj().decompress(text, maximum_output - total)
        elif compressed != 0:
            raise SystemExit(8)
        chunk = key + b"\x00" + language + b"\x00" + translated + b"\x00" + text
    else:
        chunk = b""
    if chunk:
        chunks.append(chunk[:maximum_output - total])
        total += min(len(chunk), maximum_output - total)
    offset = end + 4
    if kind == b"IEND":
        break
    if total >= maximum_output:
        break
sys.stdout.buffer.write(b"\n".join(chunks)[:maximum_output])
'''
    return _run(runtime, ["python3", "-B", "-c", script, path])


def review(source_root: Path, manifest_path: Path, image: str) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or not isinstance(manifest.get("challenges"), list):
        raise ValueError("pilot manifest is malformed")
    source_root = source_root.resolve(strict=True)
    report: dict[str, Any] = {"schema_version": 1, "source_commit": manifest.get("source_commit"), "challenges": []}

    for record in manifest["challenges"]:
        challenge_id = record.get("id")
        declared = record.get("declared_input_paths", [])
        challenge_path = str(record.get("source_path", ""))
        row: dict[str, Any] = {"challenge_id": challenge_id, "category": record.get("category"), "files": []}
        if not declared:
            row["status"] = "no_declared_input"
            report["challenges"].append(row)
            continue

        metadata_path = source_root / challenge_path / "challenge.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        flag = metadata.get("flag")
        if not isinstance(flag, str) or not flag:
            row["status"] = "missing_oracle"
            report["challenges"].append(row)
            continue
        variants = _variants(flag)
        with tempfile.TemporaryDirectory(prefix="ctfbot-attachment-review-") as temporary:
            challenge_root = Path(temporary) / "challenge"
            input_root = challenge_root / "input"
            input_root.mkdir(parents=True, mode=0o700)
            source_inputs: list[tuple[str, Path, Path, bytes]] = []
            for raw_path in declared:
                rel = _safe_rel(str(raw_path), challenge_path)
                source = source_root / str(raw_path)
                if source.is_symlink() or not source.is_file() or not source.resolve(strict=True).is_relative_to(source_root):
                    raise ValueError(f"unsafe or missing input for {challenge_id}")
                target = input_root / rel
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                shutil.copyfile(source, target)
                os.chmod(target, 0o444)
                source_inputs.append((rel, source, target, source.read_bytes()))
            for directory in sorted((p for p in input_root.rglob("*") if p.is_dir()), reverse=True):
                os.chmod(directory, 0o555)
            os.chmod(input_root, 0o555)
            os.chmod(challenge_root, 0o700)

            runtime = ReviewSandbox(challenge_root, image)
            try:
                executable_results: list[dict[str, Any]] = []
                incomplete = False
                for rel, source, _target, raw in source_inputs:
                    container_path = "/challenge/input/" + rel
                    file_info = _run(runtime, ["file", "-b", container_path])
                    file_type = file_info["stdout"].decode("utf-8", errors="replace").strip()
                    strings = _run(runtime, ["strings", "-a", "-n", "4", container_path])
                    combined = raw + b"\n" + strings["stdout"]
                    findings = _findings(combined, variants)
                    suffix = source.suffix.lower()
                    archive_status = None
                    archive_name_suspect = False
                    if suffix in {".zip", ".deb", ".tgz", ".tar"} or source.name.lower().endswith(".tar.gz"):
                        archive_suffix = ".tar.gz" if source.name.lower().endswith(".tar.gz") else suffix
                        archive_findings, archive_status, archive_name_suspect, archive_format = _archive_payload(
                            runtime, container_path, archive_suffix, file_type, variants
                        )
                    else:
                        archive_format = None
                        archive_findings = _empty_findings()
                    combined_findings = sorted(set(findings["oracle_forms"]) | set(archive_findings["oracle_forms"]))
                    png_findings = {"oracle_forms": [], "flag_shaped_strings": 0, "normalized_oracle_match": False}
                    png_status = None
                    if suffix == ".png":
                        png_text = _png_text(runtime, container_path)
                        png_status = _command_status(png_text)
                        png_findings = _findings(png_text["stdout"], variants)
                        combined_findings = sorted(set(combined_findings) | set(png_findings["oracle_forms"]))
                    file_scan_status = {
                        "raw_input": "complete",
                        "strings": _command_status(strings),
                        "file_command": _command_status(file_info),
                    }
                    if archive_status:
                        file_scan_status["archive_content"] = archive_status
                    if png_status:
                        file_scan_status["png_text"] = png_status
                    if any(status not in {"complete", "scanned"} for status in file_scan_status.values()):
                        incomplete = True
                    report_file: dict[str, Any] = {
                        "path": rel,
                        "bytes": len(raw),
                        "sha256": hashlib.sha256(raw).hexdigest(),
                        "file_type": file_type[:240],
                        "file_command_exit": file_info["exit_code"],
                        "scan_status": file_scan_status,
                        "oracle_forms_found": combined_findings,
                        "flag_shaped_strings": findings["flag_shaped_strings"] + archive_findings["flag_shaped_strings"] + png_findings["flag_shaped_strings"],
                        "normalized_oracle_match": findings["normalized_oracle_match"] or archive_findings["normalized_oracle_match"] or png_findings["normalized_oracle_match"],
                        "archive_content_status": archive_status,
                        "archive_format_detected": archive_format,
                        "archive_has_solution_named_member": archive_name_suspect,
                    }
                    if suffix == ".png":
                        report_file["png_text_scan_status"] = png_status
                        report_file["png_text_oracle_forms"] = png_findings["oracle_forms"]
                        report_file["png_text_flag_shaped_strings"] = png_findings["flag_shaped_strings"]
                    row["files"].append(report_file)

                files_by_path = {item["path"]: item for item in row["files"]}
                for rel, source, _target, _raw in source_inputs:
                    suffix = source.suffix.lower()
                    file_record = files_by_path[rel]
                    kind = file_record["file_type"].lower()
                    container_path = "/challenge/input/" + rel
                    result = None
                    smoke_kind = None
                    runtime.reset()
                    try:
                        if suffix == ".py":
                            smoke_kind = "python_smoke"
                            result = _run(runtime, ["python3", "-B", container_path], timeout=10)
                        elif "elf" in kind and "shared object" not in kind and suffix != ".so":
                            smoke_kind = "elf_smoke"
                            destination = "/work/ctfbot-candidate-executable"
                            copied = _run(runtime, ["cp", container_path, destination])
                            if copied["exit_code"] == 0:
                                chmod = _run(runtime, ["chmod", "700", destination])
                                result = _run(runtime, [destination], timeout=10) if chmod["exit_code"] == 0 else chmod
                            else:
                                result = copied
                    finally:
                        runtime.reset()
                    if result is not None and smoke_kind is not None:
                        evidence = result["stdout"] + result["stderr"]
                        run_findings = _findings(evidence, variants)
                        executable_results.append({
                            "path": rel, "kind": smoke_kind, "exit_code": result["exit_code"],
                            "timed_out": result["timed_out"], "truncated": result["truncated"],
                            "oracle_forms_found": run_findings["oracle_forms"],
                            "flag_shaped_strings": run_findings["flag_shaped_strings"],
                            "normalized_oracle_match": run_findings["normalized_oracle_match"],
                        })
                        incomplete |= result["timed_out"] or result["truncated"] or bool(result.get("error_type"))
                row["execution_smokes"] = executable_results
                leak_found = any(
                    file.get("oracle_forms_found") or file.get("flag_shaped_strings")
                    or file.get("normalized_oracle_match") or file.get("archive_has_solution_named_member")
                    for file in row["files"]
                ) or any(
                    run.get("oracle_forms_found") or run.get("flag_shaped_strings")
                    or run.get("normalized_oracle_match")
                    for run in executable_results
                )
                row["status"] = "potential_answer_leak" if leak_found else "review_incomplete" if incomplete else "no_obvious_answer_leak"
            except Exception as exc:
                row["status"] = "review_error"
                row["error_type"] = type(exc).__name__
            finally:
                runtime.close()
        report["challenges"].append(row)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--manifest", type=Path, default=Path("doc/phase-a/pilot-manifest.json"))
    parser.add_argument("--image", default=DEFAULT_IMAGE, help="immutable local Docker image ID")
    parser.add_argument("--output", type=Path, default=Path("/tmp/ctfbot-stage-a-attachment-review.json"))
    args = parser.parse_args()
    report = review(args.source_root, args.manifest, args.image)
    args.output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, args.output)
    counts: dict[str, int] = {}
    for row in report["challenges"]:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    print(json.dumps({"report": str(args.output), "challenges": len(report["challenges"]), "status_counts": counts}, ensure_ascii=False))


if __name__ == "__main__":
    main()
