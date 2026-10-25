# Overlay contract

A private distribution (an organization that runs forge against its own identity provider, model endpoints and tools) keeps its configuration in an **overlay**: a directory of its own, outside this repository. forge publishes the contract and a synthetic example in `overlay/example/`; it never names a private path, host or credential.

## Applying an overlay

```bash
OVERLAY_DIR=/abs/path/to/overlay docker compose \
  --env-file .env.example --env-file "$OVERLAY_DIR/overlay.env" \
  -f compose.yaml -f compose.server.yaml -f "$OVERLAY_DIR/compose.overlay.yaml" up
```

A later `--env-file` wins and a later `-f` merges over the earlier ones. `OVERLAY_DIR` is an absolute path, or one starting with `./` (a bare relative name is read by compose as a volume). The overlay's location is always an argument; no tracked file of forge reads it.

## Files of an overlay

| File | Purpose |
|---|---|
| `overlay.json` | `{"contract": 1, "name": "<[a-z0-9-]+>"}` |
| `overlay.env` | variable values; see the rules below |
| `compose.overlay.yaml` | the compose override (identity provider, mounts of the overlay directory) |
| `upstreams.json` | the egress allowlist, a full replacement of `server/egress/upstreams.json` |
| `registry.json` | the MCP catalog; each entry names an upstream, never a host |
| `policy/params.json` | reserved: policy parameters, a JSON object, consumed by the policy pack |
| `agents/*.json` | reserved: role agents, each with a `name` and a `description` |

## What an overlay may override

| Surface | How |
|---|---|
| Identity provider | `compose.overlay.yaml` points `OMNIGENT_OIDC_ISSUER` at the corporate issuer and drops the `dex` dependency with `depends_on: !override`. Client and cookie secrets stay variables from the shell. |
| Model endpoints | `FORGE_*` and `EGRESS_MODELS_*` variables in `overlay.env`. |
| Egress allowlist | `upstreams.json`, mounted at `/overlay` and selected with `EGRESS_UPSTREAMS_FILE`. It must keep `models-default` and `models-escalation`, and every upstream declares `allow` (the gateway fails closed). |
| Catalog and connectors | `registry.json` plus the upstream it names; every role it lists is declared by an `agents/*.json`. |
| Policy parameters, role agents | the reserved slots above; only their shape is checked. |

## What an overlay may never do

- Hold a credential value. A variable ending `_TOKEN`, `_SECRET` or `_PASSWORD` is empty or a placeholder (`<...>`, `change-me...`, `stub-token`); real values come from the shell or a secret store.
- Open a route out of the gateway: an upstream without `allow` is refused.
- Give an agent a real URL: registry entries name an upstream.
- Replace forge's own files. `overlay.env` may set only a variable named in `.env.example`, one its upstreams name in `base_url_env` or `token_env`, or an `OVERLAY_`-prefixed one.

## Checking an overlay

```bash
python3 -I scripts/overlay-check.py "$OVERLAY_DIR"
```

The checker calls forge's own loaders (`load_upstreams` in `server/egress/egress.py`, `load_registry` in `server/mcp/registry.py`), so the rules are the code's, not a copy. Exit 0 is valid, 1 violations, 2 unusable input. Then `docker compose ... config -q` with the arguments above validates the override.

## The leak scan

`bash scripts/leak-overlay.sh <dir>` applies the generic leak patterns of `scripts/leak-generic.sh` and a synthetic rule: every URL host is reserved (`example.com|org|net`, `*.example`, `*.test`, `*.invalid`, `localhost`, `127.0.0.1`), a dotless service name or a placeholder, and every email is at an `example.*` domain. forge's gate runs it on `overlay/example/` and on `docs/`. A private overlay holds real hosts by design, so it does not run the synthetic rule; it runs `cq security leak` with its own denylist, kept outside the repository, on any change it sends back to forge.
