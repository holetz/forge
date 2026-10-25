#!/usr/bin/env bash
# leak-overlay.sh: the overlay leak scan. Usage: bash scripts/leak-overlay.sh <dir>
# Scans every file under <dir> (tracked or not, so it also works before a commit) for
#   1. the generic leak patterns of leak-generic.sh (same list; keep the two aligned by hand), and
#   2. the synthetic rule: every http(s) host and every email address must be reserved or a
#      placeholder (example.com|org|net, *.example, *.test, *.invalid, localhost, 127.0.0.1, a
#      dotless compose service name, <placeholder> or ${VAR}).
# forge runs it on overlay/example, where rule 2 is what keeps the example synthetic. A private
# distribution holds real hosts by design, so it does not run rule 2 on its own overlay; it runs
# `cq security leak` (with its denylist, kept outside the repository) on anything it sends to forge.
# Output names file:line and the rule, never the matched text. A grep failure is exit 2, not clean.
set -Eeuo pipefail

[ "$#" -eq 1 ] && [ -d "$1" ] || { echo "usage: leak-overlay.sh <dir>" >&2; exit 2; }
DIR="$1"

declare -A PATTERNS=(
  [brazilian-cpf]='\b[0-9]{3}\.[0-9]{3}\.[0-9]{3}-[0-9]{2}\b'
  [brazilian-cnpj]='\b[0-9]{2}\.[0-9]{3}\.[0-9]{3}/[0-9]{4}-[0-9]{2}\b'
  [azure-databricks-host]='adb-[0-9]{6,}\.[0-9]+\.azuredatabricks\.net'
  [databricks-pat]='\bdapi[0-9a-f]{32}\b'
  [classic-cluster-id]='\b[0-9]{4}-[0-9]{6}-[a-z0-9]{8}\b'
  [private-key]='-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----'
)
RESERVED='(^|\.)(example\.(com|org|net)|example|test|invalid|localhost)$'

found=0
report() { echo "leak-overlay: $1 at $2" >&2; found=1; }

# grep_lines <extended-regex> [-o]: prints file:line[:match] for every hit under DIR; exit 2 on error.
grep_lines() {
  local rc=0 out
  out="$(grep -rnIiE "${@:2}" -e "$1" -- "$DIR")" || rc=$?
  if [ "$rc" -gt 1 ]; then echo "leak-overlay: grep failed (exit $rc); the scan did not run." >&2; exit 2; fi
  printf '%s' "$out"
}

for name in "${!PATTERNS[@]}"; do
  hits="$(grep_lines "${PATTERNS[$name]}" | cut -d: -f1,2)"
  [ -z "$hits" ] || while IFS= read -r loc; do report "$name" "$loc"; done <<<"$hits"
done

host_ok() { # host_ok <host>
  local h="${1,,}"
  case "$h" in
    ""|*"<"*|*'$'*|*"{"*|localhost|127.0.0.1) return 0 ;;
    *.*) [[ "$h" =~ $RESERVED ]] ;;
    *) return 0 ;;
  esac
}

hits="$(grep_lines 'https?://[^/[:space:]"'"'"'<>)]+' -o)"
if [ -n "$hits" ]; then
  while IFS= read -r line; do
    loc="$(cut -d: -f1,2 <<<"$line")"; url="${line#*:*:}"
    authority="${url#*://}"; host="${authority##*@}"; host="${host%%:*}"
    host_ok "$host" || report "non-synthetic-host" "$loc"
  done <<<"$hits"
fi

hits="$(grep_lines '[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}' -o)"
if [ -n "$hits" ]; then
  while IFS= read -r line; do
    loc="$(cut -d: -f1,2 <<<"$line")"; addr="${line#*:*:}"
    host_ok "${addr##*@}" || report "non-synthetic-email" "$loc"
  done <<<"$hits"
fi

if [ "$found" -ne 0 ]; then
  echo "leak-overlay: refused; make the content synthetic, never weaken a rule." >&2
  exit 1
fi
echo "leak-overlay: clean"
