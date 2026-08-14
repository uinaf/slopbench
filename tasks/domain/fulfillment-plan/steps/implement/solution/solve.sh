#!/bin/sh
set -eu

phase=implement
targeted=true
if [ -n "${SLOPBENCH_TARGET_PHASE:-}" ] && [ "$phase" != "$SLOPBENCH_TARGET_PHASE" ]; then
  targeted=false
fi

if [ "$targeted" = true ] && [ -n "${SLOPBENCH_ATTACK_FIXTURE:-}" ]; then
  exec python /solution/attack.py
fi

variant=${SLOPBENCH_VARIANT:-oracle}
if [ "$targeted" = false ]; then
  variant=oracle
fi

case "$variant" in
  oracle) implementation=/solution/oracle.py ;;
  alternate) implementation=/solution/alternate.py ;;
  invalid) implementation=/solution/invalid.py ;;
  *) echo "unknown SLOPBENCH_VARIANT: ${SLOPBENCH_VARIANT}" >&2; exit 2 ;;
esac

cp "$implementation" /app/src/fulfillment.py
cd /app
python tools/write_slopbench_report.py
