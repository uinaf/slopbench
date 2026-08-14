# Structured review output

Write `slopbench-review.json` with exactly these top-level fields:

```json
{
  "schema_version": "slopbench.review.v1",
  "task_id": "slopbench/review/webhook-dispatch",
  "task_digest": "the SLOPBENCH_TASK_DIGEST value",
  "base_revision": "the git rev-parse HEAD value",
  "findings": []
}
```

Each finding contains `path`, `start_line`, `line_count`, `category`, `severity`, and a non-empty
`explanation`. `line_count` must be from one through five.

Categories are `api_contract`, `concurrency`, `correctness`, `data_integrity`, `error_handling`,
`resource_lifecycle`, and `security`. Severities are `critical`, `high`, `medium`, and `low`.
Classify observable impact, not patch size: critical means broad compromise or irreversible loss;
high means serious loss, exposure, or sustained outage; medium means a material bounded failure;
low means narrow impact with straightforward recovery.

Do not report style, preference, tone, or speculative hardening. Equivalent explanations are not
distinguished by scoring.
