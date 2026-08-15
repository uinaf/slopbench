# Review tasks

Review tasks inspect a sealed repository without changing tracked files. The agent writes
`slopbench-review.json` using `slopbench.review.v1`, then writes the ordinary revision-bound
SlopBench receipt. Each finding has a canonical repository-relative path, a one-based
`start_line`, a `line_count` from 1 through 10, a category, a severity, and a non-empty
explanation. Individual tasks may declare a smaller maximum line count.

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

Each task seals an adjudicated defect set, reasonable classification alternatives, accepted
locations, adjudicated false positives, location tolerance, maximum range width, and recall and
precision thresholds. The scorer applies these rules:

1. Normalize findings into a stable path, start line, line count, category, severity, and
   explanation order.
2. Consider a defect or false-positive target only when the path is exact, the submitted range is
   within tolerance of a canonical or accepted location, and the category/severity pair is
   canonical or an explicitly adjudicated equivalent.
3. Choose the target with the smallest range gap, then the closest range shape, then the canonical
   classification and location. Break remaining ties by target kind and ID. A defect and a finding
   can each match at most once.
4. Count another finding for an already matched defect as a duplicate false positive only when its
   path, range, category, and severity are identical to the first finding. Queue a non-identical
   overlap for human adjudication because the deterministic scorer cannot safely compare prose.
5. Count the closest match to an adjudicated false positive as a false positive.
6. Put every other unmatched finding in the versioned novel-finding queue. Novel findings do not
   affect the official score until a human adjudicates them into a future task version.

Recall is matched defects divided by adjudicated defects. Precision is matched defects divided by
matched defects plus adjudicated false positives and duplicates; an empty scored set has precision
one and recall zero. The pass gate uses recall and precision only. Category, severity, and exact
canonical classification calibration are reported separately over matched defects and do not turn
an accepted defect detection into a failure.

The checked-in `0.2.0` profiles use calibration only to refine the quality contribution of a
passed `requested_behavior` gate: 50% semantic detection, 25% canonical category calibration, and
25% canonical severity calibration. A failed gate still contributes zero. Exact canonical
classification remains diagnostic because weighting it would count category and severity twice.
Review calibration never changes the trial classification or reliability; an accepted semantic
match remains a valid pass.

Explanations are schema-checked but not interpreted. Semantic equivalence therefore has to be
represented by accepted structured classifications and locations in the sealed adjudication;
ambiguous unmatched prose remains in the novel queue instead of being guessed by an LLM judge.

The verifier publishes versioned score and novel-queue artifacts beside its normal evidence. A
defect-set or adjudication change requires a new task version and fresh admission evidence.
Missing or malformed submissions fail closed before score and novel-queue artifacts are emitted;
deterministic artifact comparison applies to completed scorer runs.
