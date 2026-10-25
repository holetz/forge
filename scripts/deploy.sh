#!/usr/bin/env bash
# deploy.sh: deploy, upgrade, roll back, back up and health-check the server profile on a Linux host
# reached over SSH. The host and the git ref come from arguments, the environment or an untracked
# deploy.local.env, never from a tracked file. Run on the workstation it only parses its arguments and
# streams itself to the host; with --on-host it acts on the machine it runs on. It prints paths
# relative to the deploy root and never a secret, a user name or a host name. Every model upstream
# stays on the local stub; switching to the Unity Gateway is a manual step (docs/deployment.md).
set -Eeuo pipefail

PROJECT=forge
SERVICES=(postgres dex stub router omnigent egress otel-collector mcp-registry)
MIN_FREE_GB="${MIN_FREE_GB:-8}"
MIN_MEM_MB="${MIN_MEM_MB:-3072}"
MIN_DOCKER=24.0
MIN_COMPOSE=2.24
DEFAULT_REPO_URL=https://github.com/holetz/forge.git
DEFAULT_PORT_BASE=18200
CONFIG_KEYS=(FORGE_HOST FORGE_REF FORGE_DEPLOY_ROOT FORGE_REPO_URL FORGE_PORT_BASE)

usage() {
  cat <<'EOF'
Usage: scripts/deploy.sh <command> [options]

Commands (each runs on the host over SSH):
  preflight   read-only host checks: docker, compose, tools, disk, memory
  deploy      clean checkout of --ref (default main), generate .env, bring the project up, run health
  upgrade     back up, then switch to --ref (required); `rollback` undoes it
  rollback    swap back to the previous release and bring it up
  backup      pg_dump and a copy of both audit sinks into backups/<UTC>/
  health      the non-destructive checks of the server smoke against the running stack
  down        stop the `forge` project (add --volumes to delete its volumes); never prunes
  others      count and fingerprint the containers outside the `forge` project (read-only)

Options:
  --target T        SSH target (else FORGE_HOST, else deploy.local.env)
  --ref R           git ref to deploy (else FORGE_REF, else main)
  --root DIR        deploy root on the host, relative to its home (else FORGE_DEPLOY_ROOT, else forge-host)
  --repo-url URL    clone URL (else FORGE_REPO_URL)
  --port-base N     first loopback port to try (else FORGE_PORT_BASE, else 18200)
  --replace-clone   move a clone with another origin aside and clone again
  --volumes         with `down`: also delete the project's volumes
  --dry-run         print the SSH invocation and stop
  --on-host         act on this machine instead of relaying over SSH
  -h, --help        this text
EOF
}

say() { echo "deploy: $*"; }
die() { echo "deploy: FAIL: $*" >&2; exit 1; }

# --- pure functions (sourced by scripts/test-deploy.sh) ---------------------------------------

# valid_token KIND VALUE: strict shapes for everything that crosses the SSH command line.
valid_token() {
  local kind="$1" v="$2"
  case "$kind" in
    target) [[ "$v" =~ ^[A-Za-z0-9][A-Za-z0-9._@:-]*$ ]] ;;
    ref) [[ "$v" =~ ^[A-Za-z0-9][A-Za-z0-9._/-]*$ ]] && [[ "$v" != *..* ]] ;;
    root) [[ "$v" =~ ^[A-Za-z0-9._/-]+$ ]] && [[ "$v" != *..* ]] ;;
    url) [[ "$v" =~ ^https://[A-Za-z0-9._/-]+$ ]] ;;
    port) [[ "$v" =~ ^[0-9]{4,5}$ ]] && [ "$v" -ge 1024 ] && [ "$v" -le 65000 ] ;;
    *) return 1 ;;
  esac
}

