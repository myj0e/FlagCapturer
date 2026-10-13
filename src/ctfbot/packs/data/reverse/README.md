# Reverse workflow v1

Read magic, machine, endianness and strings before choosing a disassembler. ELF metadata does not prove a guessed password. Use objdump only after environment checks. For PE, packed binaries and decompilation use an available reviewed tool or a bounded custom script; current built-in metadata helper supports ELF only. Keep generated scripts for replay and submit the flag and finish its objective; do not wait for platform verification.

## Execution and verification

Use workflow_environment before workflow_run. Inputs are read-only paths relative to /challenge; scripts and extracted files go to /work. Inspect artifact hashes and lineage. Submit the flag using candidate_submit to complete that requested objective. Continue only actual remaining subtasks, then include an agent-authored Markdown writeup in run_complete; platform acceptance is never pending work. Output caps, timeouts and missing dependencies must be retained in evidence.

The general-v3 toolbox provides file, strings, xxd, readelf, objdump, nm, GDB,
GCC/G++ (32/64-bit), patchelf, strace/ltrace, NASM, QEMU user emulation and the
Python capstone, unicorn, pyelftools, pefile and z3 libraries. Check availability
first; a different pinned image may have fewer tools. Use command_run for these
commands and script_save/script_run for reproducible Python analysis. GDB can
launch and debug its own child under the accepted profile; do not request extra
capabilities or attach to unrelated processes. UPX and Go are included. Inspect
packing signatures before trying UPX; a failed unpack is not proof of corruption
or a missing tool. Copy the read-only input into /work before unpacking it. For
local Go helpers, use writable GOCACHE, GOPATH and GOTMPDIR under /work with
GOPROXY=off and GOSUMDB=off; compile only code whose dependencies are available
offline. The Go runtime does not provide a Windows runtime or decompiler. For batch GDB, use `-nx -batch`,
`set debuginfod enabled off` and `set disable-randomization off`. Architecture
emulation may require a matching target loader/sysroot. A decompiler is not
included. Never install dependencies during a challenge run.

## Coverage limits

This is a beginner workflow, not full category coverage. The representative authored fixture checks one mechanism; no live model solve rate or generalization claim has been established. Source and license inventory is in pack.yaml.
