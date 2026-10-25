#!/usr/bin/env bash
# smoke-server.sh: end-to-end proof of the server profile overlay, with no Databricks and no paid
# model call (every upstream is the local stub). Starts only the services the proof needs, tears the
# stack down on exit (`down -v`), removes only the images this run pulled, and prints free disk
# before and after. Aborts before pulling when fewer than MIN_FREE_GB would remain.
set -Eeuo pipefail
cd "$(git rev-parse --show-toplevel)"

MIN_FREE_GB=8        # abort when fewer GB than this would remain after the pulls
PULL_ESTIMATE_GB=2   # upper estimate for the images that are not present yet
ENV_FILE=.env
CREATED_ENV=0
PROJECT=forge-server-smoke
SERVICES=(egress router stub otel-collector mcp-registry agent-probe)

free_gb() { df -Pk . | awk 'NR==2 {printf "%d", $4/1048576}'; }
fail() { echo "smoke-server: FAIL: $*" >&2; exit 1; }
ok() { echo "ok: $*"; }
dc() { docker compose -p "$PROJECT" -f compose.yaml -f compose.server.yaml "$@"; }
probe() { dc exec -T agent-probe "$@"; }

command -v docker >/dev/null || fail "docker is required"
command -v curl >/dev/null || fail "curl is required"

if [ ! -f "$ENV_FILE" ]; then
  cp .env.example "$ENV_FILE"
  CREATED_ENV=1
  sed -i \
    -e "s|^OMNIGENT_OIDC_CLIENT_SECRET=.*|OMNIGENT_OIDC_CLIENT_SECRET=$(openssl rand -hex 32)|" \
    -e "s|^OMNIGENT_OIDC_COOKIE_SECRET=.*|OMNIGENT_OIDC_COOKIE_SECRET=$(openssl rand -hex 32)|" \
    -e "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$(openssl rand -hex 16)|" "$ENV_FILE"
fi
set -a
# shellcheck disable=SC1090
. "./$ENV_FILE"
set +a
# Isolation: own project, own ports, and every upstream pinned to the local stub whatever a
# live-check .env says (no paid call here). The upstream token is generated per run: it must
# reach the stub through the gateway only.
UPSTREAM_TOKEN="up-$(openssl rand -hex 12)"
export ROUTER_PORT=14001 STUB_PORT=18081
export STUB_TOKEN="$UPSTREAM_TOKEN"
export EGRESS_PLAIN_HTTP_HOSTS=stub
export EGRESS_MODELS_DEFAULT_BASE_URL=http://stub:8080/v1 EGRESS_MODELS_DEFAULT_TOKEN="$UPSTREAM_TOKEN"
export EGRESS_MODELS_ESCALATION_BASE_URL=http://stub:8080/v1 EGRESS_MODELS_ESCALATION_TOKEN="$UPSTREAM_TOKEN"
export EGRESS_GIT_BASE_URL="" EGRESS_GIT_TOKEN="" EGRESS_MCP_CATALOG_BASE_URL="" EGRESS_MCP_CATALOG_TOKEN=""
export FORGE_DEFAULT_MODEL=system.ai.deepseek-v4-1-flash-1017 FORGE_ESCALATION_MODEL=databricks-claude-opus-5-5

IMAGES=$(dc config --images "${SERVICES[@]}" | sort -u)
PULLED=()
need=0
for img in $IMAGES; do
  docker image inspect "$img" >/dev/null 2>&1 || { PULLED+=("$img"); need=1; }
done

FREE_BEFORE=$(free_gb)
echo "disk before: ${FREE_BEFORE}G free"
echo "images present before up: $(for img in $IMAGES; do docker image inspect "$img" >/dev/null 2>&1 && echo -n "$img "; done)"
if [ "$need" = 1 ] && [ $((FREE_BEFORE - PULL_ESTIMATE_GB)) -lt "$MIN_FREE_GB" ]; then
  [ "$CREATED_ENV" = 1 ] && rm -f "$ENV_FILE"
  fail "only ${FREE_BEFORE}G free; the pulls would leave under ${MIN_FREE_GB}G"
fi

cleanup() {
  code=$?
  trap - EXIT
  dc down -v --remove-orphans >/dev/null 2>&1 || true
  for img in "${PULLED[@]}"; do docker image rm "$img" >/dev/null 2>&1 || true; done
  [ "$CREATED_ENV" = 1 ] && rm -f "$ENV_FILE"
  echo "images pulled and removed: ${PULLED[*]:-none}"
  echo "disk after: $(free_gb)G free (before: ${FREE_BEFORE}G)"
  [ "$code" = 0 ] && echo "smoke-server: green"
  exit "$code"
}
trap cleanup EXIT

dc up -d --wait --wait-timeout 240 "${SERVICES[@]}" || { dc ps -a; dc logs --tail 40; fail "stack did not become healthy"; }
ok "services healthy"

ROUTER="http://localhost:${ROUTER_PORT}"
STUB="http://localhost:${STUB_PORT}"
BODY='{"model":"client-model","messages":[{"role":"user","content":"hi"}],"max_tokens":8}'
SESSION="smoke-$(openssl rand -hex 6)"
CLIENT_AUTH="Authorization: Bearer client-$(openssl rand -hex 8)"
json_field() { python3 -c 'import json,sys; print(json.load(sys.stdin).get(sys.argv[1]))' "$1"; }

# 1. Router -> gateway -> stub: default model, the gateway's credential, never the client's.
curl -fsS "$ROUTER/v1/chat/completions" -H 'Content-Type: application/json' -H "$CLIENT_AUTH" \
  -H 'X-Forge-User: smoke-user' -H "X-Forge-Session: $SESSION" -d "$BODY" >/dev/null