# config_get FILE KEY: the value of KEY in a KEY=VALUE file. The file is parsed, never sourced, and
# only whitelisted keys are honoured.
config_get() {
  local file="$1" key="$2" k line v found=0
  [ -f "$file" ] || return 0
  for k in "${CONFIG_KEYS[@]}"; do [ "$k" = "$key" ] && found=1; done
  [ "$found" = 1 ] || return 0
  while IFS= read -r line || [ -n "$line" ]; do
    [[ "$line" =~ ^[[:space:]]*# ]] && continue
    if [[ "$line" =~ ^[[:space:]]*([A-Z_]+)=(.*)$ ]] && [ "${BASH_REMATCH[1]}" = "$key" ]; then
      v="${BASH_REMATCH[2]}"
      v="${v%\"}"; v="${v#\"}"; v="${v%\'}"; v="${v#\'}"
      printf '%s\n' "$v"
    fi
  done <"$file" | tail -n 1
}

# version_ge A B: true when version A >= B (dotted numbers).
version_ge() { [ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -n 1)" = "$2" ]; }

# used_ports: listening TCP ports, one per line. FORGE_USED_PORTS (space separated) replaces ss in tests.
used_ports() {
  if [ -n "${FORGE_USED_PORTS+x}" ]; then
    tr ' ' '\n' <<<"$FORGE_USED_PORTS" | sed '/^$/d'
  else
    ss -ltnH 2>/dev/null | awk '{print $4}' | sed 's/.*://' | sort -un
  fi
}

# pick_ports BASE COUNT: COUNT consecutive-or-later free ports from BASE up, skipping listeners.
pick_ports() {
  local p="$1" need="$2" got=0 used
  used=" $(used_ports | tr '\n' ' ') "
  while [ "$got" -lt "$need" ]; do
    [ "$p" -le 65000 ] || return 1
    case "$used" in *" $p "*) ;; *) echo "$p"; got=$((got + 1)) ;; esac
    p=$((p + 1))
  done
}

# parse_args ARGS...: sets CMD TARGET REF ROOT REPO_URL PORT_BASE REPLACE_CLONE VOLUMES DRY_RUN ON_HOST.
parse_args() {
  CMD="" ; ON_HOST=0 ; DRY_RUN=0 ; REPLACE_CLONE=0 ; VOLUMES=0
  local a_target="" a_ref="" a_root="" a_url="" a_port=""
  while [ $# -gt 0 ]; do
    case "$1" in
      -h|--help) CMD=help; return 0 ;;
      --on-host) ON_HOST=1 ;;
      --dry-run) DRY_RUN=1 ;;
      --replace-clone) REPLACE_CLONE=1 ;;
      --volumes) VOLUMES=1 ;;
      --target|--ref|--root|--repo-url|--port-base)
        [ $# -ge 2 ] || { echo "deploy: $1 needs a value" >&2; return 2; }
        case "$1" in
          --target) a_target="$2" ;; --ref) a_ref="$2" ;; --root) a_root="$2" ;;
          --repo-url) a_url="$2" ;; --port-base) a_port="$2" ;;
        esac
        shift ;;
      -*) echo "deploy: unknown option $1" >&2; return 2 ;;
      *) [ -z "$CMD" ] || { echo "deploy: one command only (got $CMD and $1)" >&2; return 2; }
         CMD="$1" ;;
    esac
    shift
  done
  case "$CMD" in
    preflight|deploy|upgrade|rollback|backup|health|down|others) ;;
    "") echo "deploy: a command is required" >&2; return 2 ;;
    *) echo "deploy: unknown command $CMD" >&2; return 2 ;;
  esac
  local cfg="${FORGE_DEPLOY_CONFIG:-deploy.local.env}"
  TARGET="${a_target:-${FORGE_HOST:-$(config_get "$cfg" FORGE_HOST)}}"
  REF="${a_ref:-${FORGE_REF:-$(config_get "$cfg" FORGE_REF)}}"
  ROOT="${a_root:-${FORGE_DEPLOY_ROOT:-$(config_get "$cfg" FORGE_DEPLOY_ROOT)}}"
  REPO_URL="${a_url:-${FORGE_REPO_URL:-$(config_get "$cfg" FORGE_REPO_URL)}}"
  PORT_BASE="${a_port:-${FORGE_PORT_BASE:-$(config_get "$cfg" FORGE_PORT_BASE)}}"
  : "${ROOT:=forge-host}" "${REPO_URL:=$DEFAULT_REPO_URL}" "${PORT_BASE:=$DEFAULT_PORT_BASE}"
  if [ -z "$REF" ]; then
    if [ "$CMD" = upgrade ]; then echo "deploy: upgrade needs --ref" >&2; return 2; fi
    REF=main
  fi
  if [ "$ON_HOST" = 0 ]; then
    [ -n "$TARGET" ] || { echo "deploy: no SSH target (--target, FORGE_HOST or deploy.local.env)" >&2; return 2; }
    valid_token target "$TARGET" || { echo "deploy: the SSH target has a shape that is refused" >&2; return 2; }
  fi
  valid_token ref "$REF" || { echo "deploy: invalid ref" >&2; return 2; }
  valid_token root "$ROOT" || { echo "deploy: invalid root" >&2; return 2; }
  valid_token url "$REPO_URL" || { echo "deploy: invalid repo url" >&2; return 2; }
  valid_token port "$PORT_BASE" || { echo "deploy: invalid port base" >&2; return 2; }
}

