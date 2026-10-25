#!/usr/bin/env bash
# test-overlay-check.sh: prove overlay-check.py accepts a minimal valid overlay and refuses each
# kind of violation. Overlays are built in a temp directory; no value here is real.
set -Euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
fail=0
check() { # check <label> <expected-exit> <actual-exit>
  if [ "$2" = "$3" ]; then echo "ok   $1"; else echo "FAIL $1 (expected $2, got $3)"; fail=1; fi
}
run() { python3 -I "$HERE/overlay-check.py" "$1" >"$TMP/out" 2>&1; }

make() { # make <dir>: a minimal valid overlay
  local d="$1"
  mkdir -p "$d/agents"
  echo '{"contract": 1, "name": "demo"}' >"$d/overlay.json"
  echo 'services: {}' >"$d/compose.overlay.yaml"
  printf 'OVERLAY_NAME=demo\nFORGE_DEFAULT_MODEL=demo-model\nEGRESS_GIT_TOKEN=\n' >"$d/overlay.env"
  cat >"$d/upstreams.json" <<'JSON'
{
  "models-default": {"base_url_env": "EGRESS_MODELS_DEFAULT_BASE_URL", "token_env": "EGRESS_MODELS_DEFAULT_TOKEN", "allow": [{"methods": ["POST"], "prefixes": ["/chat/completions"]}]},
  "models-escalation": {"base_url_env": "EGRESS_MODELS_ESCALATION_BASE_URL", "token_env": "EGRESS_MODELS_ESCALATION_TOKEN", "allow": [{"methods": ["POST"], "prefixes": ["/chat/completions"]}]},
  "demo-catalog": {"base_url_env": "OVERLAY_CATALOG_BASE_URL", "token_env": "OVERLAY_CATALOG_TOKEN", "allow": [{"methods": ["GET"], "prefixes": ["/"]}]}
}
JSON
  cat >"$d/registry.json" <<'JSON'
{"servers": [{"name": "catalog", "description": "demo", "transport": "streamable-http", "upstream": "demo-catalog", "roles": ["analyst"], "enabled": false}]}
JSON
  echo '{"name": "analyst", "description": "demo role"}' >"$d/agents/analyst.json"
}

make "$TMP/ok"; run "$TMP/ok"; check "a valid overlay passes" 0 $?
python3 -I "$HERE/overlay-check.py" >/dev/null 2>&1; check "no argument is a usage error" 2 $?

make "$TMP/a"; printf 'UNKNOWN_VARIABLE=1\n' >>"$TMP/a/overlay.env"; run "$TMP/a"; check "an unknown variable is refused" 1 $?
make "$TMP/b"; printf 'EGRESS_GIT_TOKEN=plainvalue\n' >>"$TMP/b/overlay.env"; run "$TMP/b"; check "a populated token is refused" 1 $?
grep -q 'plainvalue' "$TMP/out"; check "the refusal never echoes the value" 1 $?
make "$TMP/c"; printf 'EGRESS_GIT_TOKEN=<git-token>\n' >>"$TMP/c/overlay.env"; run "$TMP/c"; check "a placeholder token passes" 0 $?
make "$TMP/d"; sed -i 's/"allow": \[{"methods": \["GET"\], "prefixes": \["\/"\]}\]/"x": 1/' "$TMP/d/upstreams.json"; run "$TMP/d"; check "an upstream without allow is refused" 1 $?
make "$TMP/e"; sed -i '/models-escalation/d' "$TMP/e/upstreams.json"; run "$TMP/e"; check "a missing required upstream is refused" 1 $?
make "$TMP/f"; rm "$TMP/f/agents/analyst.json"; run "$TMP/f"; check "a registry role with no agent is refused" 1 $?
make "$TMP/g"; sed -i 's/demo-catalog", "roles"/nowhere", "roles"/;s/"upstream": "demo-catalog"/"upstream": "nowhere"/' "$TMP/g/registry.json"; run "$TMP/g"; check "a registry entry naming an unknown upstream is refused" 1 $?
make "$TMP/h"; echo '{"contract": 2, "name": "demo"}' >"$TMP/h/overlay.json"; run "$TMP/h"; check "a wrong contract version is refused" 1 $?
make "$TMP/i"; rm "$TMP/i/compose.overlay.yaml"; run "$TMP/i"; check "a missing compose override is refused" 1 $?
make "$TMP/j"; echo '{' >"$TMP/j/registry.json"; run "$TMP/j"; check "malformed JSON is refused" 1 $?

make "$TMP/pp"; mkdir "$TMP/pp/policy"; echo '{"egress": {"allow_hosts": ["git.example.com"]}}' >"$TMP/pp/policy/params.json"
run "$TMP/pp"; check "a valid policy/params.json passes" 0 $?
for bad in '{"egres": {}}' '{"approval": {"action": "MAYBE"}}' '{"budget": {"daily_ask_usd": [99]}}' '[]'; do
  make "$TMP/pb"; mkdir -p "$TMP/pb/policy"; printf '%s' "$bad" >"$TMP/pb/policy/params.json"
  run "$TMP/pb"; check "a bad policy/params.json is refused: $bad" 1 $?; rm -rf "$TMP/pb"
done

exit "$fail"
