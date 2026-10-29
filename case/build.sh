#!/bin/bash
# Build a design: model, run the checks, export to out/<design>-NN/.
# Usage: ./build.sh <design> [--set key=value ...] [--parts FILE] [--keys]
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
cd "$here"
exec uv run python -m lib.build "$@"
