#!/usr/bin/env bash
# Dubdeck local CI gate. Every refactor task must leave this green.
# Runs the full backend + frontend check suite; exits non-zero on the first failure.
# Forgejo has no CI runner; GitHub Actions arrives in Phase 8 and mirrors this script.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "== backend: ruff check =="
( cd "$repo_root/backend" && uv run ruff check . )

echo "== backend: ruff format --check =="
( cd "$repo_root/backend" && uv run ruff format --check . )

echo "== backend: pytest =="
( cd "$repo_root/backend" && uv run pytest )

echo "== frontend: lint =="
( cd "$repo_root/frontend" && npm run lint )

echo "== frontend: tsc =="
( cd "$repo_root/frontend" && npx tsc -b )

echo "== frontend: vitest =="
( cd "$repo_root/frontend" && npx vitest run )

echo "== frontend: build =="
( cd "$repo_root/frontend" && npm run build )

echo "All checks passed."
