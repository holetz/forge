#!/usr/bin/env bash
# smoke-local.sh: end-to-end proof of the local compose profile, with no Databricks and no paid
# model call (every upstream is the local stub). Tears the stack down on exit, removes only the
# images this run pulled, and prints free disk before and after.
set -Eeuo pipefail
cd "$(git rev-parse --show-toplevel)"

MIN_FREE_GB=8        # abort when fewer GB than this would remain after the pulls
PULL_ESTIMATE_GB=2   # upper estimate for the images that are not present yet
ENV_FILE=.env
CREATED_ENV=0
JAR=$(mktemp)
TMP=$(mktemp -d)

free_gb() { df -Pk . | awk 'NR==2 {printf "%d", $4/1048576}'; }
fail() { echo "smoke-local: FAIL: $*" >&2; exit 1; }
ok() { echo "ok: $*"; }

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
# shellcheck disable=SC1090
set -a; . "./$ENV_FILE"; set +a
# Isolation: a project of its own (the quickstart's volumes are never touched by the teardown) and
# every upstream pinned to the local stub, whatever a live-check .env says (no paid call here).
export COMPOSE_PROJECT_NAME=forge-smoke
# A non-default port set and client id: the overrides must reach dex's issuer, redirect and client.
export OMNIGENT_PORT=18000 DEX_PORT=15556 OMNIGENT_OIDC_CLIENT_ID=omnigent-smoke
export FORGE_DEFAULT_BASE_URL=http://stub:8080/v1 FORGE_DEFAULT_MODEL=system.ai.deepseek-v4-1-flash-1017 FORGE_DEFAULT_TOKEN=stub-token
export FORGE_ESCALATION_BASE_URL=http://stub:8080/v1 FORGE_ESCALATION_MODEL=databricks-claude-opus-5-5 FORGE_ESCALATION_TOKEN=stub-token

IMAGES=$(docker compose config --images | sort -u)
PULLED=()
need=0
for img in $IMAGES; do
  docker image inspect "$img" >/dev/null 2>&1 || { PULLED+=("$img"); need=1; }
done

FREE_BEFORE=$(free_gb)
echo "disk before: ${FREE_BEFORE}G free"
if [ "$need" = 1 ] && [ $((FREE_BEFORE - PULL_ESTIMATE_GB)) -lt "$MIN_FREE_GB" ]; then
  [ "$CREATED_ENV" = 1 ] && rm -f "$ENV_FILE"
  rm -rf "$JAR" "$TMP"
  fail "only ${FREE_BEFORE}G free; the pulls would leave under ${MIN_FREE_GB}G"
fi

cleanup() {
  code=$?
  trap - EXIT
  docker compose down -v --remove-orphans >/dev/null 2>&1 || true
  for img in "${PULLED[@]}"; do docker image rm "$img" >/dev/null 2>&1 || true; done
  [ "$CREATED_ENV" = 1 ] && rm -f "$ENV_FILE"
  rm -rf "$JAR" "$TMP"
  echo "images pulled and removed: ${PULLED[*]:-none}"
  echo "disk after: $(free_gb)G free (before: ${FREE_BEFORE}G)"
  [ "$code" = 0 ] && echo "smoke-local: green"
  exit "$code"
}
trap cleanup EXIT

docker compose up -d --wait --wait-timeout 240 || { docker compose ps -a; docker compose logs --tail 40; fail "stack did not become healthy"; }
ok "all services healthy"

ROUTER="http://localhost:${ROUTER_PORT:-4000}"
STUB="http://localhost:${STUB_PORT:-8080}"
OMNI="http://localhost:${OMNIGENT_PORT:-8000}"
BODY='{"model":"client-model","messages":[{"role":"user","content":"hi"}],"max_tokens":8}'

# A throwaway client credential, generated per run: it must never reach the stub.
CLIENT_AUTH="Authorization: Bearer client-$(openssl rand -hex 8)"

json_field() { python3 -c 'import json,sys; print(json.load(sys.stdin).get(sys.argv[1]))' "$1"; }