# --- the workstation half ---------------------------------------------------------------------

relay() {
  local self="$1" args=(--on-host "$CMD" --ref "$REF" --root "$ROOT" --repo-url "$REPO_URL" --port-base "$PORT_BASE")
  [ "$REPLACE_CLONE" = 1 ] && args+=(--replace-clone)
  [ "$VOLUMES" = 1 ] && args+=(--volumes)
  if [ "$DRY_RUN" = 1 ]; then
    echo "ssh -o BatchMode=yes <server-host> <run this script with: ${args[*]}>"
    return 0
  fi
  # The script goes to a temporary file on the host so no command inside it can consume it as stdin.
  # shellcheck disable=SC2029
  ssh -o BatchMode=yes -o ConnectTimeout=15 -o ServerAliveInterval=15 "$TARGET" \
    "f=\$(mktemp) && cat >\"\$f\" && bash \"\$f\" ${args[*]}; rc=\$?; rm -f \"\$f\"; exit \$rc" <"$self"
}

# --- the host half ----------------------------------------------------------------------------

rel() { local p="$1"; [ "$p" = "$ROOT_ABS" ] && echo . || echo "${p#"$ROOT_ABS"/}"; }

dc_in() { # RELEASE_DIR ARGS...
  local dir="$1"; shift
  docker compose -p "$PROJECT" --project-directory "$dir" --env-file "$ROOT_ABS/.env" \
    -f "$dir/compose.yaml" -f "$dir/compose.server.yaml" "$@"
}

current_dir() { [ -L "$ROOT_ABS/current" ] || die "no current release (run deploy first)"; echo "$ROOT_ABS/$(readlink "$ROOT_ABS/current")"; }

env_get() { sed -n "s/^$1=//p" "$ROOT_ABS/.env" | tail -n 1; }

set_kv() { # FILE KEY VALUE
  [[ "$3" =~ ^[A-Za-z0-9._:/@-]*$ ]] || die "refusing to write an unsafe value for $2"
  if grep -q "^$2=" "$1"; then sed -i "s|^$2=.*|$2=$3|" "$1"; else echo "$2=$3" >>"$1"; fi
}

project_running() { [ -n "$(docker ps -q --filter "label=com.docker.compose.project=$PROJECT")" ]; }

existing_dir() { local d="$1"; while [ ! -d "$d" ]; do d="$(dirname "$d")"; done; echo "$d"; }

