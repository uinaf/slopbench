#!/bin/sh
set -eu

if [ -n "${SLOPBENCH_ATTACK_FIXTURE:-}" ]; then
  exec python /solution/attack.py
fi

case "${SLOPBENCH_VARIANT:-oracle}" in
  oracle)
    implementation=/solution/implementation_oracle.py
    ;;
  alternate)
    implementation=/solution/implementation_alternate.py
    ;;
  invalid)
    implementation=/solution/implementation_invalid.py
    ;;
  *)
    echo "unknown SLOPBENCH_VARIANT: ${SLOPBENCH_VARIANT}" >&2
    exit 2
    ;;
esac

cp "$implementation" /app/src/eventlog.py
cd /app
python tools/write_slopbench_report.py
