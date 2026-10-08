# Reverse workflow v1

Read magic, machine, endianness and strings before choosing a disassembler. ELF metadata does not prove a guessed password. Use objdump only after environment checks. For PE, packed binaries and decompilation use an available reviewed tool or a bounded custom script; current built-in metadata helper supports ELF only. Keep generated scripts for replay and verify a candidate through the controller.

## Execution and verification

Use workflow_environment before workflow_run. Inputs are read-only paths relative to /challenge; scripts and extracted files go to /work. Inspect artifact hashes and lineage. Submit a candidate using candidate_submit; workflow output alone never marks a solve verified. Output caps, timeouts and missing dependencies must be retained in evidence.

The general-v2 toolbox provides file, strings, xxd, readelf, objdump, nm, GDB,
GCC/G++ (32/64-bit), patchelf, strace/ltrace, NASM, QEMU user emulation and the
Python capstone, unicorn, pyelftools, pefile and z3 libraries. Check availability
first; a different pinned image may have fewer tools. Use command_run for these
commands and script_save/script_run for reproducible Python analysis. GDB can
launch and debug its own child under the accepted profile; do not request extra
capabilities or attach to unrelated processes. For batch GDB, use `-nx -batch`,
`set debuginfod enabled off` and `set disable-randomization off`. Architecture
emulation may require a matching target loader/sysroot. A decompiler is not
included. Never install dependencies during a challenge run.

## Coverage limits

This is a beginner workflow, not full category coverage. The representative authored fixture checks one mechanism; no live model solve rate or generalization claim has been established. Source and license inventory is in pack.yaml.
