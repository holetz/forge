#!/bin/sh
# Render the dex config template to stdout: render.sh <template>.
# Reads DEX_ISSUER, DEX_PORT, DEX_CLIENT_ID and DEX_REDIRECT_URI from the environment. The client id
# is free-form, so it is escaped for the YAML double-quoted scalar the template wraps it in, and then
# for sed's replacement (backslash, & and the | delimiter). A newline cannot be escaped in a one-line
# sed replacement, so it is refused.
set -eu

[ $# -eq 1 ] || { echo "usage: render.sh <template>" >&2; exit 2; }

nl='
'
case "$DEX_CLIENT_ID" in
  *"$nl"* | *"$(printf '\r')"*)
    echo "render.sh: OMNIGENT_OIDC_CLIENT_ID must not contain a line break" >&2
    exit 1 ;;
esac

# Command substitution would strip trailing newlines; none can reach here.
client_id=$(printf '%s' "$DEX_CLIENT_ID" | sed -e 's/[\\"]/\\&/g' -e 's/[\\&|]/\\&/g')

sed -e "s|@DEX_ISSUER@|$DEX_ISSUER|g" -e "s|@DEX_PORT@|$DEX_PORT|g" \
  -e "s|@DEX_CLIENT_ID@|$client_id|g" -e "s|@DEX_REDIRECT_URI@|$DEX_REDIRECT_URI|g" "$1"
