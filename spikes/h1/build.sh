#!/usr/bin/env bash
# Build the H1 agent image from a fresh quenching clone (never the shared ~/Projetos/quenching).
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
clone="${QUENCHING_CLONE:-/tmp/forge-h1/quenching}"
image="${H1_IMAGE:-forge-h1-opencode:local}"
sha="$(git -C "$clone" rev-parse HEAD)"
context="$(mktemp -d)"
trap 'rm -rf "$context"' EXIT
cp "$here"/docker/{Dockerfile,entrypoint.sh,opencode.json} "$context/"
cp -R "$clone/plugins/quenching-opencode" "$context/quenching-opencode"
docker build -q --build-arg "QUENCHING_SHA=$sha" -t "$image" "$context"
echo "built $image from quenching $sha"
