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
run manifest, all five profiles, all pinned reference configurations, deterministic task-set and
release-evidence regeneration, the readiness report, and generated-schema drift. Docker is only
required for the end-to-end tracer, hardening, and corpus matrices.

Regenerate and validate the provisional 12-task public development set independently:

```sh
make task-set profiles reference-configurations release-candidate
```

The development set is versioned `0.x`. Its machine admission and adversarial evidence are
complete. The checked-in [readiness report](release/slopbench-swe-v1-dev-readiness.json) keeps
owner approval, independent human and expert calibration, signed five-trial references, release
audits, and held-out execution explicit as blockers; it is not a stable benchmark release.

## Contracts

- [Architecture](docs/ARCHITECTURE.md) explains trust boundaries, evidence flow, and failure
  classification.
- [Methodology](docs/METHODOLOGY.md), [limitations](docs/LIMITATIONS.md), and
  [reproduction](docs/REPRODUCING.md) define the provisional release boundary and the evidence
  still required for v1.
- [Results and lifecycle](docs/RESULTS.md) defines task-set versions, profiles, trial policy,
  held-out disclosure, retirement bridges, and maintainer attestations.
- [Review tasks](docs/REVIEW_TASKS.md) defines the finding taxonomy and deterministic matching
  rules.
- [Task schema](schemas/slopbench-task.schema.json) declares phases, capabilities, provenance,
  licensing, and immutable inputs.
- [Run schema](schemas/slopbench-run.schema.json) pins the agent and runtime configuration.
- [Receipt schema](schemas/slopbench-report.schema.json) carries claims, commands, uncertainty,
  and the final revision.
- [Review schema](schemas/slopbench-review.schema.json) defines structured review-only findings.
- [Result schema](schemas/slopbench-result.schema.json) records evidence and the gate vector.
- [Task-set schema](schemas/slopbench-task-set.schema.json),
  [profile schema](schemas/slopbench-profile.schema.json), and
  [evaluation schemas](schemas/slopbench-evaluation.schema.json) define reproducible suite
  computation without tying dataset versions to runner releases.
- [Reference configuration](schemas/slopbench-reference-configuration.schema.json),
  [release evidence](schemas/slopbench-release-evidence.schema.json),
  [release readiness](schemas/slopbench-release-readiness.schema.json), and
  [regression](schemas/slopbench-regression.schema.json) schemas define pinned harnesses and the
  provisional-to-stable gate.
- [Roadmap issue #1](https://github.com/uinaf/slopbench/issues/1) tracks the path from this tracer
  to the release corpus.

## License

SlopBench-authored code and task artifacts are available under the [MIT License](LICENSE).
