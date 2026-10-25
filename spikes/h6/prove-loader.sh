#!/usr/bin/env bash
# prove-loader.sh: the pinned Omnigent server's own loader resolves the two H6 spike policies from
# spikes/h6/policy/config.yaml and synthetic events return the expected verdicts. One container,
# `docker run --rm`, no compose stack, no volume, no port, no model call, no Databricks.
# The image is the one pinned in compose.yaml; it is removed afterwards only if this run pulled it.
set -Eeuo pipefail
cd "$(git rev-parse --show-toplevel)"

MIN_FREE_GB=8        # abort when fewer GB than this would remain after the pull
PULL_ESTIMATE_GB=2   # upper estimate for the image when it is not present yet

free_gb() { df -Pk . | awk 'NR==2 {printf "%d", $4/1048576}'; }
fail() { echo "prove-loader: FAIL: $*" >&2; exit 1; }

command -v docker >/dev/null || fail "docker is required"
IMAGE=$(awk '/^  omnigent:/ {f=1} f && $1=="image:" {print $2; exit}' compose.yaml)
[ -n "$IMAGE" ] || fail "no omnigent image pinned in compose.yaml"

PULLED=0
docker image inspect "$IMAGE" >/dev/null 2>&1 || PULLED=1
FREE_BEFORE=$(free_gb)
echo "disk before: ${FREE_BEFORE}G free; image $IMAGE; pull needed: $PULLED"
if [ "$PULLED" = 1 ] && [ $((FREE_BEFORE - PULL_ESTIMATE_GB)) -lt "$MIN_FREE_GB" ]; then
  fail "only ${FREE_BEFORE}G free; the pull would leave under ${MIN_FREE_GB}G"
fi

cleanup() {
  code=$?
  trap - EXIT
  # Safety only: this proof starts no stack, so this is a no-op unless a smoke stack is left up.
  if [ -n "$(docker compose ps -q 2>/dev/null)" ]; then docker compose down -v --remove-orphans >/dev/null 2>&1 || true; fi
  if [ "$PULLED" = 1 ]; then docker image rm "$IMAGE" >/dev/null 2>&1 && echo "removed pulled image $IMAGE"; fi
  echo "disk after: $(free_gb)G free"
  exit "$code"
}
trap cleanup EXIT

docker run --rm -i \
  -v "$PWD/spikes/h6/policy:/policy:ro" \
  -e OMNIGENT_CONFIG=/policy/config.yaml -e PYTHONPATH=/policy \
  --entrypoint python "$IMAGE" - <<'PY' | sed 's/^/  /' || fail "loader proof failed"
import sys

from omnigent.policies.function import resolve_function_policy
from omnigent.policies.registry import is_registered_handler, load_registry
from omnigent.server.server_config import load_server_config
from omnigent.spec.parser import parse_default_policies

cfg = load_server_config()
load_registry(extra_modules=cfg.get("policy_modules") or [])
specs = {s.name: s for s in parse_default_policies(cfg.get("policies"))}
want = ["h6__compute_guard", "h6__column_override_guard"]
missing = [n for n in want if n not in specs]
if missing:
    sys.exit("missing policies: " + ", ".join(missing))
for n in want:
    if not is_registered_handler(specs[n].function.path):
        sys.exit("handler not in the registry: " + n)
ev = {n: resolve_function_policy(specs[n])._callable for n in want}


def call(name, event):
    out = ev[name](event)
    return out["result"] if out else None


def shell(cmd):
    return {"type": "tool_call", "target": "Bash", "data": {"name": "Bash", "arguments": {"command": cmd}}}


checks = [
    ("compute denies an unlisted cluster", "h6__compute_guard",
     shell("synthcli compute run --cluster-id team-b-adhoc"), "DENY"),
    ("compute denies a create", "h6__compute_guard", shell("synthcli compute create --name scratch"), "DENY"),
    ("compute allows an approved cluster", "h6__compute_guard",
     shell("synthcli compute run --cluster-id team-a-shared"), None),
    ("compute ignores an unrelated command", "h6__compute_guard", shell("git status"), None),
    ("column denies an unregistered override", "h6__column_override_guard",
     shell("synthcli model apply --override-column sales.margin=0.2"), "DENY"),
    ("column allows a registered override", "h6__column_override_guard",
     shell("synthcli model apply --override-column sales.channel=web"), None),
]
bad = 0
for label, name, event, want_ in checks:
    got = call(name, event)
    print(("ok: policy " if got == want_ else "FAIL: policy ") + label)
    bad += got != want_
sys.exit(1 if bad else 0)
PY
echo "prove-loader: ok: the pinned loader resolves both H6 policies and the verdicts match"
