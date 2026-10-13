---
name: ctf-writeup
description: Write a reproducible CTF report from saved evidence, actual commands and exact submitted results.
license: MIT
---
# ctf-writeup

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

- [Upstream category guide](../../vendor/ctf-writeup/SKILL.md): select techniques and supporting documents for the current evidence. Installation and host-specific commands are descriptive examples and require the mappings above.

Report verified findings and remaining limitations. Use only actual execution records; do not claim a candidate was locally reproduced or externally accepted when it was not.
