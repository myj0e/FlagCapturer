# Crypto workflow v1

For general-v2, workflow_environment exposes PyCryptodome (Crypto), sympy,
gmpy2, z3 and YAFU 3.1.9. Use saved Python scripts for AES/RSA operations,
big integers and bounded constraints. For general-purpose RSA factorization,
try YAFU with `yafu "factor(<modulus>)"`; it selects an appropriate available
method such as SIQS for medium-size composites. A factorization attempt is
still bounded by the run budget. SageMath is not part of this base toolbox.
Do not install packages or use online factoring services during a challenge run.

Decode a bounded encoding before selecting an algorithm. For RSA inspect modulus size and exponent, then test one bounded small-factor hypothesis. If it fails, distinguish prime powers, shared primes and padding issues before writing a new script. Elliptic curve and SageMath workflows require an explicitly prepared image; online factoring is disabled.

## Execution and verification

Use workflow_environment before workflow_run. Inputs are read-only paths relative to /challenge; scripts and extracted files go to /work. Inspect artifact hashes and lineage. Submit a candidate using candidate_submit; workflow output alone never marks a solve verified. Output caps, timeouts and missing dependencies must be retained in evidence.

## Coverage limits

This is a beginner workflow, not full category coverage. The representative authored fixture checks one mechanism; no live model solve rate or generalization claim has been established. Source and license inventory is in pack.yaml.
