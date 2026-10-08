"""Versioned, deliberately narrow configuration for the first local service fixture."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class LocalServiceSpec:
    image: str
    argv: tuple[str, ...]
    port: int
    startup_timeout_seconds: int
    authorization_basis: str

    @property
    def endpoint(self) -> str:
        return f"challenge:{self.port}"

    @classmethod
    def from_manifest(cls, value: Any) -> LocalServiceSpec:
        from ctfbot.runtime.docker import validate_image_reference

        required = {
            "schema_version", "image", "argv", "port", "startup_timeout_seconds",
            "runtime_authorized", "authorization_basis",
        }
        if not isinstance(value, dict) or set(value) != required:
            raise ValueError("local_service must contain exactly the supported version-1 fields")
        if type(value["schema_version"]) is not int or value["schema_version"] != 1:
            raise ValueError("unsupported local_service schema version")
        validate_image_reference(value["image"])
        argv = value["argv"]
        if not isinstance(argv, list) or not 1 <= len(argv) <= 64 or any(
            not isinstance(item, str) or "\x00" in item for item in argv
        ):
            raise ValueError("service argv must contain 1 to 64 NUL-free strings")
        if not argv[0].startswith("/") or sum(len(item.encode("utf-8")) for item in argv) > 16 * 1024:
            raise ValueError("service executable must be absolute and argv must fit in 16 KiB")
        port = value["port"]
        if type(port) is not int or not 1024 <= port <= 65535:
            raise ValueError("service port must be an unprivileged TCP port")
        timeout = value["startup_timeout_seconds"]
        if type(timeout) is not int or not 1 <= timeout <= 30:
            raise ValueError("service startup timeout must be 1 to 30 seconds")
        basis = value["authorization_basis"]
        if value["runtime_authorized"] is not True or not isinstance(basis, str) or not basis.strip():
            raise ValueError("local service execution requires a recorded authorization basis")
        if len(basis.encode("utf-8")) > 4096:
            raise ValueError("service authorization basis exceeds 4 KiB")
        return cls(value["image"], tuple(argv), port, timeout, basis)
