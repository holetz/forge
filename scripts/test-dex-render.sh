#!/usr/bin/env bash
# test-dex-render.sh: prove local/dex/render.sh hands dex the exact client id set in .env, whatever
# sed, shell or YAML metacharacters it carries, and refuses a value it cannot render.
set -Euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEX="$HERE/../local/dex"
fail=0
check() { # check <label> <ok-flag>
  if [ "$2" = 1 ]; then echo "ok   $1"; else echo "FAIL $1"; fail=1; fi
}

render() { # render <client id>: the dex config on stdout
  DEX_ISSUER='http://dex.localhost:5556/dex' DEX_PORT=5556 DEX_CLIENT_ID="$1" \
    DEX_REDIRECT_URI='http://localhost:8000/auth/callback' sh "$DEX/render.sh" "$DEX/config.yaml"
}

client_id() { # the staticClients id, read back from the rendered config on stdin
  python3 -I -c '
import json, re, sys
m = re.search(r"^  - id: (\".*\")$", sys.stdin.read(), re.M)
sys.stdout.write(json.loads(m.group(1)))'
}

for id in 'omnigent-local' 'a&b' 'a|b' 'a&&b||c' 'back\slash' 'say"hi"' '$HOME' '`id`' 'two words' '#x' 'a:b' "it's" '\&|"' '&' '|'; do
  got="$(render "$id" | client_id; printf x)"; got="${got%x}"
  [ "$got" = "$id" ]; check "client id ${id} renders unchanged" $(( $? == 0 ))
done

render $'a\nb' >/dev/null 2>&1; check "a newline in the client id is refused" $(( $? != 0 ))
OUT="$(render 'plain' 2>/dev/null)"
case "$OUT" in *'@DEX_'*) left=1 ;; '') left=1 ;; *) left=0 ;; esac
check "a plain render is non-empty and leaves no placeholder" $(( left == 0 ))

exit "$fail"
