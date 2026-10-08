# Stage C implementation record

Stage C1, the first C2 interactive-session increment, and the authored C3 single-service loop are delivered within their recorded scopes. C4's first controlled TCP loop now passes all 28 required synthetic live cases plus one HTTP path check, including reviewed CLI/TUI entrypoints. The temporary endpoint is stopped and no default remote profile is installed. See [C4 live acceptance](remote-acceptance.md). Older sections below describe earlier increments.

## C1: run lifecycle

Status: lifecycle implementation and synthetic backend acceptance complete. This increment keeps the Stage B local attachment, offline runtime profile, authorization checks, and shared application service as its execution boundary.

Each admitted run now has a private `run-state.json` snapshot, atomically updated through preparing, starting, running, stopping, and terminal states. Transition events and all run evidence carry the run and challenge IDs. Provider initialization failures, runtime startup failures, timeouts, cancellation, cleanup failures, and the cleanup outcome are recorded separately from the solve result. A report can be generated while a run is incomplete and links to evidence already written; an incomplete final JSONL fragment is called out and omitted.

Automatic retry and resume are deliberately disabled. The state and report direct the operator to inspect partial evidence and start a new run; no second provider request is issued implicitly. Batch runs now defer model creation to the per-run lifecycle, so provider startup failures receive the same record.

Synthetic acceptance command (covers C1 lifecycle, C2 session integration, and the Stage B application path):

```sh
PYTHONPATH=src pytest -q tests/test_stage_b_control_report.py tests/test_stage_b_application.py tests/test_stage_a_runner.py
```

Result: 37 passed. The Docker runtime adapter tests mock Docker process creation, verify both attached streams use a real controller-side PTY, and run a local interactive child through that PTY to check terminal detection plus cross-turn input/output. This matters because Docker CLI checks that attached stdin is a terminal when `--tty` is requested; Docker's `--tty` option allocates the pseudo-TTY inside the container ([Docker CLI reference](https://docs.docker.com/reference/cli/docker/container/exec), [Docker CLI implementation](https://github.com/docker/cli/blob/master/cli/streams/in.go)). `python3 -m compileall -q src tests` and `git diff --check` are also part of this increment's local acceptance.

The original increment used no real provider, Docker daemon/container, or challenge attachment. On 2026-10-07, the project `.venv` was confirmed to contain Textual 8.2.8: the earlier missing-dependency diagnosis had checked system Python. The full existing suite now passes 74 cases, including 9 TUI cases. Real Docker PTY round trips, close, cancellation, shortened idle/total watchdogs and container deletion also passed; see [live session acceptance](docker-session-acceptance.md). This completes the first C2 adapter smoke, with broader debugger/REPL workflow acceptance still separate.

## C2: bounded interactive sessions

The offline Docker runtime can now create up to four run-local interactive sessions with a canonical session ID. The agent can start an argv-based process, send bounded UTF-8 text, read output in bounded chunks, and close the session. Each session has a 60-second idle limit, a 10-minute total limit, and a 1 MiB output cap. Input/output and state changes are written to a private JSONL transcript with evidence references; reports expose transcript paths without copying raw terminal contents.

The Docker CLI receives a controller-side PTY so its `--tty` check succeeds and terminal programs receive interactive behavior. If graceful close fails, or an idle/total/output limit is reached, the adapter kills the run container to ensure the process cannot remain behind. This also ends any other process in the same run. Session tools are only registered for a runtime that implements the full session API; this increment does not enable networking, service challenges, or privileged GDB capabilities.

Remaining Stage C work:

- C3: additional reviewed service profiles, broader protocol health checks and Compose/multiple-service expansion as needed; the authored single-service profile and acknowledged-timeout recovery increment are delivered.
- C4: first controlled TCP loop and reviewed CLI/TUI activation accepted on an authored short-lived endpoint; TLS/UDP/multiple targets and other network modes remain separate expansions.

## C3: first local TCP service increment

Version-1 service configuration, private synthetic import, candidate Docker service lifecycle, readiness before provider initialization, bounded log artifacts, reports, and default-entrypoint blocking are implemented. The second increment requires isolated gateway mode and disables external DNS forwarding; its authored fixture passed live network/lifecycle acceptance, without claiming general hostile-image isolation. See [C3 implementation and remaining acceptance](local-service.md).

```sh
PYTHONPATH=src pytest -q tests/test_stage_c_service.py tests/test_stage_b_control_report.py tests/test_stage_b_application.py tests/test_stage_a_runner.py
```

Result: 65 passed (28 C3 cases plus the prior 37). C3 tests fake Docker operations and solver command execution; log capture uses a local synthetic subprocess. They cover import/authorization, disallowed config/image/network, readiness/verification/report, cancellation during startup and agent execution, provider failure, readiness and run deadlines, resource ownership, daemon failure, cleanup failure, and evidence failure. No Docker service, host network endpoint, real model, or real challenge was used. The subsequent 2026-10-07 regression with project `.venv` also passed all 9 TUI cases. This does not constitute live C3 service acceptance.

## C3: real single-service acceptance

On 2026-10-07 a fixture image was built from trusted local CPython/static BusyBox without image pulls. Two concurrent runs passed host/gateway, cross-run, IPv4/IPv6 egress and DNS negative probes from both solver and service; own-service solve requests succeeded. Seven real lifecycle cases (solve, two cancellation points, provider failure, two startup deadlines and early service exit) wrote evidence/reports and confirmed removal. A live logging-driver incompatibility was fixed by explicitly disabling compression with `max-file=1`. Existing regression remains 74 passed. See [acceptance and remaining activation boundary](local-service-acceptance.md). The deterministic model makes no real provider request.

## C3: reviewed activation and durable recovery

The default application now supplies a reviewed factory to TUI and `solve`. A private controller profile pins the authored fixture's service/solver images, argv, port, acceptance hash and local Docker identity. Unconfigured/mismatched profiles remain blocked. Create/start intent is written before an independent acknowledgement worker sends the Docker request; foreground timeouts retain the intent, block admission and allow late acknowledged ownership-checked cleanup. Lost acknowledgement remains quarantined rather than being inferred from absence. A single service run/recovery holds an admission lock per configuration directory.

Current v2 live acceptance reran the network probes and seven lifecycle cases and passed delayed network create, container create and container start recovery. A deterministic model completed the actual `solve` CLI; image/command/port mismatches were refused. A synthetic-only default profile is installed locally. The earlier 74-case unit/TUI suite was not rerun in this increment. See [activation, operator commands and exact scope](service-activation.md). C4 is the next planned mode increment.

## C4: candidate controlled TCP connector

Strict remote scope, a separate private controller grant with an immutable solver image and pinned IP, a bounded TCP exchange tool, cancellation/expiry handling, evidence/report/TUI summaries and a synthetic importer are implemented. The solver retains `--network=none`; the connector never performs DNS lookup, redirect or proxy processing. Default TUI/solve remote execution remains blocked. Only Python compilation and whitespace checks were performed in this increment; no tests, endpoint connections or real provider calls were run. A second increment implements durable single-solver ownership, independent Docker create/start acknowledgement, pending-admission blocking, crash recovery, reviewed receipt/grant activation and shared TUI/solve wiring. No remote profile has been installed. Live boundary/lifecycle/recovery and entrypoint acceptance remain required. See [C4 implementation and next increment](remote-targets.md).
