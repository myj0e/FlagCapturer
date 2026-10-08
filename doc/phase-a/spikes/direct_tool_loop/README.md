# Direct tool loop spike

This dependency-free reference exercises the proposed ctfbot boundary between a normalized model response, local tool registry, policy check, and append-only event log.

Run from the repository root:

```sh
python3 doc/phase-a/spikes/direct_tool_loop/loop.py
```

It creates a synthetic challenge workspace and a sibling controller-only oracle file, runs a fixed fake-provider transcript through `workspace.list`, `workspace.read_text`, a rejected path-traversal attempt, and `candidate.submit`, then prints an append-only JSONL-shaped event trace. The verifier confirms only the synthetic fixture. Candidate values are redacted from event arguments; evidence is referenced by hash/artifact name. It does not contact a provider, execute shell commands, access the network, or touch real challenge files. This is a design spike, not production code or a sandbox security test.

The prototype boundary is that the model returns a `ToolCall` value; ctfbot-like code still validates the tool name and arguments, enforces a workspace path policy, bounds reads, persists evidence references, and checks a candidate with an oracle unavailable to the model. The fake transcript is scripted and the oracle is synthetic, so its `verified` state does not count as a CTF solve. A real adapter still needs provider response normalization, retry/cancel/deadline behavior, token usage and malformed call handling.
