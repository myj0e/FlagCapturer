"""Read built-in JSON-compatible YAML metadata without a YAML dependency."""

from __future__ import annotations

import hashlib
import json
from importlib.resources import files
from pathlib import Path
from typing import Any

CATEGORIES = ("crypto", "forensics", "stego", "reverse", "pwn", "web")
ALIASES = {"cryptography": "crypto", "digital forensics": "forensics", "rev": "reverse",
           "reversing": "reverse", "binary exploitation": "pwn", "steganography": "stego"}

COMMON_GUIDANCE = """

## Shared reliability contract v2

- Keep concise public facts, hypotheses, checks and unknowns. Use claim_record for important conclusions; model-reported support is not controller proof.
- Summaries are filtered observations. Inspect their coverage and raw evidence with artifact_read/challenge_read_bytes before inferring absence. File offsets are bytes; domain-specific locators remain domain-specific.
- Record important experiments with experiment_record: actual execution observation, source byte/hash bindings, parameters and transformations. Hardcoded guesses or failed provenance checks remain hypotheses; exit 0 does not establish a claim.
- candidate_submit records a candidate and provenance without stopping on format. candidate_check can check applicable local relations, but a local pass never becomes a proof of flag correctness. Record missing checks honestly.
- Finish with run_complete as unsolved or candidate_unverified when appropriate. No known answer or guessed flag is required to end. The user confirms candidates on the competition platform.
- Each next experiment should distinguish a hypothesis. Reuse only observations bound to applicable image/input/run context; changing service responses require fresh checks. Repeated calls may be justified, but do not count tool use itself as progress.
- Start with cheap discriminating checks and available capabilities. Isolate each expensive library phase under a deadline and checkpoint; repeated failure needs changed parameters or a reason to continue, not an unbounded first phase.
- Focused reads return source_ref. Bind it with parameter_key (a key in parameters, not its value); the server verifies the bytes and includes the source observation. This does not prove your script actually used that parameter.
- Use session_read collect_seconds=30 (at most 60) to coalesce progress; a foreground timeout has already ended that command. Only a real live session is running in the background. Default tool-call count is telemetry only; wall time and output budgets still apply.
"""


def category(value: str) -> str:
    normalized = value.strip().lower()
    return ALIASES.get(normalized, normalized)


def catalog() -> dict[str, dict[str, Any]]:
    result = {}
    for name in CATEGORIES:
        item = json.loads(files("ctfbot.packs").joinpath("data", name, "pack.yaml").read_text())
        if item["schema_version"] != 1 or item["name"] != name:
            raise ValueError("unsupported built-in pack metadata")
        result[name] = item
    return result


def playbook(name: str) -> str:
    if name not in CATEGORIES:
        raise ValueError("unknown domain pack")
    return files("ctfbot.packs").joinpath("data", name, "README.md").read_text() + COMMON_GUIDANCE


def snapshot() -> dict[str, Any]:
    packs = catalog()
    for name, item in packs.items():
        item["playbook_sha256"] = hashlib.sha256(playbook(name).encode()).hexdigest()
    encoded = json.dumps(packs, sort_keys=True, separators=(",", ":")).encode()
    return {"schema_version": 1, "common_contract_version": 2, "catalog_sha256": hashlib.sha256(encoded).hexdigest(), "packs": packs}


def triage(root: Path) -> dict[str, Any]:
    """Bounded hints from filenames/magic; do not execute or parse attachments."""
    scores = dict.fromkeys(CATEGORIES, 0)
    clues: list[dict[str, str]] = []
    names = sorted(path for path in root.rglob("*") if path.is_file() and not path.is_symlink())[:64]
    extensions = {".pem": "crypto", ".b64": "crypto", ".hex": "crypto", ".pcap": "forensics",
                  ".zip": "forensics", ".tar": "forensics", ".wav": "stego", ".png": "stego",
                  ".ppm": "stego", ".elf": "reverse", ".exe": "reverse", ".html": "web",
                  ".http": "web", ".php": "web"}
    for path in names:
        # Do not traverse a symlink ancestor even if rglob yields its descendants.
        if any((root / parent).is_symlink() for parent in path.relative_to(root).parents) or path.is_symlink():
            continue
        with path.open("rb") as stream:
            prefix = stream.read(4096)
        hints = []
        if path.suffix.lower() in extensions:
            hints.append(extensions[path.suffix.lower()])
        if prefix.startswith((b"\x7fELF", b"MZ")):
            hints.extend(("reverse", "pwn"))
        if prefix.startswith((b"PK\x03\x04", b"\xd4\xc3\xb2\xa1", b"\x0a\x0d\x0d\x0a")):
            hints.append("forensics")
        if prefix.startswith((b"\x89PNG", b"P6\n", b"RIFF")):
            hints.extend(("forensics", "stego"))
        if prefix.startswith(b"HTTP/"):
            hints.append("web")
        if b'"n"' in prefix and b'"e"' in prefix:
            hints.append("crypto")
        for hint in set(hints):
            scores[hint] += 1
            clues.append({"path": path.relative_to(root).as_posix(), "label": hint})
    labels = [name for name in CATEGORIES if scores[name]]
    return {"labels": labels, "confidence": "low" if not labels or len(labels) > 2 else "heuristic",
            "scores": scores, "clues": clues, "fallback": "core command/session tools remain available",
            "network_authority_changed": False}
