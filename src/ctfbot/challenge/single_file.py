"""Import one user-selected attachment as an immutable private offline snapshot.

Only bounded bytes and a short magic prefix are inspected on the controller.
The attachment is never executed during import or preview.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import uuid
from itertools import islice
from pathlib import Path


MAX_ATTACHMENT_BYTES = 32 * 1024 * 1024


def select_single_attachment(source: Path) -> Path | None:
    """Accept a file or a plain folder with exactly one attachment.

    Imported workspaces retain their existing admission and authorization.
    """
    if source.is_file() or source.is_symlink():
        return source
    if source.is_dir() and not any((source / name).exists() for name in ("TASK.md", "provenance.json", "input")):
        entries = list(islice(source.iterdir(), 2))
        if len(entries) == 1 and entries[0].is_file() and not entries[0].is_symlink():
            return entries[0]
        raise ValueError("普通题目目录需仅包含一个附件；请直接选择目标文件，或使用已导入的 workspace")
    return None


def import_single_file(source: Path, imports_root: Path, *, authorize_model_data: bool = False) -> Path:
    if source.is_symlink():
        raise ValueError("select a regular challenge file, not a symbolic link")
    flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(source, flags)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("challenge attachment must be a regular file")
        if not 0 < before.st_size <= MAX_ATTACHMENT_BYTES:
            raise ValueError("challenge attachment must contain 1 byte to 32 MiB")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            data = stream.read(MAX_ATTACHMENT_BYTES + 1)
        after = os.fstat(descriptor)
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns) or len(data) != before.st_size:
            raise ValueError("challenge file changed during import; preview it again")
    finally:
        os.close(descriptor)
    digest = hashlib.sha256(data).hexdigest()
    name = source.name
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", name):
        name = "attachment.bin"
    executable = data.startswith((b"\x7fELF", b"MZ"))
    category = "reverse" if executable else "misc"
    challenge_id = "local-" + digest[:24]
    metadata_hash = hashlib.sha256(json.dumps({"name": name, "sha256": digest, "bytes": len(data)}, sort_keys=True).encode()).hexdigest()
    task = (f"Solve the local CTF challenge using the attached file `{name}`. "
            "Inspect the file and develop a reproducible solution with the registered tools. "
            "This is an offline attachment: no external service or host execution is authorized.\n")
    provenance = {
        "challenge_id": challenge_id, "source_commit": "local-file-import-v1",
        "challenge_metadata_sha256": metadata_hash, "category": category,
        "formal_admission": "admitted", "import_mode": "static_files_only",
        "authorization_scope": "private local evaluation; do not redistribute challenge assets",
        "model_data_authorized": authorize_model_data,
        "model_data_authorization_basis": (f"TUI user explicitly authorized this local file and tool outputs for model transfer; input sha256={digest}"
                                            if authorize_model_data else None),
        "task_sha256": hashlib.sha256(task.encode()).hexdigest(),
        "input_files": [{"path": name, "sha256": digest, "bytes": len(data)}],
        "local_import": {"version": 1, "source_path": str(source.absolute()), "source_sha256": digest,
                         "network_authorized": False},
    }
    imports_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if imports_root.is_symlink() or not imports_root.is_dir() or stat.S_IMODE(imports_root.stat().st_mode) & 0o077:
        raise ValueError("local imports directory must be private and must not be a symbolic link")
    workspace = imports_root / (challenge_id + "-" + uuid.uuid4().hex[:12])
    workspace.mkdir(mode=0o700)
    input_root = workspace / "input"
    input_root.mkdir(mode=0o700)
    attachment = input_root / name
    attachment.write_bytes(data)
    attachment.chmod(0o555 if executable else 0o444)
    for filename, content in (("TASK.md", task), ("provenance.json", json.dumps(provenance, indent=2))):
        target = workspace / filename
        target.write_text(content, encoding="utf-8")
        target.chmod(0o444)
    input_root.chmod(0o555)
    return workspace
