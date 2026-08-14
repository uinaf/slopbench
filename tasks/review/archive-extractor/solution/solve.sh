#!/bin/sh
set -eu

if [ -n "${SLOPBENCH_ATTACK_FIXTURE:-}" ]; then
  exec python /solution/attack.py
fi

case "${SLOPBENCH_VARIANT:-oracle}" in
  oracle) review=/solution/oracle.json ;;
  alternate) review=/solution/alternate.json ;;
  invalid) review=/solution/invalid.json ;;
  *) echo "unknown SLOPBENCH_VARIANT: ${SLOPBENCH_VARIANT}" >&2; exit 2 ;;
esac

python /solution/write_review.py "$review"
cd /app
python tools/write_slopbench_report.py
