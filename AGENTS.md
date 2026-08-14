# SlopBench agent guide

## Start and verify

- Install with `uv sync --locked --all-groups`.
- Run the repository gate with `make verify`.
- Run the Docker-backed deterministic proof with `make tracer`.
- Run the Docker-backed attack proof with `make hardening`.

## Sources of truth

- `src/slopbench/contracts.py` owns versioned wire contracts.
- `src/slopbench/runner.py` is the thin Harbor boundary and evidence finalizer.
- `tasks/<name>/slopbench-task.json` seals every task-owned input.
- `runs/` contains complete, non-secret run manifests.
- `docs/ARCHITECTURE.md` defines trust and classification boundaries.

## Task changes

After changing any file below `tasks/<name>/`, format first, then run
`uv run slopbench task seal tasks/<name>`. Update every bound run manifest with the new contract
hash and task digest, then run `make verify` and the relevant Harbor proof.

Keep credentials out of tasks, manifests, logs, fixtures, and committed results. Add agent or
environment behavior through Harbor configuration unless a repeated, documented blocker proves
the thin boundary insufficient. Every official task must keep its Harbor environment and verifier
offline, use a separate verifier, declare the exact agent allowlist, and carry deterministic
known-invalid fixtures for its task format.
