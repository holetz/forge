#!/usr/bin/env bash
# test-leak-generic.sh: prove leak-generic.sh passes a clean tree, flags a hit, and fails closed
# when `git grep` itself cannot run. Sample secrets are assembled at run time so this file matches no pattern.
set -Euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
fail=0
check() { # check <label> <expected-exit> <actual-exit>
  if [ "$2" = "$3" ]; then echo "ok   $1"; else echo "FAIL $1 (expected $2, got $3)"; fail=1; fi
}

REPO="$TMP/repo"
git init -q -b main "$REPO"
echo "nothing to see" >"$REPO/a.txt"
git -C "$REPO" add a.txt
(cd "$REPO" && bash "$HERE/leak-generic.sh" >/dev/null 2>&1); check "clean tree passes" 0 $?

echo "cpf $(printf '123.456.789-%s' 09)" >"$REPO/b.txt"
git -C "$REPO" add b.txt
(cd "$REPO" && bash "$HERE/leak-generic.sh" >/dev/null 2>&1); check "a tracked hit is refused" 1 $?

OUT="$(cd "$TMP" && GIT_CEILING_DIRECTORIES="$TMP" bash "$HERE/leak-generic.sh" 2>&1)"; code=$?
check "git grep failure (not a repository) fails closed" 2 "$code"
case "$OUT" in *clean*) echo "FAIL the error path printed clean"; fail=1 ;; *) echo "ok   the error path does not print clean" ;; esac

exit "$fail"