preflight() {
  local fail=0 v need
  say "preflight"
  for need in git curl openssl python3 docker ss df; do
    command -v "$need" >/dev/null 2>&1 && say "tool $need: present" || { say "tool $need: MISSING"; fail=1; }
  done
  if command -v docker >/dev/null 2>&1; then
    v="$(docker version --format '{{.Server.Version}}' 2>/dev/null || true)"
    if [ -n "$v" ] && version_ge "${v%%-*}" "$MIN_DOCKER"; then say "docker server $v (>= $MIN_DOCKER)"; else say "docker server '${v:-unreachable}' (needs >= $MIN_DOCKER)"; fail=1; fi
    v="$(docker compose version --short 2>/dev/null || true)"; v="${v#v}"
    if [ -n "$v" ] && version_ge "$v" "$MIN_COMPOSE"; then say "compose $v (>= $MIN_COMPOSE)"; else say "compose '${v:-missing}' (needs >= $MIN_COMPOSE)"; fail=1; fi
  fi
  local free_gb mem_mb
  free_gb="$(df -Pk "$(existing_dir "$ROOT_ABS")" | awk 'NR==2 {printf "%d", $4/1048576}')"
  if [ "$free_gb" -ge "$MIN_FREE_GB" ]; then say "disk: ${free_gb}G free (>= ${MIN_FREE_GB}G)"; else say "disk: ${free_gb}G free (needs >= ${MIN_FREE_GB}G)"; fail=1; fi
  mem_mb="$(awk '/^MemAvailable:/ {printf "%d", $2/1024}' /proc/meminfo)"
  if [ "$mem_mb" -ge "$MIN_MEM_MB" ]; then say "memory: ${mem_mb}M available (>= ${MIN_MEM_MB}M)"; else say "memory: ${mem_mb}M available (needs >= ${MIN_MEM_MB}M)"; fail=1; fi
  if [ -f "$ROOT_ABS/.env" ]; then
    if project_running; then say "ports: held by the running $PROJECT project"
    else
      local k p
      for k in OMNIGENT_PORT DEX_PORT ROUTER_PORT STUB_PORT; do
        p="$(env_get "$k")"
        case " $(used_ports | tr '\n' ' ') " in *" $p "*) say "port $k=$p: IN USE"; fail=1 ;; esac
      done
      [ "$fail" = 0 ] && say "ports: the four configured ports are free"
    fi
  else
    say "ports: will pick four free loopback ports from $PORT_BASE"
  fi
  [ "$fail" = 0 ] || die "preflight failed"
  say "preflight: green"
}

ensure_clone() {
  local clone="$ROOT_ABS/forge" url=""
  if [ -d "$clone/.git" ]; then
    url="$(git -C "$clone" remote get-url origin 2>/dev/null || true)"
    if [ "${url%.git}" = "${REPO_URL%.git}" ] && git -C "$clone" fetch --quiet --tags --prune origin '+refs/heads/*:refs/remotes/origin/*'; then
      say "clone forge: reused (origin matches, fetched)"; return 0
    fi
  fi
  if [ -e "$clone" ]; then
    [ "$REPLACE_CLONE" = 1 ] || die "forge/ exists with another origin or cannot fetch; pass --replace-clone to move it aside"
    local aside="forge.replaced-$(date -u +%Y%m%dT%H%M%SZ)"
    mv "$clone" "$ROOT_ABS/$aside"; say "clone forge: moved aside to $aside"
  fi
  git clone --quiet "$REPO_URL" "$clone"
  say "clone forge: cloned"
}

resolve_sha() { # prints the full sha of $REF in the clone
  local clone="$ROOT_ABS/forge" cand sha
  for cand in "origin/$REF" "$REF"; do
    if sha="$(git -C "$clone" rev-parse --verify --quiet "$cand^{commit}")"; then echo "$sha"; return 0; fi
  done
  die "ref '$REF' does not resolve in the clone"
}

