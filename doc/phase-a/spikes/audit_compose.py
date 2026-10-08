"""Summarize CTFTiny Compose risks without printing environment values."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError as exc:  # pragma: no cover - local spike dependency
    raise SystemExit("This read-only spike requires PyYAML in the local Python environment.") from exc


def parse_mount(value: Any) -> tuple[str, str, str] | None:
    if isinstance(value, str):
        parts = value.split(":")
        if len(parts) < 2:
            return None
        return parts[0], parts[1], parts[2] if len(parts) > 2 else ""
    if isinstance(value, dict):
        return str(value.get("source", "")), str(value.get("target", "")), "ro" if value.get("read_only") else "rw"
    return None


def audit_compose(source_root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    reports: list[dict[str, Any]] = []
    for challenge in manifest.get("challenges", []):
        if not challenge.get("compose_declared"):
            continue
        source_path = Path(str(challenge["source_path"]))
        compose_names = {
            item.get("path")
            for item in challenge.get("files", [])
            if isinstance(item, dict)
            and isinstance(item.get("path"), str)
            and item.get("path", "").lower() in {"docker-compose.yml", "compose.yml", "compose.yaml"}
        }
        if len(compose_names) != 1:
            reports.append({
                "id": challenge["id"],
                "status": "review_required_compose_file_count",
                "compose_files": sorted(compose_names),
                "safe_to_run_as_is": False,
            })
            continue
        compose_path = source_root / source_path / next(iter(compose_names))
        if compose_path.is_symlink() or compose_path.stat().st_size > 1024 * 1024:
            raise ValueError(f"unsafe Compose file path/size: {challenge['id']}")
        config = yaml.safe_load(compose_path.read_text(encoding="utf-8")) or {}
        risks: set[str] = set()
        services: list[dict[str, Any]] = []
        for name, service in (config.get("services") or {}).items():
            images: list[str] = []
            if isinstance(service.get("image"), str):
                images.append(service["image"])
            build = service.get("build")
            build_context = build.get("context", ".") if isinstance(build, dict) else build
            mounts: list[dict[str, str]] = []
            for raw_mount in service.get("volumes", []) or []:
                parsed = parse_mount(raw_mount)
                if not parsed:
                    risks.add("unparsed_mount")
                    continue
                source, target, mode = parsed
                mounts.append({"source": source, "target": target, "mode": mode})
                if source == "/var/run/docker.sock" or target == "/var/run/docker.sock":
                    risks.add("docker_socket_mount")
                if source.startswith("/") and source != "/var/run/docker.sock":
                    risks.add("host_path_mount")
            if service.get("privileged") is True:
                risks.add("privileged_container")
            for key, risk in (
                ("network_mode", "custom_network_mode"),
                ("pid", "custom_pid_namespace"),
                ("ipc", "custom_ipc_namespace"),
                ("devices", "host_device_access"),
                ("cap_add", "added_capabilities"),
            ):
                if service.get(key):
                    risks.add(risk)
            ports: list[dict[str, str | None]] = []
            for port in service.get("ports", []) or []:
                if isinstance(port, dict):
                    host_ip = str(port.get("host_ip")) if port.get("host_ip") else None
                    ports.append({
                        "host_ip": host_ip,
                        "published": str(port.get("published")) if port.get("published") else None,
                        "target": str(port.get("target")) if port.get("target") else None,
                    })
                    if host_ip not in {"127.0.0.1", "::1"}:
                        risks.add("published_port_without_loopback_binding")
                elif isinstance(port, (str, int)):
                    ports.append({"host_ip": None, "published": str(port), "target": None})
                    risks.add("published_port_without_loopback_binding")
            services.append({
                "name": str(name),
                "images": images,
                "build_context": str(build_context) if build_context else None,
                "mounts": mounts,
                "ports": ports,
                "environment_keys": sorted(service.get("environment", {}).keys())
                if isinstance(service.get("environment"), dict)
                else [],
            })
            if any("@sha256:" not in image for image in images):
                risks.add("mutable_image_reference")

        networks = []
        for name, network in (config.get("networks") or {}).items():
            network = network or {}
            external = network.get("external", False)
            internal = network.get("internal", False)
            networks.append({
                "name": str(name),
                "external": bool(external),
                "internal": bool(internal),
                "driver": network.get("driver"),
            })
            if external:
                risks.add("shared_external_network")
            if not internal:
                risks.add("network_not_internal")

        reports.append({
            "id": challenge["id"],
            "compose_file": (source_path / next(iter(compose_names))).as_posix(),
            "services": services,
            "networks": networks,
            "risks": sorted(risks),
            "safe_to_run_as_is": not risks,
        })
    return {
        "schema_version": 1,
        "source_commit": manifest.get("source_commit"),
        "environment": "read-only PyYAML parse; no image pulls or containers started",
        "reports": reports,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    result = audit_compose(args.source_root.resolve(strict=True), manifest)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Audited {len(result['reports'])} Compose challenge candidates; no services started.")
    for report in result["reports"]:
        print(f"{report['id']}: safe_to_run_as_is={report.get('safe_to_run_as_is', False)} risks={report.get('risks', ['compose_file_count'])}")


if __name__ == "__main__":
    main()
