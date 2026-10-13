"""Persist agent-authored Markdown before committing run completion."""
from __future__ import annotations

import os
import tempfile


MAX_WRITEUP_BYTES = 12 * 1024


def save_writeup(evidence, markdown: str) -> dict:
    """Atomically write the readable file and retain its evidence identity."""
    if not isinstance(markdown, str) or not markdown.strip():
        raise ValueError("writeup must contain the agent's Markdown solution summary")
    data = markdown.encode("utf-8")
    if len(data) > MAX_WRITEUP_BYTES:
        raise ValueError("writeup exceeds 12 KiB; keep a concise reproducible solution")
    target = evidence.run_dir / "writeup.md"
    if target.is_symlink() or (target.exists() and not target.is_file()):
        raise PermissionError("writeup destination must be a regular file")
    artifact = evidence.write_artifact(data, media_type="text/markdown; charset=utf-8")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=evidence.run_dir, prefix=".writeup-", delete=False) as stream:
            temporary = stream.name
            os.fchmod(stream.fileno(), 0o600)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if temporary is not None and os.path.exists(temporary):
            os.unlink(temporary)
    evidence.append("writeup_saved", path="writeup.md", writeup_evidence=artifact)
    return artifact
