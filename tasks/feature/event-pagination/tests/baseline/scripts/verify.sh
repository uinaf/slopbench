#!/bin/sh
set -eu

python -m unittest discover -s tests -v
python -m compileall -q src tests tools
