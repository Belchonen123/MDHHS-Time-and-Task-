#!/usr/bin/env bash
# Reviewer-ready ZIP of tracked sources only (`git archive`).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if [[ ! -d .git ]]; then
  echo "Not a git repository: $ROOT" >&2
  exit 1
fi
DATE="$(date +%Y-%m-%d)"
OUT="${1:-$ROOT/exports/mdhhs-poc-builder-review-$DATE.zip}"
mkdir -p "$(dirname "$OUT")"
git archive --format=zip --output="$OUT" HEAD
ls -lah "$OUT"
echo ""
echo "Excludes anything not committed. Do not commit PHI, storage/, or .env."
