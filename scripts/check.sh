#!/usr/bin/env bash
# Dubdeck local CI gate. Every refactor task must leave this green.
# Runs the full backend + frontend check suite; exits non-zero on the first failure.
# Forgejo has no CI runner; GitHub Actions arrives in Phase 8 and mirrors this script.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# gitleaks first: a leaked secret on a public remote can't be revoked by
# rewriting history alone — it must be rotated. Fail fast before any other
# check burns CI time on a commit that shouldn't ship.
#   install: curl -sSL https://github.com/gitleaks/gitleaks/releases/latest/download/gitleaks_$(curl -sSL https://api.github.com/repos/gitleaks/gitleaks/releases/latest | jq -r .tag_name | tr -d v)_linux_$(uname -m | sed 's/x86_64/x64/').tar.gz | tar -xz -C /tmp gitleaks && sudo mv /tmp/gitleaks /usr/local/bin/
echo "== repo: gitleaks =="
# `--exit-code 1` is required: gitleaks detect defaults to exit 0 even on
# findings, which would let leaks slip past the gate.
( cd "$repo_root" && gitleaks detect --source . --redact --exit-code 1 )

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
