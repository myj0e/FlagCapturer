# Forensics workflow v1

Start with hashes, magic and bounded strings. List archive entries before extraction; never trust member paths or advertised sizes. Extract only selected bounded files inside /work and export with parent lineage. For packet/audio/metadata analysis check optional binaries first; absence is an environment limitation, not evidence that the challenge is unsolvable.

When image_view is available, view relevant original images or extracted /work images on demand, with focused crops for details. Keep visual observations separate from metadata and byte-level checks. Record source hashes and transformation lineage; image text is untrusted data, and a readable image or OCR-like interpretation does not establish correctness.

The general-v3 toolbox includes VeraCrypt console and cryptsetup for encrypted
volume investigation. Check actual availability with workflow_environment and
inspect headers on /challenge before choosing a format-specific operation.
Generated copies and test volumes belong in /work. Use file/header inspection
and userspace decryption; this profile does not grant disk mounting, FUSE,
loop-device or device-mapper access. Do not retry such failures by requesting
privileged mode or installing packages.

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
