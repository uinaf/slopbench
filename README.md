![slopbench — deterministic evaluation of coding-agent software judgment.](https://uinaf.dev/og/banner/slopbench.png)

# SlopBench

SlopBench evaluates coding-agent software judgment through versioned tasks, deterministic
verification, and evidence-bound result bundles. It keeps Harbor behind a thin runner boundary
and preserves the full gate outcome vector instead of reducing a run to one score.

## Install

Install [uv](https://docs.astral.sh/uv/) and provide a Docker engine, then sync the locked
environment:

```sh
uv sync --locked --all-groups
```

Python and uv versions are pinned in `.python-version` and the CI workflow.

## Run the tracer

The tracer matrix runs a repeated oracle, a distinct valid implementation, a known-invalid
implementation, and a no-op through Harbor:

```sh
make tracer
```

The command prints the artifact root. Every trial contains its pinned run manifest, generated
Harbor config, read-only task snapshot, Harbor evidence, agent receipt when present, verifier
evidence, and `slopbench.result.v1` bundle.

Run one manifest directly when debugging:

```sh
uv run slopbench run \
  --task tasks/tracer \
  --manifest runs/tracer/oracle.json \
  --output artifacts/manual
```

Exit `0` is a valid pass, `1` is a valid agent failure, and `2` is an invalid run, benchmark
defect, infrastructure failure, or rejected boundary input.

Run the sealed clean-room attacks separately from the baseline tracer matrix:

```sh
make hardening
```

The matrix exercises verifier and test tampering, hidden-material access, protected dependency
changes, hardcoded output, behavior bypass, fabricated receipts, unauthorized network use, and
grader exploitation. It uses the zero-cost Oracle harness.

## Verify

```sh
make verify
```

This checks formatting, lint, strict types, branch-aware coverage, the immutable task seal, every
run manifest, and generated-schema drift. Docker is only required for the end-to-end tracer
and hardening matrices.

## Contracts

- [Architecture](docs/ARCHITECTURE.md) explains trust boundaries, evidence flow, and failure
  classification.
- [Task schema](schemas/slopbench-task.schema.json) declares phases, capabilities, provenance,
  licensing, and immutable inputs.
- [Run schema](schemas/slopbench-run.schema.json) pins the agent and runtime configuration.
- [Receipt schema](schemas/slopbench-report.schema.json) carries claims, commands, uncertainty,
  and the final revision.
- [Result schema](schemas/slopbench-result.schema.json) records evidence and the gate vector.
- [Roadmap issue #1](https://github.com/uinaf/slopbench/issues/1) tracks the path from this tracer
  to the release corpus.

## License

SlopBench-authored code and task artifacts are available under the [MIT License](LICENSE).