make_release() { # SHA -> prints the release dir
  local sha="$1" dir="$ROOT_ABS/releases/${1:0:12}"
  if [ -d "$dir" ]; then
    [ "$(git -C "$dir" rev-parse HEAD)" = "$sha" ] || die "release $(rel "$dir") holds another commit"
  else
    mkdir -p "$ROOT_ABS/releases"
    git -C "$ROOT_ABS/forge" worktree prune
    git -C "$ROOT_ABS/forge" worktree add --detach --quiet "$dir" "$sha"
  fi
  [ -f "$dir/compose.server.yaml" ] || die "release ${sha:0:12} has no compose.server.yaml"
  echo "$dir"
}

gen_env() { # RELEASE_DIR: first deploy only; later releases share the file (the volumes keep the secrets)
  local dir="$1" f="$ROOT_ABS/.env" tok ports p
  if [ -f "$f" ]; then say ".env: kept (mode $(stat -c %a "$f"))"; return 0; fi
  ( umask 077; cp "$dir/.env.example" "$f" )
  chmod 600 "$f"
  tok="up-$(openssl rand -hex 12)"
  set_kv "$f" POSTGRES_PASSWORD "$(openssl rand -hex 16)"
  set_kv "$f" OMNIGENT_OIDC_CLIENT_SECRET "$(openssl rand -hex 32)"
  set_kv "$f" OMNIGENT_OIDC_COOKIE_SECRET "$(openssl rand -hex 32)"
  set_kv "$f" EGRESS_IDENTITY_SECRET "$(openssl rand -hex 32)"
  set_kv "$f" STUB_TOKEN "$tok"
  set_kv "$f" EGRESS_MODELS_DEFAULT_TOKEN "$tok"
  set_kv "$f" EGRESS_MODELS_ESCALATION_TOKEN "$tok"
  for p in EGRESS_MODELS_DEFAULT_BASE_URL EGRESS_MODELS_ESCALATION_BASE_URL; do set_kv "$f" "$p" http://stub:8080/v1; done
  set_kv "$f" EGRESS_PLAIN_HTTP_HOSTS stub
  ports="$(pick_ports "$PORT_BASE" 4 | tr '\n' ' ')"
  [ -n "${ports// /}" ] || die "no four free ports from $PORT_BASE"
  # shellcheck disable=SC2086
  set -- $ports
  set_kv "$f" OMNIGENT_PORT "$1"; set_kv "$f" DEX_PORT "$2"; set_kv "$f" ROUTER_PORT "$3"; set_kv "$f" STUB_PORT "$4"
  say ".env: generated on the host (mode 600); ports OMNIGENT=$1 DEX=$2 ROUTER=$3 STUB=$4, loopback only; upstreams: stub"
}

up_release() { # RELEASE_DIR
  dc_in "$1" up -d --wait --wait-timeout 300 "${SERVICES[@]}" || { dc_in "$1" ps -a || true; die "the stack did not become healthy"; }
  say "project $PROJECT is up from releases/$(basename "$1")"
}

set_links() { # NEW_RELEASE_DIR: previous <- current, current <- new
  local new="$1" cur=""
  [ -L "$ROOT_ABS/current" ] && cur="$(readlink "$ROOT_ABS/current")"
  if [ -n "$cur" ] && [ "$cur" != "releases/$(basename "$new")" ]; then ln -sfn "$cur" "$ROOT_ABS/previous"; fi
  ln -sfn "releases/$(basename "$new")" "$ROOT_ABS/current"
}

cmd_deploy() {
  preflight
  mkdir -p "$ROOT_ABS"; chmod 700 "$ROOT_ABS"
  ensure_clone
  local sha dir; sha="$(resolve_sha)"; dir="$(make_release "$sha")"
  say "ref $REF -> ${sha:0:12}"
  gen_env "$dir"
  set_links "$dir"
  up_release "$dir"
  cmd_health
}

