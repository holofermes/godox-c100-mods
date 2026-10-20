#!/bin/bash
# Build, test and stage the release files in dist/: GP_CARDVR_UPGRADE.BIN and SHA256SUMS.
# Publishing is a separate step; the gh command is printed at the end.
# usage: ./release.sh STOCK.BIN
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
[ $# -eq 1 ] || { echo "usage: $0 STOCK.BIN" >&2; exit 1; }
cd "$here"
version=$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml)

./build.sh "$1"
uv run --group test python tests/test_v5.py build/GP_CARDVR_UPGRADE.BIN obj/v5.sym

rm -rf dist
mkdir dist
cp build/GP_CARDVR_UPGRADE.BIN dist/
(cd dist && sha256sum GP_CARDVR_UPGRADE.BIN >SHA256SUMS)
cat dist/SHA256SUMS
echo "staged v$version in dist/. To publish:"
echo "  gh release create v$version dist/GP_CARDVR_UPGRADE.BIN dist/SHA256SUMS --title v$version --notes-file <notes>"
