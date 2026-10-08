# Pwn workflow v1

Inspect ELF headers and protection evidence. Missing canary strings are inconclusive; ET_DYN only suggests PIE. Use session_start/send/read/close for local process experiments and script_save/script_run for replayable probes. pwntools and GDB are optional image dependencies. Any exploit is confined to the local sandbox or explicitly granted connector; pack selection cannot authorize a target.

## Execution and verification

Use workflow_environment before workflow_run. Inputs are read-only paths relative to /challenge; scripts and extracted files go to /work. Inspect artifact hashes and lineage. Submit a candidate using candidate_submit; workflow output alone never marks a solve verified. Output caps, timeouts and missing dependencies must be retained in evidence.

## Coverage limits

This is a beginner workflow, not full category coverage. The representative authored fixture checks one mechanism; no live model solve rate or generalization claim has been established. Source and license inventory is in pack.yaml.