cmd_upgrade() {
  [ -f "$ROOT_ABS/.env" ] || die "no .env: run deploy first"
  cmd_backup
  ensure_clone
  local sha dir old; sha="$(resolve_sha)"; dir="$(make_release "$sha")"
  old="$(readlink "$ROOT_ABS/current")"
  [ "$old" != "releases/${sha:0:12}" ] || die "already on ${sha:0:12}"
  say "ref $REF -> ${sha:0:12} (from ${old#releases/})"
  # The links move first, so `rollback` means "go back" whether the next lines pass or not.
  set_links "$dir"
  trap 'echo "deploy: the upgrade did not come up healthy; run: rollback" >&2' EXIT
  up_release "$dir"
  cmd_health
  trap - EXIT
  say "upgrade: done; undo with: rollback"
}

cmd_rollback() {
  [ -L "$ROOT_ABS/previous" ] || die "no previous release to roll back to"
  local cur prev; cur="$(readlink "$ROOT_ABS/current")"; prev="$(readlink "$ROOT_ABS/previous")"
  ln -sfn "$cur" "$ROOT_ABS/previous"; ln -sfn "$prev" "$ROOT_ABS/current"
  say "rollback: ${cur#releases/} -> ${prev#releases/} (database changes of the newer release are not reverted)"
  up_release "$ROOT_ABS/$prev"
}

cmd_backup() {
  local dir; dir="$(current_dir)"
  project_running || die "the $PROJECT project is not running"
  local out="$ROOT_ABS/backups/$(date -u +%Y%m%dT%H%M%SZ)" img
  ( umask 077; mkdir -p "$out" )
  chmod 700 "$ROOT_ABS/backups" "$out"
  ( umask 077
    dc_in "$dir" exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' >"$out/postgres.dump"
    dc_in "$dir" exec -T egress tar -C /audit -cf - . >"$out/audit.tar"
    img="$(dc_in "$dir" config --images | grep '^python:' | head -n 1)"
    docker volume inspect "${PROJECT}_telemetry-data" >/dev/null
    docker run --rm -v "${PROJECT}_telemetry-data:/t:ro" "$img" tar -C /t -cf - . >"$out/telemetry.tar" )
  [ -s "$out/postgres.dump" ] || die "the pg_dump is empty"
  say "backup: $(rel "$out") ($(du -sk "$out" | cut -f1) KiB: postgres.dump, audit.tar, telemetry.tar)"
}

cmd_health() {
  local dir; dir="$(current_dir)"
  local svc cid st
  for svc in "${SERVICES[@]}"; do
    cid="$(dc_in "$dir" ps -q "$svc")"
    [ -n "$cid" ] || die "service $svc has no container"
    st="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$cid")"
    case "$st" in healthy|running) ;; *) die "service $svc is $st" ;; esac
  done
  say "ok: ${#SERVICES[@]} services healthy"
  local router="http://127.0.0.1:$(env_get ROUTER_PORT)" stub="http://127.0.0.1:$(env_get STUB_PORT)"
  local session="health-$(openssl rand -hex 6)" last
  local jf='import json,sys; print(json.load(sys.stdin).get(sys.argv[1]))'
  curl -fsS "$router/v1/chat/completions" -H 'Content-Type: application/json' -H 'Authorization: Bearer client-health' \
    -H 'X-Forge-User: health-user' -H "X-Forge-Session: $session" \
    -d '{"model":"client-model","messages":[{"role":"user","content":"hi"}],"max_tokens":8}' >/dev/null
  last="$(curl -fsS "$stub/_last")"
  [ "$(python3 -c "$jf" model <<<"$last")" = "$(env_get FORGE_DEFAULT_MODEL)" ] || die "route model differs from the configured default"
  [ "$(python3 -c "$jf" credential_ok <<<"$last")" = "True" ] || die "the gateway credential did not reach the stub"
  say "ok: a request through router and gateway reaches the stub with the injected credential, not the client's"
  local code
  code="$(dc_in "$dir" exec -T egress python -c "
