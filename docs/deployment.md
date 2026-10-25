# Deployment: the server profile on one host over SSH

`scripts/deploy.sh` deploys, upgrades, rolls back, backs up and health-checks the server profile (`compose.yaml` + `compose.server.yaml`) on one Docker host reached over SSH. The host is called `<server-host>` here and in every log: its address, user and hostname never enter the repository. Firewall, identity provider and public hostnames stay outside this tool (see `docs/server-sizing.md`).

## How it runs

The script streams itself to the host (`ssh -o BatchMode=yes <server-host> bash -s -- --on-host <command>`), so the host needs no copy of it. Set the target in your shell, never in a file that is committed:

```bash
export FORGE_HOST=<server-host>      # or put it in deploy.local.env (git-ignored)
bash scripts/deploy.sh preflight
```

Precedence: argument, environment, `deploy.local.env`, default. `deploy.local.env` accepts `FORGE_HOST`, `FORGE_REF`, `FORGE_DEPLOY_ROOT`, `FORGE_REPO_URL`, `FORGE_PORT_BASE` and is parsed, never sourced.

## Layout under the deploy root

All paths are relative to the deploy root (default `forge-host`, under the remote home).

```
forge                 object store: one clone, fetched, never built from
releases/<sha12>      clean detached worktree of one commit; the compose project directory
current               symlink to the running release
previous              symlink to the release before it
.env                  mode 600, generated once, shared by every release
backups/<UTC>/        mode 700: postgres.dump, audit.tar, telemetry.tar
```

The compose project is always named `forge`, so the named volumes survive an upgrade and a rollback. Published ports stay on `127.0.0.1`; `deploy` picks four free loopback ports from the port base (default 18200) and records them in `.env`.

## Commands

| Command | What it does |
|---|---|
| `preflight` | read-only: docker >= 24, compose >= 2.24, tools, free disk, free memory, free ports |
| `deploy --ref R` | clean checkout of `R`, generate `.env`, bring the project up, run `health` |
| `upgrade --ref R` | back up, switch to `R`, run `health`; on failure it prints the `rollback` command and changes nothing else |
| `rollback` | swap `current` and `previous`, bring the restored release up |
| `backup` | `pg_dump -Fc` plus copies of the audit and telemetry volumes |
| `health` | services healthy, one request through the router, credential isolation, egress denials, registry, audit identity |
| `down [--volumes]` | stop the `forge` project only; `--volumes` also deletes its volumes; never prunes images |
| `others` | read-only count and fingerprint of the containers outside `forge` |

`rollback` does not revert database changes made by the newer release; that is why `upgrade` backs up first.

## Upstreams: the manual Unity Gateway switch

A fresh `.env` keeps every upstream on the local stub; the script never writes a gateway URL or token. To route to the live Unity Gateway, edit `.env` in the deploy root by hand on the host (the `EGRESS_MODELS_*` base URLs and tokens, as in `.env.example`), then bring the project up again from `current`:

```bash
docker compose -p forge --project-directory current --env-file .env -f compose.yaml -f compose.server.yaml up -d --wait
```

Only the egress gateway holds upstream tokens. Keep `.env` at mode 600.

## Restore

From a backup directory `backups/<UTC>/`:

```bash
docker compose -p forge ... exec -T postgres pg_restore --clean --if-exists -U <db-user> -d <db-name> < backups/<UTC>/postgres.dump
```

Use the same compose flags as above and the user and database from `.env`. The audit and telemetry archives unpack with `tar -C <volume mount> -xf`.

## Pilot record

Run on 2026-10-25 against `<server-host>`, with every upstream on the stub and no model call.

| Step | Ref | Result |
|---|---|---|
| `preflight` | n/a | green (docker 29.1.3, compose 5.1.0, 146G disk free, 11468M memory available) |
| `deploy` | `a82182e16aa6` | exit 0; 8 services healthy; `deploy-health: green` |
| `health` | `a82182e16aa6` | `deploy-health: green` |
| `upgrade` | `1214c6a14532` | exit 0; backup taken first; `deploy-health: green` |
| `health` | `1214c6a14532` | `deploy-health: green` |
| `backup` | `1214c6a14532` | exit 0; 80 KiB: `postgres.dump`, `audit.tar`, `telemetry.tar` |
| `rollback` | back to `a82182e16aa6` | exit 0; project up from `releases/a82182e16aa6` |
| `health` | `a82182e16aa6` | `deploy-health: green` |
| `down --volumes` | n/a | exit 0; 0 containers left in the `forge` project |

Containers outside the `forge` project: 28 before and 28 after, the same fingerprint (`ff1040c00fad`).

Backup kept for restore tests: `backups/20261025T160758Z` (relative to the deploy root; the upgrade's own pre-switch backup, `backups/20261025T160659Z`, is kept too).

No fix to `scripts/deploy.sh` was needed.
