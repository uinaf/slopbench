# Results and lifecycle

SlopBench evaluates an immutable collection of trial bundles in two layers. The raw result vector
is profile-neutral and preserves evidence. A versioned profile derives an aggregate for one routing
or regression decision. No profile is the universal rank.

The checked-in `slopbench-swe-v1-dev` task set and all profiles are provisional `0.x` contracts.
The 12 public tasks have machine admission evidence, but independent human calibration and approval
remain pending. These files are development inputs, not a stable v1 release.

## Version bindings

`slopbench.task-set.v1` versions independently from runner code. Each entry binds the task ID,
task version and digest, contract path, category, kind, capability envelope, applicable gates,
provenance, and license. `make task-set` regenerates the public development manifest from the 12
sealed task contracts; `make verify` rejects drift.

`slopbench.evaluation.v1` binds:

- the task-set ID, version, and canonical digest;
- the profile ID, version, and canonical digest;
- the harness pin and adapter name, version, and non-secret settings;
- model, effort tier, non-secret settings, environment, tool pins, and credential variable names;
- every run manifest, raw result, optional agent report, and their SHA-256 digests; and
- a unique trial identity and pair index.

The evaluator rechecks every referenced file, live task seal, task digest, instruction-layer hash,
agent configuration, container image digest, resource limit, Harbor version and task checksum,
receipt digest, and run/result identity before producing
`slopbench.evaluation-result.v1`. The result embeds the exact task-set manifest and profile behind
their bindings, so later validation can recompute metrics and reject task coverage, digest, or gate
applicability drift without trusting the original evaluator process.

## Trial policy and raw outcomes

| Purpose | Trials per task | Intended use |
|---|---:|---|
| `smoke` | 1 | Wiring and contract checks |
| `calibration` | 3 | Task and verifier calibration |
| `comparison` | 5 | Published comparisons, bridge runs, and regression decisions |

Raw trials retain the complete deterministic gate vector, failed completion gates, failure class
and reason, uncertainty, evidence receipt, usage and cost, timing and latency, Harbor trajectory
hash, artifact hashes, complete agent configuration, runtime image pins, limits, and trial identity.
They also retain the complete task binding, adapter evidence, hashed instruction layers, and only
the names of credential variables, never their values. Repeated trials for one task must use the
same task binding, instructions, runtime, and limits. The evaluator orders trials by task and pair
index and hashes the complete raw vector. Changing a profile never changes that vector; it creates
a new profile binding and aggregate.

Reliability uses only agent-attributable evidence: valid passes, valid agent failures, and invalid
agent runs. Infrastructure failures and benchmark defects remain explicit in failure counts and in
the raw trials, but are reported as excluded reliability trials instead of being scored as agent
failures. A comparison containing either class is not publishable until replacement trials exist.

Compute a result from an immutable bundle:

```sh
uv run slopbench evaluate \
  --manifest release/evaluation.json \
  --task-set datasets/slopbench-swe-v1-dev.json \
  --profile profiles/balanced.json \
  --project-root . \
  --bundle-root artifacts/reference \
  --origin external \
  --output release/evaluation-result.json
```

External and maintainer-produced result files both carry `official: false`. Official status is a
separate trust decision produced only by successful attestation verification.

## Profiles

| Profile | Decision emphasis | Budget |
|---|---|---|
| `reliability-first` | Repeatable valid completion and core trust gates | None |
| `balanced` | Equal quality and reliability weighting | None |
| `cost-aware` | Balanced score with eligibility based on mean cost | USD 1 per trial |
| `fast-feedback` | Behavior and build feedback with latency eligibility | 300 seconds per trial |
| `altay` | Reliable, evidence-backed, scope-disciplined behavior | None; explicitly subjective |

Cost and latency remain separate reported dimensions. A declared budget changes only eligibility;
it is not folded into quality or reliability. Missing required usage produces `incomplete`, not a
free pass. The Altay profile records its non-sensitive source priorities and is labeled subjective.

## Active held-out disclosure

An active held-out result can be disclosed only from a five-trial comparison. The command
recomputes the aggregate before writing a whitelist-only document:

```sh
uv run slopbench disclose \
  --task-set private/held-out-task-set.json \
  --profile profiles/balanced.json \
  --result release/held-out-result.json \
  --project-root . \
  --output release/public-disclosure.json
```

The public document contains category counts, sanitized capability requirements, the scoring
contract, task-set version and digest, aggregate gate outcomes and failure classes, cost, latency,
and budget status. It never contains task IDs, paths, instructions, fixtures, patches, host
allowlists, environment names, detailed traces, artifact paths, or per-check output.

## Retirement and bridge runs

A task may retire only for `leakage`, `verifier_weakness`, `dependency_rot`, or
`major_task_set_release`. Every removed task identity needs a new same-category identity and a
publication record with HTTPS links for the retired task, fixtures, and reference runs. Published
provenance and license must equal the sealed retired contract.

Identity is the task ID plus digest. Only a `major_task_set_release` may preserve a stable task ID
while replacing its digest. Leakage, verifier weakness, and dependency rot require a new task ID;
an unchanged ID and digest can never be presented as a replacement.

Before retirement, run the old and replacement sets with the same configuration and profile using
five paired trials, then create and verify the bridge:

```sh
uv run slopbench bridge \
  --before-task-set release/held-out-0.1.0.json \
  --after-task-set release/held-out-0.2.0.json \
  --before-result release/result-0.1.0.json \
  --after-result release/result-0.2.0.json \
  --project-root . \
  --output release/bridge-0.1.0-to-0.2.0.json

uv run slopbench retirement \
  --manifest release/retirement-0.2.0.json \
  --bridge release/bridge-0.1.0-to-0.2.0.json \
  --before-task-set release/held-out-0.1.0.json \
  --after-task-set release/held-out-0.2.0.json \
  --before-result release/result-0.1.0.json \
  --after-result release/result-0.2.0.json \
  --project-root .
```

Validation reloads both comparison results and reconstructs the bridge before checking retirement.
For every unchanged task identity, the bridge also requires identical task bindings, instruction
layers, runtime pins, and limits on both sides. Replacement tasks may carry new task-specific pins.
Validation rejects lost category coverage, an unrecorded removal, a carried-over task presented as
a replacement, mismatched provenance or license, and any bridge or comparison-result digest
mismatch.

## Maintainer attestations

Maintainer reference results can be signed with an OpenSSH key. The private key is read only by
`ssh-keygen`; SlopBench stores the armored SSHSIG signature and never serializes the key.

```sh
uv run slopbench attestation sign \
  --evaluation release/evaluation.json \
  --result release/evaluation-result.json \
  --identity "$SLOPBENCH_SIGNING_KEY" \
  --signer maintainer@uinaf.dev \
  --output release/attestation.json

uv run slopbench attestation verify \
  --attestation release/attestation.json \
  --allowed-signers release/allowed_signers \
  --evaluation release/evaluation.json \
  --result release/evaluation-result.json \
  --output release/reference-verification.json
```

Verification reconstructs the canonical statement, checks both file digests, and asks
`ssh-keygen -Y verify` to authorize the named principal against the external `allowed_signers`
trust root. Only the resulting `slopbench.reference-verification.v1` document has status
`official`. An external bundle remains reproducible but unofficial.
