# Docker mount and limits spike

This is a benign Stage A smoke recipe, not a CTF runtime or security certification. It uses a locally available **static** BusyBox binary and a `scratch` base, so the image build does not need to pull a base image.

## Reproduce on Linux

From the repository root, confirm that `busybox` is static (`file /bin/busybox`), then create a temporary build context:

```sh
mkdir -p /tmp/ctfbot-docker-smoke/input /tmp/ctfbot-docker-smoke/work
cp "$(command -v busybox)" /tmp/ctfbot-docker-smoke/busybox
cp doc/phase-a/spikes/docker_runtime/Dockerfile /tmp/ctfbot-docker-smoke/Dockerfile
cp doc/phase-a/spikes/docker_runtime/fixture.txt /tmp/ctfbot-docker-smoke/input/fixture.txt
docker build --pull=false --tag ctfbot-stage-a-runtime:local /tmp/ctfbot-docker-smoke
```

Run one synthetic container. Substitute the local UID/GID for `1000:1000` if needed:

```sh
docker run --rm --pull=never --network none --read-only \
  --memory=64m --cpus=0.25 --pids-limit=16 --user=1000:1000 \
  --cap-drop=ALL --security-opt=no-new-privileges \
  --mount type=bind,src=/tmp/ctfbot-docker-smoke/input,dst=/input,readonly \
  --mount type=bind,src=/tmp/ctfbot-docker-smoke/work,dst=/work \
  ctfbot-stage-a-runtime:local sh -c 'set -eu; \
    test "$(cat /input/fixture.txt)" = "synthetic-stage-a-input"; \
    if printf blocked > /input/deny.txt; then exit 10; fi; \
    if printf blocked > /rootfs-deny.txt; then exit 11; fi; \
    printf "workspace-write-ok\n" > /work/result.txt; \
    test "$(cat /work/result.txt)" = "workspace-write-ok"; \
    echo "PASS: input readable; input/rootfs writes denied; workdir writable"'
```

Check that the temporary container was removed and the output is on the host:

```sh
docker ps -a --filter ancestor=ctfbot-stage-a-runtime:local
cat /tmp/ctfbot-docker-smoke/work/result.txt
```

Then remove the locally built image and temp context when finished:

```sh
docker image rm ctfbot-stage-a-runtime:local
rm -rf /tmp/ctfbot-docker-smoke
```

## Run record

On 2026-09-29, the static BusyBox image was built locally from `scratch` and run once with the constraints above. Input and rootfs writes failed with `Read-only file system`; a write to the mounted workdir succeeded and was read back from the host. A filtered `docker ps -a` showed no remaining container. This did not verify host-network egress, DNS/redirect scope, resource-exhaustion behavior, PTY/session lifecycle, cleanup after forced termination, or resistance to malicious challenge images.
