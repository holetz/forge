#!/usr/bin/env bash
# test-leak-overlay.sh: prove leak-overlay.sh passes a synthetic overlay, flags each kind of leak
# without echoing it, and fails closed on a bad argument. Leaks are assembled at run time so this
# file matches no pattern.
set -Euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
fail=0
check() { # check <label> <expected-exit> <actual-exit>
  if [ "$2" = "$3" ]; then echo "ok   $1"; else echo "FAIL $1 (expected $2, got $3)"; fail=1; fi
}
scan() { bash "$HERE/leak-overlay.sh" "$1" >"$TMP/out" 2>&1; }
dir() { rm -rf "$TMP/d"; mkdir -p "$TMP/d"; printf '%s\n' "$@" >"$TMP/d/f.txt"; }

dir 'EGRESS_GIT_BASE_URL=https://git.example.com' 'issuer: https://idp.example.org/realm' 'ops@example.net' \
    'http://stub:8080/v1' 'http://127.0.0.1:4000' 'https://<workspace>/ai-gateway' 'https://${HOST}/x' 'https://a.b.test/p'
scan "$TMP/d"; check "a synthetic overlay passes" 0 $?

dir "https://platform.$(printf 'contoso-bank')-corp.io/api"
scan "$TMP/d"; check "a real-looking host is refused" 1 $?
grep -q 'contoso' "$TMP/out"; check "the refusal never echoes the host" 1 $?
pw="$(printf 'hunter%s' 2)"
dir "https://svc:${pw}@example.com/x"
scan "$TMP/d"; check "a credential in the userinfo is refused" 1 $?
grep -q 'credential-userinfo' "$TMP/out"; check "the userinfo refusal names its rule" 0 $?
grep -q "$pw" "$TMP/out"; check "the userinfo refusal never echoes the credential" 1 $?
dir "https://svc@example.com/x"
scan "$TMP/d"; check "a user name with no password passes" 0 $?
dir "https://[$(printf '2001:db8:%s' 0::1)]:8443/x"
scan "$TMP/d"; check "a non-loopback bracketed IPv6 host is refused" 1 $?
grep -q '2001' "$TMP/out"; check "the IPv6 refusal never echoes the host" 1 $?
dir "http://[::1]:4000/x"
scan "$TMP/d"; check "the IPv6 loopback passes" 0 $?
dir "https://svc:${pw}@[::1]:4000/x"
scan "$TMP/d"; check "userinfo on a loopback IPv6 host is still refused" 1 $?
dir "contact: jane.doe@$(printf 'acme')-corp.io"
scan "$TMP/d"; check "a non-example email is refused" 1 $?
dir "contact: jane.doe@example.org"
scan "$TMP/d"; check "an example email passes" 0 $?
dir "cpf $(printf '123.456.789-%s' 09)"
scan "$TMP/d"; check "a generic pattern is refused" 1 $?
dir "token $(printf 'dapi%s' 0123456789abcdef0123456789abcdef)"
scan "$TMP/d"; check "a Databricks token is refused" 1 $?

scan "$TMP/missing"; check "a missing directory is a usage error" 2 $?
bash "$HERE/leak-overlay.sh" >/dev/null 2>&1; check "no argument is a usage error" 2 $?

exit "$fail"
