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
