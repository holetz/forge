#!/usr/bin/env bash
# test-render-policy-config.sh: prove the renderer merges, refuses a bad file and detects drift.
set -Euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
R="$HERE/render-policy-config.py"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
fail=0
check() { if [ "$2" = 1 ]; then echo "ok   $1"; else echo "FAIL $1"; fail=1; fi; }

python3 -I "$R" --out "$tmp/a.json" >/dev/null; check "defaults render" $(( $? == 0 ))
python3 -I "$R" --check --out "$tmp/a.json" >/dev/null; check "a fresh rendering is current" $(( $? == 0 ))
python3 -I "$R" --check >/dev/null; check "committed policy/config.yaml is current" $(( $? == 0 ))

printf '{"budget": {"max_tool_calls": 7}}' >"$tmp/p.json"
python3 -I "$R" --params "$tmp/p.json" --out "$tmp/b.json" >/dev/null
grep -q '"limit": 7' "$tmp/b.json"; check "an overlay value reaches the rendering" $(( $? == 0 ))
python3 -I "$R" --check --out "$tmp/b.json" >/dev/null 2>&1; check "drift against the defaults is detected" $(( $? != 0 ))

printf '{"budget": {"session_max_usd": null, "session_ask_usd": []}}' >"$tmp/n.json"
python3 -I "$R" --params "$tmp/n.json" --out "$tmp/n.out.json" >/dev/null
! grep -q 'forge__session_budget' "$tmp/n.out.json"; check "a null budget drops its policy" $(( $? == 0 ))

for bad in '{"egres": {}}' '{"approval": {"action": "MAYBE"}}' '{"budget": {"session_ask_usd": [50]}}' '[1]' 'not json'; do
  printf '%s' "$bad" >"$tmp/bad.json"
  python3 -I "$R" --params "$tmp/bad.json" --out "$tmp/bad.out" >/dev/null 2>&1
  rc=$?; [ "$rc" != 0 ] && [ ! -e "$tmp/bad.out" ]; check "refused: $bad" $(( $? == 0 ))
done

exit "$fail"
