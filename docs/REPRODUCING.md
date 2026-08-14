# Reproducing SlopBench

## Machine verification

Install the pinned Python environment and run the repository gate:

```sh
uv sync --locked --all-groups
make verify
```

This validates code, schemas, task seals, utility run manifests, profiles, the generated task set,
agent-rule coverage, reference configurations, release evidence, and the deterministic readiness
report without model spend.

With Docker available, run the zero-cost execution proofs:

```sh
make tracer
make hardening
make corpus
```

These commands create untracked artifact directories. They repeat oracle runs, accept distinct
valid solutions, reject no-ops and known-invalid solutions, and exercise every sealed attack
fixture.

## Generate reference trials

Read the Docker server version, then generate one Cursor smoke manifest with that exact value:

```sh
docker version --format '{{.Server.Version}}'
```

```sh
uv run python scripts/generate-reference-runs.py \
  --configuration reference-configurations/cursor-grok-4.6-medium.json \
  --purpose smoke \
  --environment-provider-version <docker-server-version> \
  artifacts/reference-manifests \
  tasks/diagnosis/query-cache-key
```

Run it only with the named credential present in the process environment:

```sh
uv run slopbench run \
  --task tasks/diagnosis/query-cache-key \
  --manifest artifacts/reference-manifests/diagnosis-query-cache-key/trial-1.json \
  --output artifacts/reference-smoke
```

Provide credentials through an approved secret process. Do not write their values into files,
shell history, task inputs, manifests, or logs. The pinned configurations name the required
variables:

- Cursor: `CURSOR_API_KEY`;
- Codex subscription auth: `CODEX_AUTH_JSON_PATH`; and
- Claude subscription auth: `CLAUDE_CODE_OAUTH_TOKEN` plus `CLAUDE_FORCE_OAUTH=1`.

Generate `comparison` manifests for five matched trials. After all raw bundles exist, create an
evaluation manifest that binds every run, result, optional receipt, and digest. Recompute the
result with `slopbench evaluate`, then sign and verify a maintainer reference as described in
[Results and lifecycle](RESULTS.md).

## Audit the release candidate

Regenerate the evidence and readiness report:

```sh
make release-candidate
```

The command does not convert missing evidence into a pass. A stable release must have a
`slopbench.release-readiness.v1` report with `stable_eligible: true`, no blockers, and a `1.x`
release-evidence manifest. Run the same commands in a clean checkout with only public inputs and
separately authorized held-out access before recording the clean-reproduction audit.
