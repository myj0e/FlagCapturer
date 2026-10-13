"""Build-time inventory, also usable as a read-only runtime diagnostic."""
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys

BINARIES = (
    "python", "python3", "pip", "bash", "sh", "file", "strings", "xxd",
    "hexdump", "readelf", "objdump", "nm", "gdb", "gcc", "g++", "make",
    "strace", "ltrace", "patchelf", "nasm", "qemu-i386", "qemu-arm",
    "curl", "wget", "nc", "socat", "tshark", "tcpdump", "unzip", "7z",
    "exiftool", "binwalk", "steghide", "foremost", "yafu", "node", "npm", "ruby",
    "perl", "java", "jq", "openssl", "factor", "upx", "go", "veracrypt", "cryptsetup",
    "bkcrack", "fcrackzip", "john", "zip2john", "hashcat", "clinfo", "zsteg", "sox", "sage",
)

def inventory():
    packages = subprocess.run(["dpkg-query", "-W", "-f=${Package}\t${Version}\n"],
                              check=True, capture_output=True, text=True).stdout
    binaries = {name: shutil.which(name) for name in BINARIES}
    missing = [name for name, path in binaries.items() if path is None]
    if missing:
        raise RuntimeError("toolbox build is missing declared binaries: " + ", ".join(missing))
    return {
        "schema_version": 1, "profile": "general-v3", "python": sys.version,
        "binaries": binaries,
        "runtime_constraints": {
            "network": "offline unless explicitly controller-authorized",
            "filesystem": "read-only /challenge; writable /work; read-only root",
            "disk_encryption": "file/header analysis and userspace crypto only; no mount, FUSE or device-mapper access",
            "go": "offline builds; put GOCACHE, GOPATH and GOTMPDIR under /work; GOPROXY=off and GOSUMDB=off",
            "password_recovery": "CPU OpenCL via PoCL; no GPU passthrough; keep sessions, potfiles and caches under /work",
            "sage": "use sage or sage -python (Debian Python); DOT_SAGE=/work/.sage; not the /usr/local Python environment",
        },
        "external_tools": {
            "john": {
                "variant": "Jumbo; complete run and doc directories, including *2john converters",
                "commit": os.environ.get("CTFBOT_JOHN_COMMIT"),
                "source": "https://github.com/openwall/john",
            },
            "bkcrack": {
                "version": os.environ.get("CTFBOT_BKCRACK_VERSION"),
                "commit": os.environ.get("CTFBOT_BKCRACK_COMMIT"),
                "source": "https://github.com/kimci86/bkcrack",
            },
            "zsteg": {
                "version": os.environ.get("CTFBOT_ZSTEG_VERSION"),
                "sha256": os.environ.get("CTFBOT_ZSTEG_SHA256"),
                "source": "https://rubygems.org/gems/zsteg",
            },
            "upx": {
                "version": os.environ.get("CTFBOT_UPX_VERSION"),
                "sha256": os.environ.get("CTFBOT_UPX_SHA256"),
                "source": "https://github.com/upx/upx/releases",
            },
            "veracrypt": {
                "version": os.environ.get("CTFBOT_VERACRYPT_VERSION"),
                "sha256": os.environ.get("CTFBOT_VERACRYPT_SHA256"),
                "source": "https://github.com/veracrypt/VeraCrypt/releases",
                "variant": "Debian 12 amd64 console",
            },
            "yafu": {
                "version": os.environ.get("CTFBOT_YAFU_VERSION"),
                "commit": os.environ.get("CTFBOT_YAFU_COMMIT"),
                "source": "https://github.com/bbuhrow/yafu",
            },
        },
        "python_packages": {item.metadata["Name"]: item.version for item in importlib.metadata.distributions()},
        "debian_packages": dict(line.split("\t", 1) for line in packages.splitlines()),
        "ruby_gems": subprocess.run(["gem", "list", "--local"], check=True,
                                    capture_output=True, text=True).stdout.splitlines(),
        "licenses": "Debian copyright notices: /usr/share/doc/*/copyright; Python distribution metadata: *.dist-info",
        "runtime": "offline, read-only root, unprivileged user; networking and capabilities controlled by ctfbot",
    }

if __name__ == "__main__":
    print(json.dumps(inventory(), sort_keys=True, indent=2))
