# Structured review output

Write `slopbench-review.json` with exactly these top-level fields:

```json
{
  "schema_version": "slopbench.review.v1",
  "task_id": "slopbench/review/archive-extractor",
  "task_digest": "the SLOPBENCH_TASK_DIGEST value",
  "base_revision": "the git rev-parse HEAD value",
  "findings": []
}
```

Each finding contains `path`, `start_line`, `end_line`, `category`, `severity`, and a non-empty
`explanation`. Ranges may span at most five lines.

Categories are `api_contract`, `concurrency`, `correctness`, `data_integrity`, `error_handling`,
`resource_lifecycle`, and `security`. Severities are `critical`, `high`, `medium`, and `low`.
Classify observable impact, not patch size: critical means broad compromise or irreversible loss;
high means serious loss, exposure, or sustained outage; medium means a material bounded failure;
low means narrow impact with straightforward recovery.

Do not report style, preference, tone, or speculative hardening. Equivalent explanations are not
distinguished by scoring.
