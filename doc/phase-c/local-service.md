# C3: local service increments

Status: the first increment closed the single-service path with injected mocks. The second increment passed real Docker network isolation and seven lifecycle paths using the authored TCP fixture and a deterministic model; see [live acceptance](local-service-acceptance.md). The third increment now conditionally enables the authored fixture in TUI/solve through a private reviewed profile and durable timeout recovery; see [activation](service-activation.md). Without a matching profile, service execution remains blocked.

## Configuration and entrypoints

An imported snapshot uses `import_mode=local_service` and a `local_service` object inside its read-only `provenance.json`. Its version-1 fields are:

```json
{
  "schema_version": 1,
  "image": "repository/service@sha256:<64 lowercase hex digits>",
  "argv": ["/usr/local/bin/python3", "/opt/ctfbot-fixture/server.py"],
  "port": 31337,
  "startup_timeout_seconds": 15,
  "runtime_authorized": true,
  "authorization_basis": "Recorded permission for this exact local fixture"
}
```

Immutable local `sha256:<image ID>` references are also accepted. The executable must be absolute, argv is capped at 16 KiB, the TCP port is 1024–65535, and startup is capped at 30 seconds and the remaining run wall-time. Unknown fields are rejected. Config cannot specify host ports, mounts, network mode, capabilities, build instructions, environment variables, or Compose documents. Image-declared anonymous volumes in either service or solver images are rejected by the candidate adapter. Adding a service object to an offline import is rejected.

`LocalChallengeService.preview` shows the endpoint and the live-mode block independently of model-data authorization. `run_baseline` also enforces the block, so the headless path cannot bypass it. The default Codex application supplies a reviewed `service_runtime_factory`. `ctfbot service approve` activates only complete current v2 acceptance for the authored fixture; `--service-profile` selects an alternative private controller profile. Direct backend calls without a factory remain blocked. Model-data authorization is checked independently.

## Candidate runtime and evidence

The candidate adapter creates a unique internal IPv4-only bridge with isolated gateway mode and no external DNS forwarding, a service container aliased as `challenge`, and a solver container. No ports are published. Both containers use read-only roots, non-root users, dropped capabilities, no-new-privileges, fixed read-only challenge input mounts, and bounded tmpfs storage. Combined container caps are 2 CPUs, 3 GiB memory with swap disabled, and 192 PIDs. Images must already be present locally; no automatic build or pull occurs.

TCP readiness is probed from the solver container with Python 3. A successful TCP connection is the first fixture's health signal; it does not validate a general application protocol. Runtime failure or a readiness timeout cannot initialize the model. Service transitions, image/endpoint, generated resource names and failure types are associated with the run. At teardown, the last 100 service log lines are drained with a 3-second process timeout and a 64 KiB retained-output cap into a private artifact. Docker's local service log is capped at 1 MiB with compression explicitly disabled for the single-file configuration. Reports link raw log artifacts without copying their contents.

Cleanup checks resource ownership labels, removes solver/service/network resources, and confirms absence. Cancellation has the same teardown. Failed removal or an unavailable daemon is recorded as cleanup failure, with generated names retained for inspection. Evidence-write failures do not skip resource-removal attempts. Reviewed runtime requests persist creation/start intent and use independent acknowledgement workers. Timeouts retain unconfirmed intent and block new runs; acknowledged late completion triggers ownership-checked recovery. A dead worker or lost acknowledgement remains quarantined. See [recovery semantics](service-activation.md).

## Synthetic fixture

The authored server and candidate Dockerfile are in [fixtures/local-service](fixtures/local-service). The fixture accepts `solve\n` and returns a known synthetic flag. The oracle is created outside the agent workspace. No real CTF data is included.

To prepare a private snapshot without contacting Docker or a model:

```sh
PYTHONPATH=src python -m ctfbot.challenge.service_fixture \
  --output /absolute/private/new-fixture-directory \
  --image 'repository/service@sha256:<digest>'
```

The output directory must not already exist. Model-data authorization defaults to false; `--authorize-model-data` records explicit authorization for synthetic inputs only. It neither starts a model nor enables live services. The original Dockerfile requires a reviewed digest-pinned Python base. The second increment also supplies a [local scratch builder](build_fixture_image.py), using trusted host CPython and static BusyBox without downloads; a fixture image was built and run during [live acceptance](local-service-acceptance.md).

## Acceptance and remaining work

The initial combined backend suite passed 65 cases, including 28 C3 cases, with mocked Docker/network operations. On 2026-10-07, the complete existing suite passed 74 cases including 9 TUI cases. C2 live session acceptance passed separately ([record](docker-session-acceptance.md)). The second C3 increment passed real single-service solve/verification/report, agent and startup cancellation, provider failure, readiness and run deadlines, early service exit, and confirmed resource deletion. Two concurrent runs passed host, conventional gateway, cross-run, public IPv4/IPv6 and DNS probes from both service and solver; their own approved endpoints remained reachable. See [C3 live acceptance and reproduction](local-service-acceptance.md).

Docker ordinary `--internal` networking does not remove the host bridge address; the adapter now requires isolated gateway mode ([Docker reference](https://docs.docker.com/engine/network/port-publishing/#gateway-modes)). This fixture-specific acceptance does not activate arbitrary images or certify a general host boundary. The third increment has added reviewed profile activation, a daemon-bound recovery journal, three delayed-request recovery acceptance cases and actual headless CLI activation; see [record and usage](service-activation.md). The prior 74-case unit/TUI regression was not rerun in the third increment. C4 remote scope is next. General Compose import, multiple services and protocol-specific health checks remain later C3 expansions.
