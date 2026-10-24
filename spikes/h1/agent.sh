#!/usr/bin/env bash
# Run ONE agent invocation inside a fresh, disposable container.
# The only bind mount is the case's fixture directory: the parent of the current directory when
# that is the runner's `eval-*/workspace` (it also holds the `origin.git` setup.sh creates),
# else the current directory itself. Arguments go to `opencode run` unchanged; the token travels
# by name (-e DATABRICKS_TOKEN), never in argv.
set -euo pipefail
image="${H1_IMAGE:-forge-h1-opencode:local}"
host="${DATABRICKS_HOST:?set DATABRICKS_HOST to the workspace URL, e.g. from databricks auth env -p <profile>}"
: "${DATABRICKS_TOKEN:?DATABRICKS_TOKEN is not set}"
work="$PWD"
mount="$work"
case "$(basename "$(dirname "$work")")" in eval-*) mount="$(dirname "$work")" ;; esac
exec docker run --rm -i --network bridge \
  --user "$(id -u):$(id -g)" -e HOME=/tmp/h1home \
  -e DATABRICKS_TOKEN -e "DATABRICKS_HOST=$host" \
  -e GIT_AUTHOR_NAME="Eval fixture" -e GIT_AUTHOR_EMAIL=eval@example.com \
  -e GIT_COMMITTER_NAME="Eval fixture" -e GIT_COMMITTER_EMAIL=eval@example.com \
  -e EVAL_CASE -e EVAL_BASE \
  --mount "type=bind,source=$mount,target=$mount" -w "$work" \
  --label forge.spike=h1 "$image" "$@" </dev/null
