# Additional offline toolsets

The Dockerfile extends general-v3 for the missing commands observed in run
`f8195f9f-2353-4925-8a0b-9104659f0f64`:

| Commands | Installation |
| --- | --- |
| john, zip2john and companion converters | John the Ripper Jumbo, pinned commit; complete upstream run/doc directories |
| bkcrack | 1.7.1, pinned source commit; installed examples and helper tools |
| fcrackzip | Debian bookworm package |
| hashcat | Debian package with PoCL CPU OpenCL runtime and clinfo |
| zsteg | 0.2.13, SHA-256 checked RubyGem; runtime dependencies installed by RubyGems |
| sox | Debian SoX and libsox-fmt-all format plugins |
| sage | Debian SageMath toolset, including its algebra dependencies |

Source revisions and zsteg digest are Docker build arguments recorded in the
inventory. Debian package and installed RubyGem versions are also recorded.
Transitive RubyGem dependencies are resolved at build time, not hash locked.
John's runtime tools are linked into `/usr/local/bin` so they remain available
when the controller resets PATH; the shared files stay in `/opt/ctfbot/john/run`. bkcrack's examples
and tools are in `/opt/ctfbot/bkcrack`.

SageMath uses Debian Python through `sage`/`sage -python`; it is separate from
the image's `/usr/local` Python 3.12 environment. Sage state and caches
point to `/work`; the controller sets HOME to writable `/tmp`. Save recovery sessions and potfiles explicitly under `/work`. Hashcat uses
CPU OpenCL; the sandbox has no GPU passthrough. No runtime network access or
additional privileges are introduced. SageMath and PoCL substantially increase
image size and build time.

Hashcat is exposed through `/usr/local/bin/hashcat`. Its launcher uses
libnss-wrapper to supply a private user entry with home `/work`, because
Hashcat 6.2.6 crashes when the controller UID is absent from `/etc/passwd`.
The launcher preserves the real UID and GID, sets writable XDG paths and
removes its temporary NSS files on normal exit; it does not change system
passwd files or sandbox privileges. Use `hashcat` from PATH, not the underlying
`/usr/bin/hashcat` binary.

Existing images and containers do not gain tools until a new image is built,
accepted and selected. The profile name
remains general-v3; check actual workflow_environment/inventory availability
rather than inferring this extension from the profile label alone.

Final image acceptance is `doc/phase-d/verify_tool_image.py`; it now checks an
authored encrypted ZIP with fcrackzip and John/zip2john, Hashcat backend discovery,
bkcrack/zsteg startup, SoX audio generation and Sage integer factorization.
These image checks must be run after building; configuration checks alone do
not establish functional image acceptance. The original toolbox and extra
toolsets run in separate bounded probes so cold OpenCL kernel compilation
does not consume the original toolbox probe's deadline.

## Local acceptance on 2026-10-09

Accepted image:
`sha256:832c36c7a7494925a85f8dc975998e2b43db6410a5d7c2aafeab7d2227d437e9`.
Both `ctfbot-tools:candidate` and `ctfbot-tools:local` now reference this image;
the default runtime resolver was checked against this immutable ID.

All 16 functional groups passed in the actual offline, read-only, unprivileged
sandbox, including encrypted ZIP recovery, Hashcat CPU cracking, zsteg PNG
extraction, SoX audio generation and Sage factorization. The temporary runtime
was removed. Configuration/workflow checks passed (18 tests); no smoke test
was run. No project container was active at promotion, so no task was stopped.
Restart an already open TUI to refresh its cached runtime image selection.

Private receipts: `data/acceptance/20261009-extra-tools-build-4/`,
`data/acceptance/20261009-extra-tools-functional-5/`, and
`data/acceptance/20261009-extra-tools-promotion.json`.

Upstream references:
- https://github.com/openwall/john
- https://github.com/kimci86/bkcrack/tree/v1.7.1
- https://rubygems.org/gems/zsteg/versions/0.2.13
- https://packages.debian.org/bookworm/sagemath
- https://packages.debian.org/bookworm/hashcat