import http.client
c = http.client.HTTPConnection('localhost', 3128, timeout=5)
c.request('GET', '/u/not-listed/v1/models')
print(c.getresponse().status)" | tr -d '\r')"
  [ "$code" = 403 ] || die "an upstream off the allowlist answered $code"
  code="$(dc_in "$dir" exec -T egress python -c "
import http.client
c = http.client.HTTPConnection('localhost', 3128, timeout=5)
c.request('CONNECT', 'stub:8080')
print(c.getresponse().status)" | tr -d '\r')"
  [ "$code" = 405 ] || die "CONNECT answered $code"
  say "ok: upstream off the allowlist (403) and CONNECT (405) are refused"
  local list
  list="$(dc_in "$dir" exec -T egress python -c "import urllib.request as u; print(u.urlopen('http://mcp-registry:8090/servers', timeout=5).read().decode())")"
  case "$list" in *"http://egress:3128/u/mcp-catalog/"*) ;; *) die "the registry does not list gateway endpoints" ;; esac
  say "ok: the registry lists its entries behind the gateway"
  local line="" i
  for i in 1 2 3 4 5 6 7 8 9 10; do
    line="$(dc_in "$dir" exec -T egress grep -h "$session" /audit/egress.jsonl 2>/dev/null | head -n 1 || true)"
    [ -n "$line" ] && break
    sleep 1
  done
  for want in '"upstream":"models-default"' '"status":200' '"user":"health-user"' '"identity":"router"'; do
    case "$line" in *"$want"*) ;; *) die "the audit record lacks $want" ;; esac
  done
  say "ok: the audit record (who, upstream, status) is on the sink with the router identity"
  case "$(env_get EGRESS_MODELS_DEFAULT_BASE_URL)$(env_get EGRESS_MODELS_ESCALATION_BASE_URL)" in
    http://stub:8080/v1http://stub:8080/v1) say "upstreams: stub" ;;
    *) say "upstreams: NOT the stub (a manual switch is in force)" ;;
  esac
  echo "deploy-health: green"
}

cmd_down() {
  local dir; dir="$(current_dir)"
  if [ "$VOLUMES" = 1 ]; then dc_in "$dir" down -v --remove-orphans; else dc_in "$dir" down --remove-orphans; fi
  say "down: $PROJECT project stopped$([ "$VOLUMES" = 1 ] && echo ', volumes deleted'); containers left in the project: $(docker ps -aq --filter "label=com.docker.compose.project=$PROJECT" | wc -l)"
}

cmd_others() {
  local ids n
  ids="$(docker ps -aq --no-trunc | sort | while read -r id; do
    [ "$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project"}}' "$id")" = "$PROJECT" ] || echo "$id"
  done)"
  n="$(printf '%s' "$ids" | sed '/^$/d' | wc -l)"
  say "containers outside the $PROJECT project: $n, fingerprint $(printf '%s\n' "$ids" | sha256sum | cut -c1-12)"
}

run_on_host() {
  case "$ROOT" in /*) ROOT_ABS="$ROOT" ;; *) ROOT_ABS="$HOME/$ROOT" ;; esac
  case "$CMD" in
    preflight) preflight ;;
    deploy) cmd_deploy ;;
    upgrade) cmd_upgrade ;;
    rollback) cmd_rollback ;;
    backup) cmd_backup ;;
    health) cmd_health ;;
    down) cmd_down ;;
    others) cmd_others ;;
  esac
}

main() {
  parse_args "$@" || exit 2
  if [ "$CMD" = help ]; then usage; exit 0; fi
  if [ "$ON_HOST" = 1 ]; then run_on_host; else relay "${BASH_SOURCE[0]}"; fi
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then main "$@"; fi
