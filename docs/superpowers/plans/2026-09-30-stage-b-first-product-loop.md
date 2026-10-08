# Stage B: first usable product loop

Spec: `doc/AI_CTF_AGENT_DETAILED_PLAN.md`, sections 6.1–6.5. The roadmap design at `docs/superpowers/specs/2026-09-30-iterative-closure-roadmap-design.md` is the higher-level sequencing authority.

## Goal and scope

Deliver the first usable local attachment workflow: preview an importer-generated admitted workspace, check model-transfer authorization and a pinned offline image, run one bounded agent through the shared application service, stop it, inspect recorded evidence, and generate a basic report. Headless `solve` and Textual TUI use the same application service and run policy.

This increment accepts the existing importer-generated workspace plus its controller-only oracle. Remote targets, services, interactive sessions, batch evaluation, additional providers, and broader Docker certification remain unsupported. Use only synthetic fixtures, fake model sessions, and fake runtime implementations during development verification. Do not send real challenge data to a model or start Docker in this work.

## Global constraints

- Validate workspace, oracle, authorization, image reference, and limits before constructing a model or runtime.
- Keep the oracle controller-only. Show model-submitted candidate values only on TUI `candidate_submit` records, loaded from private hash-verified evidence and labeled with the exact-verifier result; keep raw candidates out of Markdown reports and model-facing tool replies.
- Keep the current offline, digest-pinned Docker profile, hard budgets, append-only evidence, and read-only attachment input.
- Display untrusted text as escaped plain text; do not interpret Rich markup or terminal escape sequences.
- A user stop must interrupt the current model process/command, finish runtime cleanup, and preserve evidence already written.
- Preserve pre-existing working-tree changes. Do not commit this task's work in the shared dirty checkout.

## Task 1 — Shared application service and preflight preview

**Files:** `src/ctfbot/application/service.py`, `src/ctfbot/application/baseline.py`, `src/ctfbot/cli/main.py`, `tests/test_stage_b_application.py`.

1. Write tests for preview metadata and fail-closed behavior: input paths and hashes are reported; authorization and image support are visible; invalid admission/image and missing authorization stop before model/runtime factories are called.
2. Run the tests and confirm the new service API is missing.
3. Add an application service that previews an existing private importer-generated workspace and runs it through the existing baseline policy. Inject model/runtime factories for synthetic verification, and keep factories lazy until preflight succeeds.
4. Route headless `solve` through the same service.
5. Run the Stage B application tests and the full existing suite.

**Produces:** `ChallengePreview`, shared service preview/run entry points, lazy dependency construction, CLI use of the service.

**Expected:** synthetic previews show the challenge id, relative input paths, hashes, byte counts, verifier method, authorization state, image profile, and limits without reading or returning oracle contents; denied preflight creates no model/runtime.

## Task 2 — Cancellation, event delivery, and basic report

**Files:** `src/ctfbot/application/control.py`, `src/ctfbot/agent/loop.py`, `src/ctfbot/evidence/store.py`, `src/ctfbot/model_adapters/codex_session.py`, `src/ctfbot/model_adapters/codex_app_server.py`, `src/ctfbot/runtime/docker.py`, `src/ctfbot/tools/registry.py`, `src/ctfbot/reporting/__init__.py`, `tests/test_stage_b_control_report.py`.

1. Write tests for cancellation during a blocked fake model turn and a blocked fake command, persisted event notification, controller verifier metadata, and a report that includes evidence references but never candidate/oracle values.
2. Run tests and confirm the new control/report behaviors are missing.
3. Thread a cancellation control through the agent loop and application service. Cancellation interrupts the Codex App Server process and kills the active Docker container; normal teardown closes and removes runtime resources.
4. Deliver persisted evidence records to an optional event sink without allowing UI delivery failures to interrupt a run.
5. Add a deterministic Markdown report from run metadata and recorded events, omitting raw candidate values and raw artifact contents.
6. Run focused tests and the full suite.

**Produces:** an idempotent stop control, explicit `user_cancelled` result/evidence, persisted event callbacks, exact-verifier metadata, and a basic evidence-linked report.

**Expected:** synthetic cancellation returns a stopped result and closes model/runtime; verified results name the exact-string controller verifier; generated reports identify run, inputs, limits, status, event timeline, and evidence paths without containing the synthetic flag.

## Task 3 — Textual single-run TUI

**Files:** `pyproject.toml`, `src/ctfbot/tui/app.py`, `src/ctfbot/tui/safe_text.py`, `tests/test_stage_b_tui.py`.

1. Write Textual Pilot tests for preview, authorization denial, synthetic run, stop, event/evidence visibility, report generation, and escaped control/bidirectional text.
2. Run the tests and confirm the production screen and safe renderer are missing.
3. Add the ADR-selected `textual==8.2.8` runtime dependency and a keyboard-focused view with workspace/oracle/image/run-root inputs and Preview, Run, Stop, Evidence, Report, and Quit actions.
4. Construct no model/runtime during preview. Run through the shared application service in a worker; show candidate and verifier status, with the submitted value read from its private persisted artifact for the TUI record.
5. Render all dynamic text as escaped plain Rich `Text` with markup disabled. Preserve the non-interactive terminal guard.
6. Run Textual Pilot tests and the full suite.

**Produces:** the first production TUI workflow, safe status/event rendering, and dependency declaration.

**Expected:** a synthetic admitted case can move from preview to run result and report using fakes; an unauthorized or invalid case cannot run; stop remains responsive while a worker blocks; no control or bidi characters reach rendered strings.

## Task 4 — Scope and acceptance record

**Files:** `README.md`, `doc/AI_CTF_AGENT_DETAILED_PLAN.md`, `doc/phase-a/README.md`.

1. Update user instructions for the Stage B workspace inputs, per-challenge model authorization, supported offline runtime profile, and report/evidence paths.
2. Record what the synthetic acceptance proves and the exact unverified live/provider/runtime expansions that remain; do not mark those expansions complete.
3. Run the full synthetic test suite and inspect the final diff for changes outside this plan's file list.

**Expected:** docs describe the implemented entry path and limits accurately; no real model or Docker execution is required for the recorded synthetic verification.

## Review focus

- Preflight cannot instantiate a model/runtime for unauthorized, malformed, out-of-root, symlinked, or mutable-image inputs.
- Cancellation races do not relabel a controller-verified candidate as stopped, strand a blocking model/command, expose oracle values or candidate values in model-facing tool replies/reports, or skip runtime cleanup.
- TUI rendering cannot interpret ANSI/OSC/Rich markup or bidi controls from input, event fields, or errors.
- Reports only claim actions present in persisted evidence and never include raw candidate/artifact contents.
- The headless and TUI paths use the same application service and limits.

## Execution

Execute inline in the user's selected current workspace. Keep changes uncommitted so the existing Stage A work remains reviewable in place. Use `PYTHONPATH=src pytest -q` for the whole suite; when importing Textual from the Stage A spike environment, include `/tmp/ctfbot-phase-a-tui/lib/python3.14/site-packages` on `PYTHONPATH`.
