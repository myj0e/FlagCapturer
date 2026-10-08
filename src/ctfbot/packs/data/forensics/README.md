# Forensics workflow v1

Start with hashes, magic and bounded strings. List archive entries before extraction; never trust member paths or advertised sizes. Extract only selected bounded files inside /work and export with parent lineage. For packet/audio/metadata analysis check optional binaries first; absence is an environment limitation, not evidence that the challenge is unsolvable.

## Execution and verification

Use workflow_environment before workflow_run. Inputs are read-only paths relative to /challenge; scripts and extracted files go to /work. Inspect artifact hashes and lineage. Submit a candidate using candidate_submit; workflow output alone never marks a solve verified. Output caps, timeouts and missing dependencies must be retained in evidence.

## Coverage limits

This is a beginner workflow, not full category coverage. The representative authored fixture checks one mechanism; no live model solve rate or generalization claim has been established. Source and license inventory is in pack.yaml.
