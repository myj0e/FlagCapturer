# Changelog

Notable changes are recorded here for each project version.

## Unreleased

- Add bounded run-state retrieval, summary history and continuation snapshots with auditable source references.
- Preserve long output tails and provide focused artifact reads/search without nested JSON previews.
- Separate ordinary, recovery and closing reply budgets from provider token telemetry.
- Normalize Codex context/compaction events, bound transport queues and restore run state after observed compaction.

- Remove known-answer files and exact-string flag verification from CLI, TUI, imports, runtime wiring and replay.
- Record candidates without automatic completion; preserve local checks and explicit `run_complete` outcomes.
- Use v3 answer-free evaluation datasets and candidate/execution metrics instead of claimed solve correctness.
- Consolidate batch execution under `evaluate`; retire the duplicate Stage A runner and answer-based pilot prototypes.
- Update regression and Docker acceptance checks to exercise candidate, report and replay workflows.

## 0.1.0 — 2026-09-29

- Establish the Python 3.11+ package and `src/` layout.
- Add the `ctfbot` entry point, version display, environment diagnostics, and a minimal terminal menu.
- Add project plans, baseline documentation, and ignore rules for local CTF data and credentials.
- The solving loop, model integration, sandbox, and full TUI remain unimplemented.
