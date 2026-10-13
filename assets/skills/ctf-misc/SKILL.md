---
name: ctf-misc
description: Solve CTF encoding, jail, signal, geometry and miscellaneous puzzles with evidence-driven probes.
license: MIT
---
# ctf-misc

Use `skill_read` to read the references below only when relevant. The guide is adapted for FlagCapturer; upstream examples are reference material, not executable environment setup.

## Execution in this workbench

- Execute commands and parse challenge data only through sandbox tools. Read original and derived files by asset ID; preserve output assets and execution provenance.
- Check `workflow_environment` for an applicable method or inspect the approved `tool_request` catalog before choosing capabilities. Missing dependencies use `tool_request`; do not run upstream installers, package managers or downloads in solving scripts.
- Upstream Bash/Read/Write/Edit/Skill names describe another host. Use `command_run`, `script_save`/`script_run`, `asset_read`, `workflow_search`/`workflow_read` and `skill_read` here. Bundled scripts can be staged with `skill_materialize`, then executed with sandbox tools. Skill paths are documentation paths, not sandbox paths.
- Network access is limited to currently registered tools and granted endpoints. An HTTPS reference URL is not its content. If a required source cannot be read, promptly report the missing capability and request its text or an available acquisition method. Do not replace reading a supplied solution with blind parameter search.
- Examples involving mounting, privileged devices, unrestricted sockets or host access do not confer these capabilities. Prefer existing user-space extraction methods; report unsupported prerequisites.
- Before a substantial experiment, state the tested hypothesis, parameter basis, expected observation and next decision using `claim_record`/`experiment_record` when useful. A successful script or continuous curve does not prove that its encoding matches the task.
- User hints are prioritized leads; separate them from observed facts. Failed variants reject only their tested conditions. If progress stalls, load `ctf-route-review` and revisit incomplete evidence before adding more variants.
- Submit identified results with `candidate_submit`. Finish with evidence-backed Markdown through `run_complete`; do not add platform submission, corpus-wide uniqueness checks or unrelated objectives unless the user requested them.

## References

- [Upstream category guide](../../vendor/ctf-misc/SKILL.md): select techniques and supporting documents for the current evidence. Installation and host-specific commands are descriptive examples and require the mappings above.
- [pyjails.md](../../vendor/ctf-misc/pyjails.md): Python jail/sandbox escape techniques, quine context detection, restricted character repunit decomposition, func_globals module chain traversal, restricted charset number generation, class attribute persistence, f-string config injection via stored eval
- [bashjails.md](../../vendor/ctf-misc/bashjails.md): Bash jail/restricted shell escape techniques, HISTFILE file read trick, bash -v verbose mode, ctypes.sh direct C library calls
- [encodings.md](../../vendor/ctf-misc/encodings.md): Encodings, QR codes, esolangs, UTF-16 tricks, BCD encoding, multi-layer auto-decoding, indexed directory QR reassembly, multi-stage URL encoding chains
- [encodings-advanced.md](../../vendor/ctf-misc/encodings-advanced.md): Verilog/HDL, Gray code cyclic encoding, RTF custom tag extraction, SMS PDU decoding, multi-encoding sequential solvers, UTF-9, pixel binary encoding, hexadecimal Sudoku + QR assembly, TOPKEK, MaxiCode
- [rf-sdr.md](../../vendor/ctf-misc/rf-sdr.md): RF/SDR/IQ signal processing (QAM-16, carrier recovery, timing sync)
- [dns.md](../../vendor/ctf-misc/dns.md): DNS exploitation (ECS spoofing, NSEC walking, IXFR, rebinding, tunneling)
- [games-and-vms.md](../../vendor/ctf-misc/games-and-vms.md): WASM patching, Roblox place file reversing, PyInstaller, marshal analysis, Python env RCE, Z3 (including boolean logic gate network SAT solving), K8s RBAC, floating-point precision exploitation, custom assembly language sandbox escape via Python MRO chain
- [games-and-vms-2.md](../../vendor/ctf-misc/games-and-vms-2.md): Cookie checkpoint game brute-forcing, Flask cookie game state leakage, WebSocket game manipulation, server time-only validation bypass, De Bruijn sequence, Brainfuck instrumentation, WASM linear memory manipulation
- [games-and-vms-3.md](../../vendor/ctf-misc/games-and-vms-3.md): memfd_create packed binaries, multi-phase crypto games with HMAC commitment-reveal and GF(256) Nim, emulator ROM-switching state preservation, Python marshal code injection, Benford's Law bypass, parallel connection oracle relay, nonogram solver pipelines, 100 prisoners problem, C code jail escape via emoji identifiers, BuildKit daemon build secret exploitation, Docker container escape, Levenshtein distance oracle attack, taint analysis bypass via type coercion, shredded document pixel-edge reas
- [games-and-vms-4.md](../../vendor/ctf-misc/games-and-vms-4.md): Part 4 (2018-era): XSLT as Turing-complete VM, JavaScript MAX_SAFE_INTEGER successor equality, binary search oracle in comparison-only DSL, blind SQLi via script-engine timeout error, OEIS sequence lookup automation, QR code reassembly from format-string constraints, matrix exponentiation for Fibonacci recurrence, Tribonacci for frog-jump counting, Selenium + Tesseract dynamic CAPTCHA, Brainfuck→Piet multi-layer polyglot, bytebeat synth code recognition
- [linux-privesc.md](../../vendor/ctf-misc/linux-privesc.md): Sudo wildcard parameter injection (fnmatch), crafted pcap for sudoers.d, monit confcheck process injection, Apache -d override, backup cronjob SUID, PostgreSQL COPY TO PROGRAM RCE, PostgreSQL backup credential extraction, NFS share exploitation, SSH Unix socket tunneling, PaperCut Print Deploy privesc, Squid proxy pivoting, Zabbix admin password reset via MySQL, WinSSHTerm credential decryption
- [ctfd-navigation.md](../../vendor/ctf-misc/ctfd-navigation.md): CTFd platform API navigation without browser: detection, token auth, challenge listing, file download, flag submission, scoreboard, hints, notifications, Python client class
