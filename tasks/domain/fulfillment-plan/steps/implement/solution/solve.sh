#!/bin/sh
set -eu

if [ -n "${SLOPBENCH_ATTACK_FIXTURE:-}" ]; then
  exec python /solution/attack.py
fi

case "${SLOPBENCH_VARIANT:-oracle}" in
  oracle) implementation=/solution/oracle.py ;;
  alternate) implementation=/solution/alternate.py ;;
  invalid) implementation=/solution/invalid.py ;;
  *) echo "unknown SLOPBENCH_VARIANT: ${SLOPBENCH_VARIANT}" >&2; exit 2 ;;
esac

cp "$implementation" /app/src/fulfillment.py
cd /app
python tools/write_slopbench_report.py
