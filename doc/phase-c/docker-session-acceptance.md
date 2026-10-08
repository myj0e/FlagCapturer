# C2 real Docker session acceptance

On 2026-10-07 the controller could connect to Docker 29.8.1 with its existing
`docker` supplementary group. The project virtual environment already contained
Textual 8.2.8; earlier reports of a missing dependency had checked system Python.
Pytest 9.1.1 was installed into `.venv` for this acceptance.

## Reproduce

The opt-in [acceptance script](verify_docker_sessions.py) requires an immutable
local image with `/bin/sh` and `/bin/sleep`. It does not pull images or call a
model. A minimal image can be built from the host's **static** BusyBox:

```sh
file /usr/bin/busybox  # Confirm it is statically linked.
context=$(mktemp -d /tmp/ctfbot-c2-image.XXXXXX)
mkdir "$context/bin"
cp /usr/bin/busybox "$context/bin/busybox"
ln -s busybox "$context/bin/sh"
ln -s busybox "$context/bin/sleep"
printf 'FROM scratch\nCOPY bin/ /bin/\n' > "$context/Dockerfile"
docker build --pull=false --network=none -t ctfbot-c2-session-smoke:local "$context"
rm -r "$context"
image=$(docker image inspect --format '{{.Id}}' ctfbot-c2-session-smoke:local)
PYTHONPATH=src .venv/bin/python doc/phase-c/verify_docker_sessions.py --image "$image"
PYTHONPATH=src .venv/bin/python -m pytest -q tests
```

Recorded immutable image:
`sha256:ef45c1ca29444594bfe9fa3edc5295596c16968043fb6b91b6413b809d42c7df`.
The old Stage A image selected initially contained only `/bin/busybox`, so it
could not start the current adapter's `/bin/sh` entrypoint. Its failed-start
container was removed before building the dedicated image above.

## Results

| Case | Observed result |
| --- | --- |
| PTY and two input/output rounds | Both container streams were terminals; both replies reached the reader and output callback; EOF closed the process. |
| Cancellation | Session reported `cancelled`; container was removed. |
| Idle watchdog | Session reported `idle_timeout` with `timed_out=true`; container was removed. |
| Total watchdog | Session reported `total_timeout` with `timed_out=true`; container was removed. |

The harness shortens each watchdog to 0.5 seconds using the session's internal
condition. Production limits remain 60 seconds idle and 600 seconds total. Each
case uses a separate offline container, closes it in `finally`, and checks absence
with a successful Docker container-list request. A daemon error fails acceptance.
The transcript callback is checked in memory; existing synthetic integration
tests cover its persistence through tool events and reports.

Live cancellation exposed a race: killing the container before marking sessions
cancelled allowed PTY EOF to record `exited`. The runtime now records session
cancellation before killing the container.

Full existing regression suite: **74 passed**, including **9 Textual TUI cases**.
No actual model, CTF attachment, local service, or remote target was used. This
closes the first C2 adapter's live session smoke, not acceptance of all GDB/REPL
workflows or malicious-image isolation. C3 still needs runtime-enforced host,
DNS, public-egress and cross-run isolation plus live service lifecycle acceptance;
default service entrypoints remain disabled. C4 remains future work.
