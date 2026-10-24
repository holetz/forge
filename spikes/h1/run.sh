#!/usr/bin/env bash
# Spike H1 driver.
#   run.sh [--dry-run] <arm>...   run the three H1 suites on each arm (same cases, same grader)
#   run.sh --agent <args>         one invocation: mint a fresh token, run agent.sh (used by the profiles)
# Arms: opus-5-5 sonnet-5-5 haiku-5-5 deepseek-v4-1-flash glm-5-3-flash
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
profile="${H1_PROFILE:-ia}"
if [[ "${1:-}" == "--agent" ]]; then
  shift
  DATABRICKS_TOKEN="$(databricks auth token -p "$profile" --force-refresh -o json \
    | python3 -c 'import json,sys;print(json.load(sys.stdin)["access_token"])')"
  export DATABRICKS_TOKEN
  exec bash "$here/agent.sh" "$@"
fi
clone="${QUENCHING_CLONE:-/tmp/forge-h1/quenching}"
grader="${H1_GRADER:-databricks/databricks-claude-opus-5-5}"
results="${H1_RESULTS:-$here/results}"
dry=()
if [[ "${1:-}" == "--dry-run" ]]; then dry=(--dry-run); shift; fi
(( $# )) || set -- opus-5-5
export H1_RUN="$here/run.sh"
evals="$clone/plugins/quenching/assets/evals"
for arm in "$@"; do
  case "$arm" in
    opus-*|sonnet-*|haiku-*) model="databricks/databricks-claude-$arm" ;;
    *) model="databricks-oai/databricks-$arm" ;;
  esac
  for suite in specs/create git/commit knowledge/define; do
    python3 "$clone/scripts/run_evals.py" "${dry[@]}" --harnesses "$here/eval_harnesses.json" \
      --harness "h1-${suite/\//-}" --grader-harness h1-grader --grader-model "$grader" \
      --model "$model" --run-id "h1-$arm" --suite "$evals/$suite" \
      --timeout "${H1_TIMEOUT:-900}" --extra-arg=--auto --out "$results/$arm"
    [[ ${#dry[@]} -gt 0 ]] || mv "$results/$arm/summary.json" "$results/$arm/${suite/\//-}.summary.json"
  done
done
