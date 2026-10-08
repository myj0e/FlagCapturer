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
    "perl", "java", "jq", "openssl",
)

def inventory():
    packages = subprocess.run(["dpkg-query", "-W", "-f=${Package}\t${Version}\n"],
                              check=True, capture_output=True, text=True).stdout
    return {
        "schema_version": 1, "profile": "general-v2", "python": sys.version,
        "binaries": {name: shutil.which(name) for name in BINARIES},
        "external_tools": {
            "yafu": {
                "version": os.environ.get("CTFBOT_YAFU_VERSION"),
                "commit": os.environ.get("CTFBOT_YAFU_COMMIT"),
                "source": "https://github.com/bbuhrow/yafu",
            },
        },
        "python_packages": {item.metadata["Name"]: item.version for item in importlib.metadata.distributions()},
        "debian_packages": dict(line.split("\t", 1) for line in packages.splitlines()),
        "licenses": "Debian copyright notices: /usr/share/doc/*/copyright; Python distribution metadata: *.dist-info",
        "runtime": "offline, read-only root, unprivileged user; networking and capabilities controlled by ctfbot",
    }

if __name__ == "__main__":
    print(json.dumps(inventory(), sort_keys=True, indent=2))
