# forge
Self-hosted stack for AI-assisted development around Omnigent: docker compose with model routing over Unity Gateway, OIDC, egress proxy, observability and an MCP registry

See [CONTRIBUTING.md](CONTRIBUTING.md) for the leak gate: installing the git hooks and running the repository gate.

## Local profile

`docker compose up` brings up Omnigent (pinned by version and digest), Postgres, a dev OIDC provider (dex) and a task router, with a local stub as the only model endpoint. No Databricks access and no paid model call are involved. Published ports bind to `127.0.0.1`.

### Quickstart

```bash
cp .env.example .env
# replace the three change-me values: openssl rand -hex 32 (the cookie secret needs 64 hex chars)
docker compose up -d --wait
```

Open <http://localhost:8000> and sign in as `dev@example.com` with the dev-only password `password`. `bash scripts/smoke-local.sh` proves the stack end to end and tears it down (`docker compose down -v`, removes only the images it pulled, prints disk before and after; it aborts when fewer than 8 GB would remain).

### How it fits together

- **Model routing.** Omnigent sends every LLM call to the router (`OPENAI_BASE_URL=http://router:4000/v1`). The router reads the `X-Forge-Task` header: `security`, `architecture` and `repeated-failure` go to the escalation route; anything else goes to the default route (Flash). It drops the caller's `Authorization`, injects the route's token from the environment and forwards.
- **Runner wiring.** Omnigent runs in external-runner mode: start the runner as Omnigent's prompt says and point it at `http://localhost:4000/v1`. Whether a given harness honours `OPENAI_BASE_URL` is settled by the live check below.
- **`*.localhost`.** The OIDC issuer is `http://dex.localhost:5556/dex`. Browsers and curl resolve `*.localhost` to loopback; a client that does not needs `127.0.0.1 dex.localhost` in its hosts file. The compose gives the `dex` service that name as a network alias so the Omnigent container resolves the same issuer.
- **Port and client overrides.** `OMNIGENT_PORT`, `DEX_PORT` and `OMNIGENT_OIDC_CLIENT_ID` may be changed in `.env`: dex's issuer (`http://dex.localhost:<DEX_PORT>/dex`), redirect URI and client id follow them, and `scripts/smoke-local.sh` proves a non-default set (18000, 15556, `omnigent-smoke`). The client id may carry any character, `&`, `|`, `\` and `"` included (`local/dex/render.sh` escapes it and `scripts/test-dex-render.sh` proves it), except a line break, which is refused; write a literal `$` as `$$` in `.env`, because Compose interpolates it first.
- **Credentials.** Tokens are plain environment variables read from the shell or `.env` (git-ignored); the stack never calls a CLI or keyring. `.env.example` holds placeholders only.

### Live check against Unity Gateway (human step, not run by CI)

Under ten short requests on Flash, well under US$ 0.01.

1. In `.env` (or the shell) set `FORGE_DEFAULT_BASE_URL=https://<workspace>/ai-gateway/mlflow/v1` and `FORGE_DEFAULT_TOKEN` to a workspace token; leave the escalation route on the stub.
2. `docker compose up -d --wait`, then:
   ```bash
   curl -s http://localhost:4000/v1/chat/completions -H 'Content-Type: application/json' \
     -d '{"model":"any","messages":[{"role":"user","content":"Reply with exactly: HELLO"}],"max_tokens":16}'
   ```
3. Expect HTTP 200 and an answer from `DeepSeek-V4.1-Flash-1017` (reasoning in `reasoning_content`); confirm one row in `system.ai_gateway.usage`.
4. Point a runner at the router and run one trivial Omnigent agent turn to settle the runner question above.

## Server profile

An overlay on the local profile: `docker compose -f compose.yaml -f compose.server.yaml up -d --wait`. It adds, without changing `compose.yaml`:

- **Egress gateway** (`server/egress/`). A reverse gateway, not a CONNECT proxy: a client addresses `http://egress:3128/u/<upstream>/<path>`, where `<upstream>` is a key of `server/egress/upstreams.json`. The gateway drops every client credential, injects the upstream's own token, forwards and streams the answer back. An unknown upstream is refused (403), an unconfigured one answers 503, `CONNECT` is refused (405). Tokens live only in the gateway's environment (`EGRESS_*` in `.env.example`); the router holds a placeholder.
- **Internal agent network.** Runners attach to `agents`, a network with no route out; its only other members are the gateway, the collector and the registry. Containers on `default` keep NAT out (Docker cannot publish a port of an internal-only network): closing that is a host-firewall step on the VM, an accepted risk.
- **Audit and telemetry.** The gateway writes one JSON line per request (time, upstream, model, token counts, status; never a credential or a body). The user and session are recorded only when the router signed them with a secret it shares with the gateway (`EGRESS_IDENTITY_SECRET`); otherwise both are `unauthenticated`, so a client cannot forge who it is in the audit. An OpenTelemetry collector tails it and receives OTLP/HTTP on `otel-collector:4318`, writing both to `/telemetry/*.jsonl` on a volume with rotation. A file sink, not Postgres: the collector has no Postgres exporter.
- **MCP registry** (`server/mcp/`). A read-only inventory (`GET /servers`) of approved MCP servers with their roles; each entry names a gateway upstream, and a client learns the gateway endpoint, never the real URL. The shipped entry is a disabled placeholder.
- **Router.** Accepts plain HTTP only to hosts in `FORGE_PLAIN_HTTP_HOSTS` (the overlay sets `stub,egress`).

`bash scripts/smoke-server.sh` proves all of it offline against the stub (starts only the services it needs, runs a light 30-client probe, `down -v` on exit, removes only the images it pulled, aborts when fewer than 8 GB would remain). The 30-session sizing and the probe numbers are in [docs/server-sizing.md](docs/server-sizing.md).

The live check through the gateway against Unity Gateway is a human step: set `EGRESS_MODELS_DEFAULT_BASE_URL` and `EGRESS_MODELS_DEFAULT_TOKEN` in `.env`, bring the overlay up and repeat the request of the live check above against the router. Whether every client (the OpenCode runner, `git` over HTTPS, the Databricks SDK, MCP clients) can take a base URL is settled there.
