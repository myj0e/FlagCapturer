"""Opt-in live C3 synthetic service acceptance. Never calls a real model.

Requires an immutable local Python fixture image; see build_fixture_image.py.
Private evidence is retained under a new --output directory.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

from ctfbot.agent.loop import RunLimits
from ctfbot.application.control import RunControl
from ctfbot.application.service import LocalChallengeService
from ctfbot.challenge.local_service import LocalServiceSpec
from ctfbot.challenge.service_fixture import SYNTHETIC_FLAG, import_service_fixture
from ctfbot.model_adapters.protocol import ToolCall, TurnResult
from ctfbot.reporting import generate_basic_report
from ctfbot.runtime.docker import validate_image_reference
from ctfbot.runtime.local_service import DockerLocalServiceRuntime
from ctfbot.runtime.docker import RuntimeErrorSafe
from ctfbot.runtime.service_recovery import (
    NETWORK_PROFILE, daemon_identity, read_private, unfinished,
)


SOLVE = ('import socket; s=socket.create_connection(("challenge",31337),timeout=1); '
         's.sendall(b"solve\\n"); print(s.recv(128).decode()); s.close()')

# Probes run inside both solver and service. No packet can leave through a
# default route: absence is checked before probing numerical external targets.
PROBE = r'''
import json, pathlib, socket, struct, sys
targets = json.loads(sys.argv[1])
routes = pathlib.Path('/proc/net/route').read_text().splitlines()[1:]
assert not any(line.split()[1] == '00000000' for line in routes), 'IPv4 default route exists'
v6 = pathlib.Path('/proc/net/ipv6_route')
if v6.exists():
    assert not any(line.split()[0] == '0'*32 and line.split()[1] == '00'
                   and int(line.split()[8],16) & 1 and not int(line.split()[8],16) & 512
                   for line in v6.read_text().splitlines()), 'IPv6 default route exists'
results = {}
for name, host, port in targets:
    try:
        with socket.create_connection((host, port), timeout=0.4): pass
    except OSError:
        results[name] = 'blocked'
    else:
        raise RuntimeError('unexpected reachable target: '+name)
query = struct.pack('!HHHHHH', 1234, 256, 1, 0, 0, 0) + b'\x07example\x03com\x00\x00\x01\x00\x01'
for host in ('127.0.0.11', '8.8.8.8'):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(0.5)
        try:
            s.sendto(query, (host,53))
            data, peer = s.recvfrom(4096)
        except OSError:
            results['dns:'+host] = 'blocked'
        else:
            ident, flags, questions, answers, authority, additional = struct.unpack('!HHHHHH',data[:12])
            assert ident == 1234 and flags & 32768 and answers == 0, 'external DNS answer received'
            results['dns:'+host] = 'no_external_answer'
print(json.dumps(results))
'''


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def docker_json(*args: str):
    return json.loads(subprocess.run(["docker", *args], capture_output=True, timeout=10, check=True).stdout)


def confirm_removed(runtime: DockerLocalServiceRuntime) -> None:
    for kind in ("container", "network"):
        result = subprocess.run(
            ["docker", kind, "ls", *(["--all"] if kind == "container" else []),
             "--filter", f"label=ctfbot.owner={runtime.owner}", "--format", "{{.ID}}"],
            capture_output=True, timeout=10, check=True,
        )
        require(not result.stdout.strip(), f"owned {kind} remained after cleanup")


def network_acceptance(output: Path, image: str) -> dict:
    workspace, _ = import_service_fixture(output / "network-fixture", image)
    spec = LocalServiceSpec.from_manifest(json.loads((workspace / "provenance.json").read_text())["local_service"])
    runtimes = [DockerLocalServiceRuntime(workspace / "input", image, spec, event_sink=lambda _: None)
                for _ in range(2)]
    result = {}
    with socket.socket() as listener, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as discovery:
        listener.bind(("0.0.0.0", 0))
        listener.listen(8)
        port = listener.getsockname()[1]
        discovery.connect(("192.0.2.1", 9))  # Local routing lookup; no UDP payload sent.
        host_ip = discovery.getsockname()[0]
        with socket.create_connection((host_ip, port), timeout=1):
            pass  # Positive control: a real listening host service exists.
        try:
            for runtime in runtimes:
                runtime.start()
            for index, runtime in enumerate(runtimes):
                network = docker_json("network", "inspect", runtime.network_name)[0]
                peer = docker_json("container", "inspect", runtimes[1-index].service_name)[0]
                peer_ip = peer["NetworkSettings"]["Networks"][runtimes[1-index].network_name]["IPAddress"]
                subnet = ipaddress.ip_network(network["IPAM"]["Config"][0]["Subnet"])
                targets = [("host", host_ip, port), ("bridge_address", str(subnet.network_address+1), port),
                           ("cross_run", peer_ip, 31337), ("public_ipv4", "1.1.1.1", 443),
                           ("public_ipv6", "2606:4700:4700::1111", 443)]
                for role, name in (("solver", runtime.container_name), ("service", runtime.service_name)):
                    container = docker_json("container", "inspect", name)[0]
                    require(container["HostConfig"]["Dns"] == ["127.0.0.1"], "unexpected DNS forwarding")
                    require(len(container["NetworkSettings"]["Networks"]) == 1, "unexpected network attachment")
                    require(not container["HostConfig"]["PortBindings"], "unexpected published ports")
                    attachment = container["NetworkSettings"]["Networks"][runtime.network_name]
                    require(not attachment.get("Gateway") and not attachment.get("IPv6Gateway"),
                            "unexpected container gateway")
                    probe = subprocess.run(["docker", "exec", name, "python3", "-c", PROBE, json.dumps(targets)],
                                           capture_output=True, timeout=10, check=True)
                    result[f"run_{index+1}_{role}"] = json.loads(probe.stdout)
                # Both runs retain their own working service endpoint.
                solved = runtime.execute(["python3", "-c", SOLVE], timeout=5)
                require(solved.exit_code == 0 and SYNTHETIC_FLAG.encode() in solved.stdout,
                        "approved endpoint is unreachable")
        finally:
            errors = []
            for runtime in runtimes:
                try:
                    runtime.close()
                    confirm_removed(runtime)
                except Exception as exc:
                    errors.append(str(exc))
            require(not errors, "network acceptance cleanup failed: " + "; ".join(errors))
    return result


def lifecycle_acceptance(output: Path, image: str, outcome: str) -> dict:
    workspace, oracle = import_service_fixture(output / outcome, image, authorize_model_data=True)
    if outcome in {"readiness_timeout", "run_timeout", "service_exit"}:
        path = workspace / "provenance.json"
        manifest = json.loads(path.read_text())
        manifest["local_service"]["argv"] = (["/bin/sh", "-c", "exit 7"] if outcome == "service_exit"
                                               else ["/bin/sleep", "30"])
        manifest["local_service"]["startup_timeout_seconds"] = 2 if outcome == "readiness_timeout" else 15
        path.chmod(0o600)
        path.write_text(json.dumps(manifest), encoding="utf-8")
        path.chmod(0o444)
    control = RunControl()
    runtimes = []
    provider_calls = []

    def factory(root, solver_image, spec, sink):
        def record(details):
            sink(details)
            if outcome == "cancel_startup" and details["state"] == "service_started":
                control.cancel()
        runtime = DockerLocalServiceRuntime(root, solver_image, spec, event_sink=record,
                                             recovery_root=output / "lifecycle-recovery-state",
                                             expected_daemon=daemon_identity())
        runtimes.append(runtime)
        return runtime

    class Model:
        def run_turn(self, prompt, tools, on_tool_call, *, timeout):
            if outcome == "cancel":
                control.cancel()
                return TurnResult("")
            reply = on_tool_call(ToolCall("command_run", {"argv": ["python3", "-c", SOLVE]}))
            payload = json.loads(reply.content)
            require(payload["exit_code"] == 0, "solver command failed")
            candidate = payload["stdout"].strip()
            require(candidate == SYNTHETIC_FLAG, "unexpected service response")
            on_tool_call(ToolCall("candidate_submit", {"candidate": candidate}))
            return TurnResult("", tool_calls=2)

        def close(self):
            pass

    def make_model():
        provider_calls.append(True)
        if outcome == "provider_failure":
            raise RuntimeError("synthetic provider initialization failure")
        return Model()

    service = LocalChallengeService(model_factory=make_model, model_metadata={"provider": "synthetic"},
                                    service_runtime_factory=factory)
    result = service.run(workspace, oracle, image, output / "runs",
                         RunLimits(max_turns=1, wall_time_seconds=2 if outcome == "run_timeout" else 30,
                                   tool_timeout_seconds=5), control=control)
    expected = {"solve": "verified", "cancel": "user_cancelled", "provider_failure": "provider_error",
                "readiness_timeout": "error", "run_timeout": "budget_exhausted",
                "service_exit": "error", "cancel_startup": "user_cancelled"}[outcome]
    require(result.status == expected, f"{outcome}: expected {expected}, got {result.status}")
    require(not result.cleanup_errors, "run reported cleanup failure")
    for runtime in runtimes:
        confirm_removed(runtime)
    records = [json.loads(line) for line in (Path(result.run_dir) / "events.jsonl").read_text().splitlines()]
    require(any(e.get("state") == "cleaned" for e in records), "missing cleanup evidence")
    if outcome in {"readiness_timeout", "run_timeout", "service_exit", "cancel_startup"}:
        require(not provider_calls, "provider initialized before readiness")
    else:
        healthy = next(e["seq"] for e in records if e.get("state") == "healthy")
        if outcome != "provider_failure":
            initialized = next(e["seq"] for e in records if e["event_type"] == "provider_initialized")
            require(healthy < initialized, "provider initialized before readiness")
    state = json.loads((Path(result.run_dir) / "run-state.json").read_text())
    require(state["cleanup_status"] == "complete", "persisted cleanup incomplete")
    report = generate_basic_report(Path(result.run_dir))
    require(SYNTHETIC_FLAG not in report.read_text(), "raw candidate leaked into report")
    return {"status": result.status, "run_dir": result.run_dir, "report": str(report), "removed": True}


def recovery_acceptance(output: Path, image: str) -> dict:
    workspace, _ = import_service_fixture(output / "recovery-fixture", image)
    spec = LocalServiceSpec.from_manifest(json.loads((workspace / "provenance.json").read_text())["local_service"])
    real_docker = shutil.which("docker")
    require(real_docker is not None, "Docker CLI missing")
    wrapper_dir = output / "delayed-docker"
    wrapper_dir.mkdir(mode=0o700)
    wrapper = wrapper_dir / "docker"
    wrapper.write_text(
        f"#!{sys.executable}\nimport os,subprocess,sys,time\nreal={real_docker!r}\n"
        "args=sys.argv[1:]\nkind=('network_create' if args[:2]==['network','create'] else "
        "'container_create' if args[:1]==['create'] else 'container_start' if args[:1]==['start'] else '')\n"
        "if kind and kind==os.environ.get('CTFBOT_ACCEPTANCE_DELAY_KIND'):\n"
        "    if kind=='network_create':\n        time.sleep(3)\n        os.execv(real,[real,*args])\n"
        "    result=subprocess.run([real,*args])\n    time.sleep(3)\n    sys.exit(result.returncode)\n"
        "os.execv(real,[real,*args])\n", encoding="utf-8",
    )
    wrapper.chmod(0o700)
    original_path = os.environ.get("PATH", "")
    original_delay = os.environ.get("CTFBOT_ACCEPTANCE_DELAY_KIND")
    results = {}
    try:
        os.environ["PATH"] = str(wrapper_dir) + os.pathsep + original_path
        for kind in ("network_create", "container_create", "container_start"):
            os.environ["CTFBOT_ACCEPTANCE_DELAY_KIND"] = kind
            root = output / ("recovery-" + kind)
            runtime = DockerLocalServiceRuntime(workspace / "input", image, spec, event_sink=lambda _: None,
                                                recovery_root=root, expected_daemon=daemon_identity())
            runtime._admit()
            network_args = ["network", "create", "--internal", "--ipv6=false", "--opt",
                            "com.docker.network.bridge.gateway_mode_ipv4=isolated",
                            "--label", f"ctfbot.owner={runtime.owner}", runtime.network_name]
            runtime._network_attempted = True
            try:
                if kind == "network_create":
                    args = network_args
                else:
                    runtime._docker(network_args)
                    runtime._solver_attempted = True
                    create_args = runtime._create_argv()[1:]
                    if kind == "container_create":
                        args = create_args
                    else:
                        runtime._docker(create_args)
                        args = ["start", runtime.container_name]
                try:
                    runtime._docker(args, timeout=0.001)
                except subprocess.TimeoutExpired:
                    pass
                else:
                    raise RuntimeError("delayed request did not time out")
                try:
                    runtime.close()
                except RuntimeErrorSafe:
                    pass
                require(bool(unfinished(root)), "absence was mistaken for acknowledged cleanup")
                denied = DockerLocalServiceRuntime(workspace / "input", image, spec, event_sink=lambda _: None,
                                                   recovery_root=root, expected_daemon=daemon_identity())
                try:
                    denied._admit()
                except RuntimeErrorSafe:
                    pass
                else:
                    denied.close()
                    raise RuntimeError("a pending Docker request did not block the next run")
                deadline = time.monotonic() + 10
                while unfinished(root) and time.monotonic() < deadline:
                    time.sleep(0.1)
                require(not unfinished(root), "late acknowledgement did not complete recovery")
                confirm_removed(runtime)
                results[kind] = {"status": "cleaned", "blocked_while_pending": True,
                                 "journal": str(runtime._journal)}
            finally:
                runtime.close()
    finally:
        os.environ["PATH"] = original_path
        if original_delay is None:
            os.environ.pop("CTFBOT_ACCEPTANCE_DELAY_KIND", None)
        else:
            os.environ["CTFBOT_ACCEPTANCE_DELAY_KIND"] = original_delay
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, type=validate_image_reference)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    args.output = args.output.absolute()
    args.output.mkdir(mode=0o700)
    result = {"schema_version": 2, "daemon": daemon_identity(), "network_profile": NETWORK_PROFILE,
              "service_argv": ["/usr/local/bin/python3", "/opt/ctfbot-fixture/server.py"], "service_port": 31337,
              "image": args.image, "network": network_acceptance(args.output, args.image),
              "lifecycle": {case: lifecycle_acceptance(args.output, args.image, case)
                            for case in ("solve", "cancel", "cancel_startup", "provider_failure",
                                         "readiness_timeout", "run_timeout", "service_exit")},
              "recovery": recovery_acceptance(args.output, args.image), "result": "PASS"}
    summary = args.output / "acceptance.json"
    summary.write_text(json.dumps(result, indent=2), encoding="utf-8")
    summary.chmod(0o600)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
