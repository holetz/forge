# Server sizing: 30 concurrent sessions

A reasoned estimate, not a measurement. The pilot replaces it with numbers from real sessions.

## Assumptions

- A runner container (harness, toolchain, repository checkout): 1.5 GB RAM, 1 vCPU burst.
- Omnigent server, Postgres, Dex, router, gateway, collector and registry: about 3 GB RAM together.
- Headroom: 25%.

## Estimate

30 runners x 1.5 GB + 3 GB = 48 GB, plus 25% headroom: about 55 GB RAM and 16 vCPU.
Recommended VM: **16 vCPU, 64 GB RAM, 500 GB SSD**. Disk is driven by checkouts and image layers.

## Gateway load

30 sessions, each with at most one model call in flight and about 20 calls a minute per active session at the intense tier, is roughly 10 requests per second at peak. A threaded stdlib process serves that with room to spare.

## Probe numbers

`scripts/probe-concurrency.py` (run by `scripts/smoke-server.sh`): 30 concurrent clients x 5 requests (150 requests) through router and gateway to the stub, on a developer machine, with no saturating load.

| Run | p50 | p95 | max | failures | wall |
|---|---|---|---|---|---|
| default listen backlog (5) | 16 ms | 1098 ms | 1232 ms | 0 | 1.29 s |
| `request_queue_size = 128` (shipped) | 55 ms | 82 ms | 93 ms | 0 | 0.27 s |

The first run showed a 30-client burst falling into SYN retries (about one second) because the stdlib listen backlog is 5; router and gateway now set 128. The gateway container used about 14 MiB of memory. The stub answers instantly, so these numbers measure router and gateway overhead and the thread model, never model latency.

## Retention

The collector rotates each sink at 50 MB with 10 backups (500 MB per sink at most); the gateway rotates its audit file at 50 MB with one backup. At about 300 bytes per audit line and 10 requests per second at peak, a full working day of peak traffic is about 100 MB.

## Deployment steps this repository does not do

- A host firewall rule for the containers on `default` (they keep NAT out); the hard wall is the internal `agents` network where runners live.
- Provisioning the VM, a real IdP, real MCP servers and hostnames.
