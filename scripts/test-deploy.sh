#!/usr/bin/env bash
# test-deploy.sh: unit tests for the pure parts of scripts/deploy.sh (argument and config parsing,
# token shapes, version comparison, port choice) and for what the workstation half refuses and
# prints. Needs neither docker nor a host.
set -Euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY="$HERE/deploy.sh"
# shellcheck disable=SC1090
. "$DEPLOY"
set +e

fail=0
check() { # check <label> <ok-flag>
  if [ "$2" = 1 ]; then echo "ok   $1"; else echo "FAIL $1"; fail=1; fi
}
ok() { local label="$1"; shift; "$@" >/dev/null 2>&1; check "$label" $(( $? == 0 )); }
no() { local label="$1"; shift; "$@" >/dev/null 2>&1; check "$label" $(( $? != 0 )); }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
export FORGE_DEPLOY_CONFIG="$TMP/none.env"
unset FORGE_HOST FORGE_REF FORGE_DEPLOY_ROOT FORGE_REPO_URL FORGE_PORT_BASE

# --- token shapes: nothing that could become an ssh option or a shell word crosses the wire
ok "target user@host accepted" valid_token target 'user@host.example'
no "target starting with a dash refused" valid_token target '-oProxyCommand=x'
no "target with a space refused" valid_token target 'a b'
no "target with a semicolon refused" valid_token target 'a;b'
ok "ref main accepted" valid_token ref main
ok "ref with slash and dots accepted" valid_token ref 'release/1.2.3'
no "ref with .. refused" valid_token ref 'a/../b'
no "ref starting with a dash refused" valid_token ref '--upload-pack=x'
no "ref with a dollar refused" valid_token ref 'a$b'
no "root with .. refused" valid_token root '../etc'
no "non-https url refused" valid_token url 'http://x/y.git'
no "port below 1024 refused" valid_token port 80
ok "port 18200 accepted" valid_token port 18200

# --- versions
ok "24.0.7 >= 24.0" version_ge 24.0.7 24.0
ok "2.24 >= 2.24" version_ge 2.24 2.24
no "2.9 < 2.24" version_ge 2.9 2.24
ok "29.9.0 >= 24.0" version_ge 29.9.0 24.0

# --- port choice skips what listens and never returns a duplicate
FORGE_USED_PORTS="18200 18202 18203"
[ "$(pick_ports 18200 4 | tr '\n' ' ')" = "18201 18204 18205 18206 " ]; check "pick_ports skips listeners" $(( $? == 0 ))
FORGE_USED_PORTS=""
[ "$(pick_ports 18200 2 | tr '\n' ' ')" = "18200 18201 " ]; check "pick_ports on a quiet host starts at the base" $(( $? == 0 ))
unset FORGE_USED_PORTS
no "pick_ports gives up past the top" pick_ports 64999 4

# --- config file: parsed, whitelisted, never sourced
cat >"$TMP/c.env" <<'CFG'
# comment
FORGE_HOST=alice@box
FORGE_REF="v1.0"
FORGE_EVIL=$(touch EVIL)
FORGE_PORT_BASE=19000
CFG
[ "$(config_get "$TMP/c.env" FORGE_HOST)" = "alice@box" ]; check "config_get reads a plain value" $(( $? == 0 ))
[ "$(config_get "$TMP/c.env" FORGE_REF)" = "v1.0" ]; check "config_get strips quotes" $(( $? == 0 ))
[ -z "$(config_get "$TMP/c.env" FORGE_EVIL)" ]; check "config_get ignores a key off the whitelist" $(( $? == 0 ))
[ ! -e EVIL ]; check "config_get never executes a value" $(( $? == 0 ))
[ -z "$(config_get "$TMP/absent.env" FORGE_HOST)" ]; check "config_get on a missing file is empty" $(( $? == 0 ))

# --- parse_args precedence: argument > environment > file > default
FORGE_DEPLOY_CONFIG="$TMP/c.env"
parse_args deploy; [ "$TARGET" = "alice@box" ] && [ "$REF" = "v1.0" ] && [ "$PORT_BASE" = 19000 ]; check "file values apply" $(( $? == 0 ))
FORGE_HOST=bob@env parse_args deploy; [ "$TARGET" = "bob@env" ]; check "environment beats the file" $(( $? == 0 ))
FORGE_HOST=bob@env parse_args deploy --target carol@arg; [ "$TARGET" = "carol@arg" ]; check "argument beats the environment" $(( $? == 0 ))
FORGE_DEPLOY_CONFIG="$TMP/none.env"
parse_args deploy --target t@h; [ "$REF" = main ] && [ "$ROOT" = forge-host ] && [ "$PORT_BASE" = 18200 ]; check "defaults: main, forge-host, 18200" $(( $? == 0 ))
no "no target is refused" parse_args deploy
ok "no target is fine on the host" parse_args preflight --on-host
no "upgrade without a ref is refused" parse_args upgrade --target t@h
ok "upgrade with a ref parses" parse_args upgrade --target t@h --ref abc123
no "an unknown command is refused" parse_args frobnicate --target t@h
no "two commands are refused" parse_args deploy backup --target t@h
no "an unknown option is refused" parse_args deploy --target t@h --force
no "an option without a value is refused" parse_args deploy --target
no "a hostile ref is refused" parse_args deploy --target t@h --ref '$(id)'
no "a hostile target is refused" parse_args deploy --target '-oProxyCommand=x'
parse_args down --target t@h --volumes; [ "$VOLUMES" = 1 ]; check "--volumes sets the flag" $(( $? == 0 ))
parse_args deploy --target t@h --replace-clone; [ "$REPLACE_CLONE" = 1 ]; check "--replace-clone sets the flag" $(( $? == 0 ))

# --- the workstation half: a dry run prints a placeholder, never the target
OUT="$(bash "$DEPLOY" deploy --target secret-user@secret-box --dry-run 2>&1)"
case "$OUT" in *secret-*) check "dry run does not print the target" 0 ;; *"<server-host>"*) check "dry run does not print the target" 1 ;; *) check "dry run does not print the target" 0 ;; esac
bash "$DEPLOY" --help | grep -q 'preflight'; check "--help lists the commands" $(( $? == 0 ))
bash "$DEPLOY" deploy >/dev/null 2>&1; check "deploy with no target exits non-zero" $(( $? != 0 ))

# --- the script carries no host identity and writes no secret to stdout
no "deploy.sh holds no IPv4 literal but loopback" bash -c "grep -E '\\b[0-9]{1,3}\\.[0-9]{1,3}\\.[0-9]{1,3}\\.[0-9]{1,3}\\b' '$DEPLOY' | grep -qv '127\\.0\\.0\\.1'"
no "deploy.sh never turns on xtrace" grep -Eq '^\s*set -[a-zA-Z]*x' "$DEPLOY"

[ "$fail" = 0 ] && echo "test-deploy: green"
exit "$fail"
