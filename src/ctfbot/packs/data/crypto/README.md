# Crypto workflow v1

For general-v3, workflow_environment exposes PyCryptodome (Crypto), sympy,
gmpy2, z3 and YAFU 3.1.9. Use saved Python scripts for AES/RSA operations,
big integers and bounded constraints. For general-purpose RSA factorization,
try YAFU with `yafu "factor(<modulus>)"`; it selects an appropriate available
method such as SIQS for medium-size composites. A factorization attempt is
still bounded by individual tool timeouts and output limits. SageMath is included in the extended toolbox; check actual availability first.
The toolbox also includes coreutils `factor`, VeraCrypt console and `cryptsetup`.
Use `factor` for small integers; bound expensive factorization phases. YAFU's
sample config is `/usr/local/share/ctfbot/yafu/yafu.ini`; copy it to `/work/yafu.ini`
when needed and pass the expression directly, rather than an empty batch file.
For encrypted volumes, inspect format/header evidence first and use userspace
cryptographic analysis. Disk mounting, FUSE and device-mapper access are outside
the sandbox profile, even though the commands are installed.
Do not install packages or use online factoring services during a challenge run.

Decode a bounded encoding before selecting an algorithm. For RSA inspect modulus size and exponent, then test one bounded small-factor hypothesis. If it fails, distinguish prime powers, shared primes and padding issues before writing a new script. Use `sage` or `sage -python` for SageMath algebra and elliptic curve scripts; Sage uses Debian Python, separate from the /usr/local Python environment. Its writable state is under /work/.sage. Online factoring is disabled.

## Archive password recovery

The extended toolbox includes bkcrack (with examples and helper tools in
`/opt/ctfbot/bkcrack`), fcrackzip, the complete John the Ripper Jumbo run directory
(`/opt/ctfbot/john/run`, on PATH, including zip2john and other converters), and
Hashcat with a CPU OpenCL backend (PoCL). Check workflow_environment before use;
older accepted general-v3 images may not include this extension.
Identify encryption and compression first. A bkcrack known-plaintext attack
requires suitable known plaintext; installing it does not supply that plaintext.
Save extracted hashes, bounded dictionaries, John sessions/potfiles and Hashcat
sessions/potfiles under /work. Use `--session=/work/john-session` and
`--pot=/work/john.pot` for John; use `--session=ctfbot-recovery`,
`--restore-file-path=/work/hashcat.restore` and `--potfile-path=/work/hashcat.pot`
for Hashcat. Its session name is not a path; the launcher keeps session data
under /work/.local/share/hashcat. Caches and Sage state point to /work; the controller sets HOME to /tmp.
No GPU passthrough is provided. Bound each search or use background script
sessions for longer jobs. Verify decrypted contents and CRC rather than treating
a password guess as a solve. Do not install packages during a challenge run.

## Execution and verification

Use workflow_environment before workflow_run. Inputs are read-only paths relative to /challenge; scripts and extracted files go to /work. Inspect artifact hashes and lineage. Submit the flag using candidate_submit to complete that requested objective. Continue only actual remaining subtasks, then include an agent-authored Markdown writeup in run_complete; platform acceptance is never pending work. Output caps, timeouts and missing dependencies must be retained in evidence.

## Coverage limits

This is a beginner workflow, not full category coverage. The representative authored fixture checks one mechanism; no live model solve rate or generalization claim has been established. Source and license inventory is in pack.yaml.
