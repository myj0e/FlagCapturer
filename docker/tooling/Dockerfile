ARG BASE_IMAGE=python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258
FROM ${BASE_IMAGE}
LABEL org.ctfbot.tooling.profile="general-v2"
ARG YAFU_VERSION=3.1.9
ARG YAFU_COMMIT=54c5e8d4c21c8c994421d0c95c16aa6f3bd0280a
ENV CTFBOT_YAFU_VERSION=${YAFU_VERSION} CTFBOT_YAFU_COMMIT=${YAFU_COMMIT}
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PWNLIB_NOTERM=1 TERM=xterm
RUN export DEBIAN_FRONTEND=noninteractive && apt-get update && \
    apt-get install -y --no-install-recommends \
      bash coreutils findutils grep sed gawk file binutils gdb gcc g++ make \
      libc6-dev libc6-i386 lib32stdc++6 gcc-multilib g++-multilib \
      strace ltrace patchelf nasm xxd bsdextrautils busybox-static \
      zip unzip p7zip-full xz-utils bzip2 zstd tar jq openssl \
      curl wget netcat-openbsd socat tcpdump tshark \
      libimage-exiftool-perl binwalk steghide foremost qemu-user \
      nodejs npm ruby perl default-jre-headless ca-certificates git \
      pkg-config libffi-dev libssl-dev libgmp-dev libecm1-dev && \
    git clone --depth 1 --branch "v${YAFU_VERSION}" https://github.com/bbuhrow/yafu.git /tmp/yafu-src && \
    test "$(git -C /tmp/yafu-src rev-parse HEAD)" = "${YAFU_COMMIT}" && \
    cp /tmp/yafu-src/config.mk.example /tmp/yafu-src/config.mk && \
    printf '\nGMP_INCDIR := /usr/include/%s\nGMP_LIBDIR := /usr/lib/%s\n' "$(gcc -print-multiarch)" "$(gcc -print-multiarch)" >> /tmp/yafu-src/config.mk && \
    make -C /tmp/yafu-src yafu && \
    install -D -m 0755 /tmp/yafu-src/yafu /usr/local/bin/yafu && \
    install -D -m 0644 /tmp/yafu-src/docfile.txt /usr/local/share/ctfbot/yafu/docfile.txt && \
    install -m 0644 /tmp/yafu-src/yafu.ini /usr/local/share/ctfbot/yafu/yafu.ini && \
    install -m 0644 /tmp/yafu-src/README.md /usr/local/share/ctfbot/yafu/README.md && \
    rm -rf /tmp/yafu-src && \
    rm -rf /var/lib/apt/lists/*
COPY requirements.lock /opt/ctfbot/requirements.lock
RUN python3 -m pip install --no-cache-dir --require-hashes -r /opt/ctfbot/requirements.lock && \
    python3 -m pip check
COPY inventory.py /opt/ctfbot/inventory.py
RUN python3 /opt/ctfbot/inventory.py > /opt/ctfbot/inventory.json && \
    chmod -R a+rX /opt/ctfbot
USER 65532:65532
WORKDIR /tmp
ENTRYPOINT ["/bin/sh"]
