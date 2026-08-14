# Limitations

The checked-in `0.2.0` evidence is provisional. Machine admission and adversarial fixture coverage
are complete, but owner approval, independent human review, expert calibration, private held-out
execution, signed five-trial references, release audits, and clean-room reproduction are not.
The generated readiness report is the authoritative blocker list.

The tasks are small synthetic repositories. They test selected coding-agent judgment boundaries;
they do not establish universal intelligence, production readiness, security certification,
design quality, or operational competence. Deterministic verifiers can still encode an incomplete
or incorrect task specification, which is why human admission remains mandatory.

The [agent-rule coverage manifest](../coverage/slopbench-swe-v1-dev-agent-rules.json) is the
authoritative scope map. Structured-owner integration, bounded retry and recovery, and structured
failure events are measured. Exception cause chains, mixed-success batches, and live
secret-bearing logger transports are only partially covered. Communication, delegation, external
approvals, interface quality, performance evidence, documentation style, technology choice, and
delivery workflow are outside the current score.

Model sampling and provider infrastructure can vary even with a fixed model identifier and trial
seed. Five trials measure observed reliability; they do not make inference deterministic.
Model identifiers are requested selection aliases, not attestations of provider-side weights or
routing. Harbor reports the selected model metadata from the run configuration; only the CLI
version is checked against an installed executable.

The agent CLI is installed during trusted setup with a separate, explicit network allowlist.
Codex and Claude Code request exact CLI versions. Harbor's current Cursor adapter installs the
current Cursor CLI rather than a versioned artifact; SlopBench forbids a configured Cursor
version override, records the CLI's detected version, and classifies any mismatch as a benchmark
defect, but this is not byte-for-byte binary
reproducibility. Setup scripts and their distribution endpoints remain supply-chain dependencies.

Local Docker is the reference execution venue. The Cloudflare Sandbox spike could start a
rootless Docker daemon but could not create the nested network namespace Harbor requires, so it is
not an equivalent benchmark venue. A future remote provider needs the same isolation and evidence
canaries before its results are comparable.

Cost may be absent when a harness or subscription does not expose authoritative USD usage. Missing
cost stays missing and can make a budgeted profile incomplete. Subscription-backed and API-backed
runs must not be compared as though their accounting were identical.

No minimal common or open harness is admitted yet. The omission is explicit because adding an
unstable harness would weaken reproducibility. SlopBench does not host a competitive leaderboard
in v1.
