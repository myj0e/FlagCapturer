ARG BASE_IMAGE=python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258
FROM ${BASE_IMAGE} AS downloads
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates && rm -rf /var/lib/apt/lists/*
COPY downloads.lock.json fetch_tools.py /opt/downloads/
RUN --mount=type=cache,target=/var/cache/ctfbot-downloads \
    python3 /opt/downloads/fetch_tools.py /var/cache/ctfbot-downloads /opt/downloads/downloads.lock.json && \
    mkdir /downloads && cp /var/cache/ctfbot-downloads/ghidra /var/cache/ctfbot-downloads/jdk /var/cache/ctfbot-downloads/grpcurl /var/cache/ctfbot-downloads/jadx /var/cache/ctfbot-downloads/foundry /var/cache/ctfbot-downloads/pwndbg /var/cache/ctfbot-downloads/solc /var/cache/ctfbot-downloads/cyberchef /var/cache/ctfbot-downloads/networkminer /var/cache/ctfbot-downloads/burpsuite /downloads/

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
# Keep the accepted general-v2 layers reusable; general-v3 adds historical gaps.
# Release asset SHA-256 values are pinned from the upstream GitHub release metadata.
ARG UPX_VERSION=5.2.0
ARG UPX_SHA256=3db5d3294707439db97866feab8d75d800f028f48481a40547411824da4288a1
ARG VERACRYPT_VERSION=1.26.29
ARG VERACRYPT_SHA256=62405d1a0c958995ace30267b0007537e3b0b8efee32b424262a752127d5218c
ENV CTFBOT_UPX_VERSION=${UPX_VERSION} CTFBOT_UPX_SHA256=${UPX_SHA256} \
    CTFBOT_VERACRYPT_VERSION=${VERACRYPT_VERSION} CTFBOT_VERACRYPT_SHA256=${VERACRYPT_SHA256}
RUN test "$(dpkg --print-architecture)" = amd64 && \
    curl -fL --retry 3 --connect-timeout 20 --max-time 180 \
      "https://github.com/upx/upx/releases/download/v${UPX_VERSION}/upx-${UPX_VERSION}-amd64_linux.tar.xz" -o /tmp/upx.tar.xz && \
    echo "${UPX_SHA256}  /tmp/upx.tar.xz" | sha256sum -c - && \
    mkdir /tmp/upx && tar -xJf /tmp/upx.tar.xz -C /tmp/upx --strip-components=1 && \
    install -m 0755 /tmp/upx/upx /usr/local/bin/upx && \
    install -D -m 0644 /tmp/upx/COPYING /usr/local/share/doc/upx/COPYING && \
    install -m 0644 /tmp/upx/LICENSE /usr/local/share/doc/upx/LICENSE && \
    curl -fL --retry 3 --connect-timeout 20 --max-time 180 \
      "https://github.com/veracrypt/VeraCrypt/releases/download/VeraCrypt_${VERACRYPT_VERSION}/veracrypt-console-${VERACRYPT_VERSION}-Debian-12-amd64.deb" -o /tmp/veracrypt.deb && \
    echo "${VERACRYPT_SHA256}  /tmp/veracrypt.deb" | sha256sum -c - && \
    export DEBIAN_FRONTEND=noninteractive && apt-get update && \
    apt-get install -y --no-install-recommends golang-go cryptsetup-bin /tmp/veracrypt.deb && \
    rm -rf /tmp/upx /tmp/upx.tar.xz /tmp/veracrypt.deb /var/lib/apt/lists/*
LABEL org.ctfbot.tooling.profile="general-v3"
# Additional archive recovery, image/audio steganography and algebra toolsets.
# Install complete toolsets so companion converters (notably zip2john) are present.
ARG JOHN_COMMIT=302f527093210d46b8d0de5c75245765f17d4806
ARG BKCRACK_VERSION=1.7.1
ARG BKCRACK_COMMIT=d6baae68c3e009ec2be7b19a53986c75de474131
ARG ZSTEG_VERSION=0.2.13
ARG ZSTEG_SHA256=f56a63c187e8a43f379b696e13c21dee5f190b619bff1364454844bdd35e1389
ENV CTFBOT_JOHN_COMMIT=${JOHN_COMMIT} \
    CTFBOT_BKCRACK_VERSION=${BKCRACK_VERSION} CTFBOT_BKCRACK_COMMIT=${BKCRACK_COMMIT} \
    CTFBOT_ZSTEG_VERSION=${ZSTEG_VERSION} CTFBOT_ZSTEG_SHA256=${ZSTEG_SHA256}
COPY install_extra_tools.sh /opt/ctfbot/install_extra_tools.sh
RUN sh /opt/ctfbot/install_extra_tools.sh
RUN export DEBIAN_FRONTEND=noninteractive && apt-get update && \
    apt-get install -y --no-install-recommends libnss-wrapper && \
    rm -rf /var/lib/apt/lists/*
COPY --chmod=0755 hashcat.sh /usr/local/bin/hashcat
ENV PATH="/opt/ctfbot/john/run:${PATH}" \
    HOME=/work XDG_CACHE_HOME=/work/.cache DOT_SAGE=/work/.sage \
    POCL_CACHE_DIR=/work/.cache/pocl
# Extended offline toolkit: GUI tools are available through Xvfb, with no host display mount.
RUN export DEBIAN_FRONTEND=noninteractive && apt-get update && \
    apt-get install -y --no-install-recommends \
      ripgrep tree vim-tiny tesseract-ocr tesseract-ocr-eng tesseract-ocr-chi-sim \
      sleuthkit autopsy wireshark audacity pulseview sigrok-cli gnuradio \
      apktool adb chromium xvfb xauth fonts-dejavu-core fonts-liberation \
      mono-runtime libmono-system-windows-forms4.0-cil libmono-system-web4.0-cil \
      libgdiplus x11-utils && \
    rm -rf /var/lib/apt/lists/*
COPY requirements-extra.lock /opt/ctfbot/requirements-extra.lock
RUN python3 -m pip install --no-cache-dir --require-hashes -r /opt/ctfbot/requirements-extra.lock && python3 -m pip check
COPY --from=downloads /downloads /tmp/tool-downloads
COPY downloads.lock.json install_distributions.py /opt/ctfbot/
RUN python3 /opt/ctfbot/install_distributions.py
RUN apt-get update && apt-get install -y --no-install-recommends firefox-esr && rm -rf /var/lib/apt/lists/*
ENV JAVA_HOME=/opt/ctfbot/jdk JAVA_TOOL_OPTIONS="-Duser.home=/work" DEBUGINFOD_URLS="" \
    QT_QPA_PLATFORM=offscreen MPLCONFIGDIR=/work/.cache/matplotlib \
    PYTHONPYCACHEPREFIX=/work/.cache/pycache \
    RAYON_NUM_THREADS=2 TOKIO_WORKER_THREADS=2 OMP_THREAD_LIMIT=2 OPENBLAS_NUM_THREADS=2
LABEL org.ctfbot.tooling.profile="general-v4"
COPY inventory.py smoke.py gui_smoke.py /opt/ctfbot/
RUN python3 /opt/ctfbot/inventory.py > /opt/ctfbot/inventory.json && \
    find /opt/ctfbot ! -perm -0444 -exec chmod a+r {} + && \
    find /opt/ctfbot \( -type d -o -perm /111 \) ! -perm -0111 -exec chmod a+x {} +
USER 65532:65532
WORKDIR /tmp
ENTRYPOINT ["/bin/sh"]
