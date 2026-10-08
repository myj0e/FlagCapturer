# C3 live synthetic service and network acceptance

Date: 2026-10-07. Controller: non-root Linux user with Docker group access.
Daemon: Docker 29.8.1. No real provider, challenge assets, or remote CTF target
was used. A deterministic model reads the real solver command output and submits
its returned candidate through the normal controller verifier.

## Runtime changes

The service adapter now creates an internal, IPv4-only bridge with
`com.docker.network.bridge.gateway_mode_ipv4=isolated` and checks those settings
after creation. A daemon that rejects or fails to return the required profile
cannot proceed to container creation. Both containers use `--dns 127.0.0.1`
and `--dns-search .`; internal Docker service-name resolution remains available.
Service and solver images declaring anonymous volumes are rejected. Service
lifecycle evidence includes `network_profile=isolated-ipv4-no-upstream-dns-v1`.

Docker's ordinary internal bridge has a host bridge address; isolated gateway
mode removes that address ([gateway modes](https://docs.docker.com/engine/network/port-publishing/#gateway-modes)).
Custom-network DNS normally forwards external queries through host resolvers;
an explicit loopback DNS address is interpreted inside the container's namespace
([Docker DNS](https://docs.docker.com/engine/network/#dns-services)). Neither
container receives network administration capabilities or a default route.

The first real startup exposed an additional compatibility issue: Docker's
local logging driver rejected compression with `max-file=1`. The runtime now
sets `compress=false` explicitly while retaining the 1 MiB log cap.

## Reproduce

Build the authored fixture from trusted local Linux CPython, its standard
library/shared libraries, and static BusyBox. The builder uses `scratch`, does
not copy project configuration or site packages, and does not download a base
image or packages. `ldd` is run only on the trusted host interpreter/modules.

```sh
.venv/bin/python doc/phase-c/build_fixture_image.py
image=$(docker image inspect --format '{{.Id}}' ctfbot-c3-fixture:local)
PYTHONPATH=src .venv/bin/python doc/phase-c/verify_local_service.py \
  --image "$image" --output /absolute/private/new-c3-acceptance-directory
PYTHONPATH=src .venv/bin/python -m pytest -q tests
```

The acceptance directory must not exist. It retains private imported snapshots,
external oracles, run evidence, service log artifacts, generated reports, and
`acceptance.json`. Containers and networks are removed on both success and
failure, with daemon-backed absence checks. The fixture image remains local for
reproduction; remove its `ctfbot-c3-fixture:local` tag when no longer needed.

Recorded immutable image:
`sha256:4c0b5187de4f9146b628cdeb6f951f0ba8c294ab35f539a953a20f6d7b282fe6`.
Final private result: `/tmp/ctfbot-c3-acceptance-20261007-2/acceptance.json`.

## Observed network results

Two independent runs were started concurrently. All four namespaces (two
solvers and two services) passed the same probes:

| Probe | Observed result |
| --- | --- |
| Real host listener on the host's primary IPv4 address | Blocked; the controller first confirmed this listener was reachable. |
| Conventional first subnet/gateway address | Blocked; container gateway fields were empty. |
| Other run's healthy service IP and TCP port | Blocked in both directions. |
| Public IPv4 and IPv6 numerical TCP destinations | Blocked; the harness checked absence of default routes before attempting connections. |
| Embedded DNS query for `example.com` | No external answer. |
| Direct UDP DNS query to an external resolver | Blocked. |
| Each solver's own `challenge:31337` | Reachable; the expected synthetic response was received. |

Docker inspection also confirmed one network attachment, loopback DNS settings
and no published ports per container. No host firewall or Docker daemon settings
were changed.

## Observed lifecycle results

| Scenario | Final result |
| --- | --- |
| Import → readiness → command → candidate → verifier → evidence/report | `verified` |
| Cancel during agent execution | `user_cancelled` |
| Cancel during service startup | `user_cancelled` |
| Deterministic provider initialization failure after readiness | `provider_error` |
| Service readiness deadline | `error` / environment error |
| Run wall-time exhausted during startup | `budget_exhausted` |
| Service exits before readiness | `error` / environment error |

All seven cases confirmed removal of owned containers/networks, persisted
`cleanup_status=complete`, and generated reports without copying the raw flag.
Startup failure/timeout/cancellation could not initialize the model. Successful
initialization followed recorded readiness. The existing full regression suite
also passed **74 cases**, including **9 Textual TUI cases**.

## Remaining activation boundary

This closes the authored single-service fixture's live network and lifecycle
acceptance on this daemon. It does not certify malicious images, kernel escape
resistance, arbitrary host configurations or all possible network protocols.
The network permits communication between peers within the same run; the
manifest port drives readiness and agent scope, not a per-port firewall.

At the time of this second increment, default service entrypoints were disabled pending profiles and Docker timeout recovery. The third increment has delivered that controlled activation for the authored fixture; see [current activation and recovery scope](service-activation.md). Generic Compose, multiple services and additional application health checks remain later C3 expansions; C4 remote allowlists remain separate.
