"""Verify a submitted candidate in the controller without revealing the oracle."""

from __future__ import annotations

import argparse
import hmac
import json
import stat
import sys
from pathlib import Path

MAX_CANDIDATE_BYTES = 4096


def verify(oracle_path: Path, candidate: str) -> bool:
    if len(candidate.encode("utf-8")) > MAX_CANDIDATE_BYTES:
        return False
    if oracle_path.is_symlink():
        raise ValueError("oracle file must not be a symlink")
    oracle_path = oracle_path.resolve(strict=True)
    if not oracle_path.is_file() or stat.S_IMODE(oracle_path.stat().st_mode) & 0o077:
        raise ValueError("oracle file must be a private regular file")
    if stat.S_IMODE(oracle_path.parent.stat().st_mode) & 0o077:
        raise ValueError("oracle directory must be private")
    oracle = json.loads(oracle_path.read_text(encoding="utf-8"))
    expected = oracle.get("flag")
    if not isinstance(expected, str) or not expected:
        raise ValueError("oracle file has no exact-string flag")
    return hmac.compare_digest(candidate.rstrip("\r\n"), expected)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--oracle", type=Path, required=True, help="Controller-only oracle file")
    args = parser.parse_args()
    candidate = sys.stdin.read(MAX_CANDIDATE_BYTES + 1)
    try:
        accepted = verify(args.oracle, candidate)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Verifier error: {type(exc).__name__}", file=sys.stderr)
        return 2
    print("VERIFIED" if accepted else "REJECTED")
    return 0 if accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
