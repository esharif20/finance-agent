#!/usr/bin/env bash
# Full CI-equivalent, run once before a PR. Holds a lock shared by every worktree
# so only one full suite runs at a time on this Mac (16 GB M1 Pro).
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
lock="$(git rev-parse --git-common-dir)/full-suite.lock"
until mkdir "$lock" 2>/dev/null; do
  pid=$(cat "$lock/pid" 2>/dev/null || true)
  if [ -n "$pid" ] && ! kill -0 "$pid" 2>/dev/null; then rm -rf "$lock"; continue; fi  # stale lock
  echo "check-all: another full suite is running (pid ${pid:-?}); waiting..." >&2
  sleep 15
done
echo $$ > "$lock/pid"
trap 'rm -rf "$lock"' EXIT

uv sync --locked
uv run python scripts/fixture_guard.py
uv run ruff check .
uv run pyright
uv run lint-imports
uv run pytest -q
uv run python evals/categoriser.py
pnpm --dir web install --frozen-lockfile
pnpm --dir web run gen
git diff --exit-code -- web/openapi.json web/src/api/schema.d.ts
pnpm --dir web run build
pnpm --dir web run test
if command -v gitleaks >/dev/null; then gitleaks git . --no-banner --redact; else echo "check-all: gitleaks not installed, CI runs it" >&2; fi
echo "check-all: all green"
