# Methodology

SlopBench SWE evaluates whether a coding agent can make a bounded software change, preserve
supported behavior, respect authority, and produce evidence that agrees with an independent
verifier. Official scoring is deterministic. An LLM never judges the agent output.

## Corpus

The public development corpus contains 12 synthetic, SlopBench-authored tasks: two each for
diagnosis and repair, feature work, restraint and scope, composition and domain evolution, state
and effects, and code review. Review tasks use a read-only repository and structured finding
matching. Patch tasks use a writable repository. Four of the ten patch tasks have ordered phases:
two have two phases and two have three. Each later phase receives the prior repository result
through a fresh agent context.

Each task contract binds its instructions, environment, verifier, capabilities, gates,
provenance, license, valid alternatives, and adversarial fixtures. The task digest changes when
any sealed input changes.

## Agent-rule coverage

The versioned [agent-rule coverage manifest](../coverage/slopbench-swe-v1-dev-agent-rules.json)
maps sanitized behavior families to exact task IDs and applicable gates. `measured` means the
listed task evidence directly contributes to that rule, `partial` names the uncovered remainder,
and `out_of_scope` carries no task or gate attribution. The manifest binds the task-set digest and
is itself bound by release evidence, so `make verify` rejects stale mappings.

The source revision is an opaque content digest of the sanitized guidance snapshot. The manifest
does not import machine-local instructions into task prompts, add a universal style score, or
change profile weights. Only the listed gates affect evaluation results.
Maintainers update the mapping, source revision, and coverage version together when that guidance
changes; portable release verification does not fetch the external source.

## Admission

Machine admission requires all of the following:

- two byte-stable oracle passes;
- rejection of a no-op;
- acceptance of a materially different valid implementation;
- rejection of every known-invalid or reward-hacking fixture on its declared gates;
- stable verifier evidence and Harbor rewards;
- prompt and check agreement;
- no unnecessary implementation constraint; and
- explicit capabilities and applicable gates.

Machine evidence is necessary but insufficient. Stable admission also requires named owner
approval and an accepted review from a human other than the task owner. Expert humans must run a
subset covering every capability category and resolve realism, solvability, or verifier
objections.

## Execution boundary

Every run binds the task digest, instruction hashes, harness and model, Harbor adapter, runtime
images, resources, time limits, trial identity, credential variable names, and network hosts.
Credential values never enter the manifest.

The task environment starts offline. During trusted adapter setup, only the configuration's
declared installation hosts are reachable. During agent execution, Harbor replaces that policy
with the smaller declared model-transport allowlist. A separate verifier environment has an
explicit offline baseline and receives no credentials. Finalization compares Harbor's reported
harness name, detected or installed CLI version, and selected model metadata with the requested
pins before attributing a result to the agent.

## Trial and scoring policy

Smoke runs use one trial per task, calibration runs use three, and comparisons use five matched
trials per task and configuration. Published reference comparisons must bind the same task set,
profile, pair indices, and trial seeds across harnesses. A raw result preserves every gate,
classification, receipt, uncertainty, usage field, duration, configuration pin, and artifact
digest. Infrastructure failures and benchmark defects remain visible but are excluded from agent
reliability.

Profiles derive aggregates from the same immutable raw vector. Quality, reliability, cost, and
latency remain separate dimensions. A profile budget changes eligibility and never silently
changes quality or reliability.

## Regression policy

A regression report flags a task that previously passed five of five trials when the new result
has at least two agent-attributable failures. It also flags the first new failure of the authority
or safety/type-escape gate in attributable evidence. Both results must use the same task/pair
trial seeds. Reports are advisory for v1: `automatic_release_blocking` is always false until
enough baseline history exists for profile-specific policy.

## Release policy

`v0.x` is provisional. A `v1.x` release is valid only when the generated readiness report has no
blockers. That requires owner approvals, independent human reviews, expert calibration, complete
attack coverage, independent release audits, an active private held-out set, verified signed
five-trial references for every pinned configuration, and a clean reproduction. Cross-version
claims require a bridge report.

Active held-out tasks, IDs, paths, prompts, fixtures, and per-task results remain private. The
public disclosure contract permits only task-set binding, category counts, sanitized capability
classes, scoring rules, and aggregate metrics.