# Default route: no task header reaches the stub with the default model and the injected credential.
curl -fsS "$ROUTER/v1/chat/completions" -H 'Content-Type: application/json' \
  -H "$CLIENT_AUTH" -d "$BODY" >/dev/null
LAST=$(curl -fsS "$STUB/_last")
[ "$(echo "$LAST" | json_field model)" = "$FORGE_DEFAULT_MODEL" ] || fail "default route model: $LAST"
[ "$(echo "$LAST" | json_field credential_ok)" = "True" ] || fail "default route credential not injected: $LAST"
ok "default route: default model, upstream credential injected"

# Escalation route.
curl -fsS "$ROUTER/v1/chat/completions" -H 'Content-Type: application/json' \
  -H 'X-Forge-Task: security' -H "$CLIENT_AUTH" -d "$BODY" >/dev/null
LAST=$(curl -fsS "$STUB/_last")
[ "$(echo "$LAST" | json_field model)" = "$FORGE_ESCALATION_MODEL" ] || fail "escalation route model: $LAST"
ok "X-Forge-Task: security reaches the escalation model"

# The client's own credential never reaches the upstream.
# credential_ok is an exact match on the injected token, so a leaked client token would fail it.
[ "$(echo "$LAST" | json_field credential_ok)" = "True" ] || fail "client credential reached the upstream: $LAST"
ok "client Authorization never reaches the upstream"

# Omnigent sends its LLM calls to the router.
BASE=$(docker compose exec -T omnigent printenv OPENAI_BASE_URL | tr -d '\r')
[ "$BASE" = "http://router:4000/v1" ] || fail "OPENAI_BASE_URL is $BASE"
ok "omnigent OPENAI_BASE_URL is the router"

# Overrides: dex advertises the overridden issuer and Omnigent's login redirect carries the
# overridden redirect URI and client id.
ISSUER=$(curl -fsS "http://dex.localhost:${DEX_PORT}/dex/.well-known/openid-configuration" | json_field issuer)
[ "$ISSUER" = "http://dex.localhost:${DEX_PORT}/dex" ] || fail "dex issuer is $ISSUER"
ok "dex issuer follows DEX_PORT ($ISSUER)"
LOGIN_LOC=$(curl -sS -o /dev/null -D - "$OMNI/auth/login" | awk 'tolower($1)=="location:" {print $2}' | tr -d '\r')
case "$LOGIN_LOC" in "http://dex.localhost:${DEX_PORT}/dex/"*) ;; *) fail "login redirect is $LOGIN_LOC" ;; esac
case "$LOGIN_LOC" in *"client_id=${OMNIGENT_OIDC_CLIENT_ID}"*) ;; *) fail "login redirect lacks the client id: $LOGIN_LOC" ;; esac
case "$LOGIN_LOC" in *"redirect_uri=http%3A%2F%2Flocalhost%3A${OMNIGENT_PORT}%2Fauth%2Fcallback"*) ;; *) fail "login redirect lacks the redirect URI: $LOGIN_LOC" ;; esac
ok "login redirect carries the overridden client id and redirect URI"

# OIDC: scripted login through dex ends in a session whose identity is the dev user.
oidc_login() {
  local loc form
  curl -sS -c "$JAR" -b "$JAR" -o /dev/null -D "$TMP/h1" "$OMNI/auth/login"
  loc=$(awk 'tolower($1)=="location:" {print $2}' "$TMP/h1" | tr -d '\r')
  [ -n "$loc" ] || fail "omnigent did not redirect to the IdP"
  form=$(curl -sS -L -c "$JAR" -b "$JAR" -o /dev/null -w '%{url_effective}' "$loc")
  curl -sS -L -c "$JAR" -b "$JAR" -o /dev/null \
    --data-urlencode 'login=dev@example.com' --data-urlencode 'password=password' "$form"
}
oidc_login
IDENT=$(curl -fsS -b "$JAR" "$OMNI/v1/me")
echo "$IDENT" | grep -q 'dev@example.com' || fail "session identity is not the dev user: $IDENT"
ok "OIDC login: session identity is dev@example.com"
