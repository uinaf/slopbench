# Review tasks

Review tasks inspect a sealed repository without changing tracked files. The agent writes
`slopbench-review.json` using `slopbench.review.v1`, then writes the ordinary revision-bound
SlopBench receipt. Each finding has a repository-relative file, a one-based line or tight range,
a category, a severity, and a non-empty explanation.

## Taxonomy

| Category | Use |
|---|---|
| `api_contract` | The implementation violates a declared caller or protocol contract |
| `concurrency` | Interleaving or synchronization can produce an incorrect result |
| `correctness` | Deterministic behavior is wrong outside a narrower category |
| `data_integrity` | Durable or user-visible data can be lost, duplicated, or corrupted |
| `error_handling` | A failure is suppressed, misclassified, or made unrecoverable |
| `resource_lifecycle` | Bounded resources, cleanup, or ownership are handled incorrectly |
| `security` | An attacker can cross a trust or authorization boundary |

| Severity | Meaning |
|---|---|
| `critical` | Broad compromise or irreversible loss is immediately reachable |
| `high` | A supported path can cause serious loss, exposure, or sustained outage |
| `medium` | A supported path fails materially with bounded impact or recovery |
| `low` | The defect is real but has narrow impact and a straightforward recovery |

Fixture authors classify the observable impact, not the size of a possible patch. Style,
preference, tone, and speculative hardening are not findings.

## Deterministic scoring

Each task seals an adjudicated defect set, adjudicated false positives, location tolerance, maximum
range width, and recall and precision thresholds. The scorer applies these rules:

1. Normalize findings into a stable path, line, category, severity, and explanation order.
2. Match a finding only when path, category, and severity are exact and its range overlaps the
   adjudicated range within the declared line tolerance.
3. Choose the nearest unmatched defect, breaking ties by defect ID. A defect and a finding can each
   match at most once.
4. Count another finding for an already matched defect as a duplicate false positive.
5. Count a match to an adjudicated false positive as a false positive.
6. Put every other unmatched finding in the versioned novel-finding queue. Novel findings do not
   affect the official score until a human adjudicates them into a future task version.

Recall is matched defects divided by adjudicated defects. Precision is matched defects divided by
matched defects plus adjudicated false positives and duplicates; an empty scored set has precision
one and recall zero. Explanations are schema-checked but not graded for wording, so equivalent
explanations and tolerated locations receive the same outcome.

The verifier publishes versioned score and novel-queue artifacts beside its normal evidence. A
defect-set or adjudication change requires a new task version and fresh admission evidence.
