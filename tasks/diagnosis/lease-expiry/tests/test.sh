#!/bin/sh
set -eu

rm -rf /app/.git
cp -R /baseline/.git /app/.git
mkdir -p /trusted-logs
chown -R root:root /app /baseline /tests /logs/verifier /trusted-logs
chmod -R a+rX,go-w /app /baseline /tests
chmod 700 /trusted-logs
chmod 755 /tests /logs/verifier
exec python /tests/verify.py
