"""Requested remote scope; this manifest alone never grants network access."""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


def utc_time(value: Any) -> datetime:
    if not isinstance(value, str) or len(value) > 64 or not value.endswith("Z"):
        raise ValueError("authorization times must be ISO-8601 UTC strings ending in Z")
    result = datetime.fromisoformat(value[:-1] + "+00:00")
    if result.utcoffset() != timezone.utc.utcoffset(result):
        raise ValueError("authorization times must use UTC")
    return result


@dataclass(frozen=True, slots=True)
class RemoteSpec:
    host: str
    port: int
    protocol: str
    not_before: datetime
    not_after: datetime
    authorization_basis: str

    @property
    def endpoint(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"tcp://{host}:{self.port}"

    @classmethod
    def from_manifest(cls, value: Any) -> RemoteSpec:
        fields = {"schema_version", "host", "port", "protocol", "not_before", "not_after",
                  "runtime_authorized", "authorization_basis"}
        if not isinstance(value, dict) or set(value) != fields:
            raise ValueError("remote scope must contain exactly the version-1 fields")
        if type(value["schema_version"]) is not int or value["schema_version"] != 1 or value["runtime_authorized"] is not True:
            raise ValueError("remote scope requires version 1 and explicit runtime authorization")
        host = value["host"]
        if not isinstance(host, str) or len(host) > 253 or host != host.lower() or host.endswith("."):
            raise ValueError("host must be a canonical lowercase hostname or IP address")
        try:
            address = ipaddress.ip_address(host)
            if str(address) != host or "%" in host:
                raise ValueError("IP address must be canonical and have no zone identifier")
        except ValueError:
            if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host) or any(
                not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                for label in host.split(".")
            ):
                raise ValueError("invalid remote host") from None
        if type(value["port"]) is not int or not 1 <= value["port"] <= 65535 or value["protocol"] != "tcp":
            raise ValueError("first remote increment supports one TCP port only")
        before, after = utc_time(value["not_before"]), utc_time(value["not_after"])
        if before >= after:
            raise ValueError("authorization time window is empty")
        basis = value["authorization_basis"]
        if not isinstance(basis, str) or not basis.strip() or len(basis) > 4096:
            raise ValueError("remote runtime authorization requires a recorded basis")
        return cls(host, value["port"], "tcp", before, after, basis)
