#!/bin/sh
set -eu

output=${1:-"artifacts/tracer-$(date -u +%Y%m%dT%H%M%SZ)"}

run_expected() {
  expected=$1
  manifest=$2
  destination=$3
  if uv run slopbench run \
    --task tasks/tracer \
    --manifest "$manifest" \
    --output "$destination"; then
    actual=0
  else
    actual=$?
  fi
  if [ "$actual" -ne "$expected" ]; then
    echo "unexpected exit for $manifest: expected $expected, got $actual" >&2
    exit 2
  fi
}

run_expected 0 runs/tracer/oracle.json "$output/oracle-a"
run_expected 0 runs/tracer/oracle.json "$output/oracle-b"
run_expected 0 runs/tracer/alternate.json "$output/alternate"
run_expected 1 runs/tracer/invalid.json "$output/invalid"
run_expected 1 runs/tracer/nop.json "$output/nop"

oracle_run_id=$(uv run python -c \
  'import json, sys; print(json.load(open(sys.argv[1]))["run_id"])' \
  runs/tracer/oracle.json)
oracle_a="$output/oracle-a/$oracle_run_id/harbor/$oracle_run_id"
oracle_b="$output/oracle-b/$oracle_run_id/harbor/$oracle_run_id"
cmp "$oracle_a/artifacts/app/slopbench-report.json" \
  "$oracle_b/artifacts/app/slopbench-report.json"
cmp "$oracle_a/verifier/slopbench-verification.json" \
  "$oracle_b/verifier/slopbench-verification.json"

echo "$output"