LAST=$(curl -fsS "$STUB/_last")
[ "$(echo "$LAST" | json_field model)" = "$FORGE_DEFAULT_MODEL" ] || fail "route model: $LAST"
[ "$(echo "$LAST" | json_field credential_ok)" = "True" ] || fail "the gateway credential did not reach the stub: $LAST"
ok "request through router and gateway reaches the stub with the injected credential, not the client's"

# 2. No upstream token outside the gateway.
for svc in router agent-probe; do
  if dc exec -T "$svc" env | grep -q "$UPSTREAM_TOKEN"; then fail "$svc environment holds the upstream token"; fi
done
dc exec -T egress env | grep -q "$UPSTREAM_TOKEN" || fail "egress should hold the upstream token"
ok "router and probe environments hold no upstream token"

# 3. The internal network: the gateway is reachable, the stub and the outside are not.
probe python -c "import urllib.request as u; u.urlopen('http://egress:3128/healthz', timeout=3)" || fail "probe cannot reach the gateway"
if probe python -c "import urllib.request as u; u.urlopen('http://stub:8080/healthz', timeout=3)" 2>/dev/null; then fail "probe reached the stub directly"; fi
if probe python -c "import socket; socket.create_connection(('1.1.1.1', 443), timeout=3)" 2>/dev/null; then fail "probe reached an outside address"; fi
ok "agent network: gateway reachable; stub and outside addresses are not"

# 4. Refusals: an upstream off the allowlist (403) and CONNECT (405).
CODE=$(probe python -c "
import http.client
c = http.client.HTTPConnection('egress', 3128, timeout=5)
c.request('GET', '/u/not-listed/v1/models')
print(c.getresponse().status)")
[ "$(echo "$CODE" | tr -d '\r')" = "403" ] || fail "upstream off the allowlist answered $CODE"
CODE=$(probe python -c "
import http.client
c = http.client.HTTPConnection('egress', 3128, timeout=5)
c.request('CONNECT', 'stub:8080')
print(c.getresponse().status)")
[ "$(echo "$CODE" | tr -d '\r')" = "405" ] || fail "CONNECT answered $CODE"
ok "upstream off the allowlist (403) and CONNECT (405) are refused"

# 5. Audit and OTLP records reach the collector's file sink.
OTLP_MARK="otlp-$(openssl rand -hex 6)"
probe python -c "
import http.client, json
body = json.dumps({'resourceLogs': [{'resource': {'attributes': []}, 'scopeLogs': [{'scope': {}, 'logRecords': [
    {'timeUnixNano': '1', 'severityText': 'INFO', 'body': {'stringValue': '$OTLP_MARK'}}]}]}]})
c = http.client.HTTPConnection('otel-collector', 4318, timeout=5)
c.request('POST', '/v1/logs', body, {'Content-Type': 'application/json'})
print(c.getresponse().status)" >/dev/null || fail "OTLP post failed"
wait_for() { # file, marker: bounded retry while the collector flushes
  for _ in $(seq 1 30); do
    probe grep -qs "$2" "$1" 2>/dev/null && return 0
    sleep 1
  done
  return 1
}
wait_for /telemetry/audit.jsonl "$SESSION" || { probe ls -la /telemetry /audit || true; dc logs --tail 30 otel-collector; fail "the audit record did not reach the sink"; }
AUDIT_LINE=$(probe grep -h "$SESSION" /audit/egress.jsonl | head -1)
for want in '"upstream":"models-default"' '"status":200' '"user":"smoke-user"' '"total_tokens":3'; do
  case "$AUDIT_LINE" in *"$want"*) ;; *) fail "audit record lacks $want: $AUDIT_LINE" ;; esac
done
case "$AUDIT_LINE" in *"$UPSTREAM_TOKEN"*|*"client-"*) fail "audit record holds a credential" ;; esac
ok "audit record (who, upstream, model, tokens, status) appears in the collector's file sink"
wait_for /telemetry/otlp.jsonl "$OTLP_MARK" || { dc logs --tail 30 otel-collector; fail "the OTLP log did not reach the sink"; }
ok "an OTLP log sent to the collector appears in the file sink"

# 6. The registry lists its entries and refuses one with no upstream.
LIST=$(probe python -c "import urllib.request as u; print(u.urlopen('http://mcp-registry:8090/servers', timeout=3).read().decode())")
case "$LIST" in *'"name": "catalog"'*|*'"name":"catalog"'*) ;; *) fail "registry list: $LIST" ;; esac
case "$LIST" in *"http://egress:3128/u/mcp-catalog/"*) ;; *) fail "registry endpoint is not the gateway's: $LIST" ;; esac
python3 -B -I -c '
import json, os, sys, tempfile
sys.path.insert(0, "server/mcp")
import registry
d = tempfile.mkdtemp()
path = os.path.join(d, "registry.json")
json.dump({"servers": [{"name": "x", "description": "d", "transport": "streamable-http", "upstream": "no-such", "roles": [], "enabled": False}]}, open(path, "w"))
try:
    registry.load_registry(path, "server/egress/upstreams.json")
except ValueError:
    sys.exit(0)
sys.exit(1)' || fail "the registry accepted an entry with no upstream"
ok "registry lists its entries (gateway endpoints) and refuses an entry with no upstream"

# 7. Light concurrency probe: 30 clients x 5 requests through router and gateway.
PROBE_OUT=$(python3 -B scripts/probe-concurrency.py --url "$ROUTER" --clients 30 --requests 5) || { echo "$PROBE_OUT"; fail "the concurrency probe had failures"; }
echo "$PROBE_OUT"
ok "30-client probe finished with every request answered"
echo "gateway memory: $(docker stats --no-stream --format '{{.MemUsage}}' "$(dc ps -q egress)")"
