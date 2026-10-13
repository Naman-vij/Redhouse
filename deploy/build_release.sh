#!/usr/bin/env bash
# build_release.sh - make redhouse-release.tgz, the one file you upload to AWS CloudShell.
# Run from the repo root (Git Bash on Windows):  bash deploy/build_release.sh
# Needs: node/npm and tar. It holds the server code, the built page, the AWS template and deploy.sh.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${1:-$ROOT/release}"
rm -rf "$OUT" && mkdir -p "$OUT/stage/backend" "$OUT/stage/engine" "$OUT/stage/scenarios"

echo "page"
( cd "$ROOT/frontend" && npm run build >/dev/null )
cp -r "$ROOT/frontend/dist" "$OUT/frontend-dist"

echo "server code"
cp -r "$ROOT/backend/app" "$OUT/stage/backend/app"
cp "$ROOT/backend/requirements-prod.txt" "$OUT/stage/backend/requirements.txt"
cp "$ROOT"/engine/*.py "$OUT/stage/engine/"
rm -f "$OUT"/stage/engine/make_test_*.py
cp "$ROOT"/scenarios/*.json "$OUT/stage/scenarios/"
find "$OUT/stage" -name "__pycache__" -type d -prune -exec rm -rf {} +
tar czf "$OUT/app.tgz" -C "$OUT/stage" .
rm -rf "$OUT/stage"

cp "$ROOT/deploy/template.yaml" "$ROOT/deploy/deploy.sh" "$ROOT/deploy/teardown.sh" "$ROOT/deploy/README.md" "$OUT/"
sed -i 's/\r$//' "$OUT/template.yaml" "$OUT/deploy.sh" "$OUT/teardown.sh"      # Windows line endings would break the scripts on Linux
TGZ="$(dirname "$OUT")/redhouse-release.tgz"
tar czf "$TGZ" -C "$OUT" .
echo "built $TGZ"
