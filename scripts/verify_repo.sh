#!/usr/bin/env bash
# verify_repo.sh: the single repository gate. Needs neither `cq` nor the private denylist, so CI
# runs this same file; the hook test runs only where `cq` is installed. Zero errors is green.
set -Eeuo pipefail
cd "$(git rev-parse --show-toplevel)"

step() { echo "==> $1"; shift; "$@"; }

step "shell syntax" bash -c 'for f in scripts/*.sh scripts/githooks/*; do bash -n "$f"; done'
if command -v cq >/dev/null 2>&1; then
  step "leak gate behaviour" bash scripts/test-leak-gate.sh
else
  echo "==> leak gate behaviour: skipped (cq not on PATH; the hooks need it)"
fi
step "leak-generic behaviour" bash scripts/test-leak-generic.sh
step "leak gate (generic patterns)" bash scripts/leak-generic.sh
if command -v docker >/dev/null 2>&1; then
  step "compose file" docker compose --env-file .env.example config -q
  step "compose server overlay" docker compose --env-file .env.example -f compose.yaml -f compose.server.yaml config -q
else
  echo "==> compose file: skipped (docker not on PATH)"
fi
step "router and stub unit tests" bash -c 'python3 -B -m unittest discover -s local/router -p "test_*.py" && python3 -B -m unittest discover -s local/stub -p "test_*.py"'
step "gateway and registry unit tests" bash -c 'python3 -B -m unittest discover -s server/egress -p "test_*.py" && python3 -B -m unittest discover -s server/mcp -p "test_*.py"'
echo "verify_repo: green"
