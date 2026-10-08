# Web workflow v1

Start with one bounded GET/HEAD through http_request if the remote grant permits the TCP endpoint. Save raw responses, compare status, body hashes and headers, and vary one input at a time. Redirects are observed but never followed. Browser automation, TLS, authenticated sessions and scanners are outside the first workflow. Offline response analysis remains available without network access.

## Execution and verification

Use workflow_environment before workflow_run. Inputs are read-only paths relative to /challenge; scripts and extracted files go to /work. Inspect artifact hashes and lineage. Submit a candidate using candidate_submit; workflow output alone never marks a solve verified. Output caps, timeouts and missing dependencies must be retained in evidence.

## Coverage limits

This is a beginner workflow, not full category coverage. The representative authored fixture checks one mechanism; no live model solve rate or generalization claim has been established. Source and license inventory is in pack.yaml.
