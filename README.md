![uinaf/slopbench — deterministic evaluation of coding-agent software judgment.](https://uinaf.dev/og/banner/slopbench.png)

# uinaf/slopbench

`uinaf/slopbench` evaluates coding-agent software judgment with versioned tasks,
deterministic verification, and evidence-bound result bundles. It keeps Harbor behind a thin
runner boundary and preserves the complete gate outcome vector instead of reducing a run to one
score.

## Status

The checked-in `slopbench-swe-v1-dev` task set is a provisional `0.x` development corpus. Its 12
public synthetic tasks have deterministic machine-admission and adversarial evidence. The
[readiness report](release/slopbench-swe-v1-dev-readiness.json) records the human calibration,
signed reference, held-out, audit, and clean-reproduction evidence required before a stable v1
release.

## Install and verify

Install [uv](https://docs.astral.sh/uv/) and provide a Docker engine. Python and uv versions are
pinned in `.python-version` and the verification workflow.

```sh
uv sync --locked --all-groups
make verify
```

`make verify` checks formatting, lint, strict types, branch-aware coverage, task seals, run
manifests, profiles, reference configurations, generated task-set and release evidence, the
readiness report, and schema drift. It does not require Docker.

## Run the deterministic proofs

```sh
make tracer
make hardening
make corpus
```

- `make tracer` runs repeated oracle, distinct-valid, known-invalid, and no-op trials through
  Harbor.
- `make hardening` exercises every sealed attack fixture, including verifier tampering, hidden
  material, unauthorized network use, fabricated receipts, and grader exploitation.
- `make corpus` applies the deterministic admission matrix to the public development corpus.

These commands use the zero-cost Oracle harness and print or create their artifact roots. A trial
bundle contains the pinned run manifest, generated Harbor configuration, read-only task snapshot,
Harbor and verifier evidence, optional agent receipt, and `slopbench.result.v1` result.

Run one manifest directly when debugging:

```sh
uv run slopbench run \
  --task tasks/tracer \
  --manifest runs/tracer/oracle.json \
  --output artifacts/manual
```

Exit `0` is a valid pass, `1` is a valid agent failure, and `2` is an invalid run, benchmark
defect, infrastructure failure, or rejected boundary input.

Regenerate the provisional public task set, profiles, reference configurations, and release
readiness evidence with:

```sh
make task-set profiles reference-configurations release-candidate
```

## Documentation

- [Architecture](docs/ARCHITECTURE.md): trust boundaries, evidence flow, and failure
  classification.
- [Methodology](docs/METHODOLOGY.md): admission, execution, scoring, and release policy.
- [Agent-rule coverage](coverage/slopbench-swe-v1-dev-agent-rules.json): measured, partial, and
  out-of-scope behavior families for the current task set.
- [Reproduction guide](docs/REPRODUCING.md): local proofs, reference trials, and release audits.
- [Results and lifecycle](docs/RESULTS.md): task-set versions, profiles, held-out disclosure,
  retirement bridges, and attestations.
- [Review-task contract](docs/REVIEW_TASKS.md): finding taxonomy and deterministic matching.
- [Limitations](docs/LIMITATIONS.md): current evidence and interpretation boundaries.
- [Schemas](schemas/): versioned coverage, task, run, receipt, review, result, evaluation, and
  release contracts.

## Contributing and security

Run `make verify` before opening a pull request and follow the uinaf organization
[contribution guidance](https://github.com/uinaf/.github/blob/main/CONTRIBUTING.md). Report
suspected vulnerabilities privately according to the uinaf
[security policy](https://github.com/uinaf/.github/blob/main/SECURITY.md).

## License

Code and task artifacts authored by `uinaf/slopbench` are available under the
[MIT License](LICENSE).
