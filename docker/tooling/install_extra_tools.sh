#!/bin/sh
# Runs at image build time only; runtime remains offline and unprivileged.
set -eu
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
    cmake zlib1g-dev libbz2-dev libpcap-dev \
    fcrackzip hashcat pocl-opencl-icd clinfo \
    sox libsox-fmt-all sagemath

# Pin source commits, retaining upstream companion tools, configs and wordlists.
git init /tmp/john-src
git -C /tmp/john-src remote add origin https://github.com/openwall/john.git
git -C /tmp/john-src fetch --depth 1 origin "${CTFBOT_JOHN_COMMIT}"
git -C /tmp/john-src checkout --detach FETCH_HEAD
test "$(git -C /tmp/john-src rev-parse HEAD)" = "${CTFBOT_JOHN_COMMIT}"
(
    cd /tmp/john-src/src
    ./configure --disable-native-tests \
        CPPFLAGS='-DJOHN_SYSTEMWIDE -DJOHN_SYSTEMWIDE_EXEC=\"/opt/ctfbot/john/run\" -DJOHN_SYSTEMWIDE_HOME=\"/opt/ctfbot/john/run\"'
    make -j2
)
mkdir -p /opt/ctfbot/john
cp -a /tmp/john-src/run /tmp/john-src/doc /opt/ctfbot/john/
# ctfbot replaces PATH at container creation; publish all executable companions
# into that fixed PATH while keeping the complete shared runtime directory.
for tool in /opt/ctfbot/john/run/*; do
    if [ -x "$tool" ] && [ ! -d "$tool" ] && [ ! -e "/usr/local/bin/$(basename "$tool")" ]; then
        ln -s "$tool" "/usr/local/bin/$(basename "$tool")"
    fi
done

git clone --depth 1 --branch "v${CTFBOT_BKCRACK_VERSION}" \
    https://github.com/kimci86/bkcrack.git /tmp/bkcrack-src
test "$(git -C /tmp/bkcrack-src rev-parse HEAD)" = "${CTFBOT_BKCRACK_COMMIT}"
cmake -S /tmp/bkcrack-src -B /tmp/bkcrack-build \
    -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/opt/ctfbot/bkcrack \
    -DBKCRACK_BUILD_TESTING=OFF
cmake --build /tmp/bkcrack-build --parallel 2
cmake --install /tmp/bkcrack-build
ln -s /opt/ctfbot/bkcrack/bkcrack /usr/local/bin/bkcrack

# RubyGems installs zsteg's image parsing dependencies as well as the CLI.
curl -fL --retry 3 --connect-timeout 20 --max-time 180 \
    "https://rubygems.org/downloads/zsteg-${CTFBOT_ZSTEG_VERSION}.gem" -o /tmp/zsteg.gem
echo "${CTFBOT_ZSTEG_SHA256}  /tmp/zsteg.gem" | sha256sum -c -
gem install --no-document /tmp/zsteg.gem
rm -rf /tmp/john-src /tmp/bkcrack-src /tmp/bkcrack-build /tmp/zsteg.gem /var/lib/apt/lists/*
