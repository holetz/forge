# Cost per person, team, session and project

`server/cost/` totals what the platform spends from two sources and serves the result over MCP for
the coordinator's panel. It is built and tested only against synthetic fixtures: nothing in the
repository queries Databricks.

## What it reads

| Source | Shape the service assumes | Used for |
|---|---|---|
| `system.ai_gateway.usage` | `request_id`, `event_time`, `requester`, `endpoint_name`, `input_tokens`, `cache_read_tokens`, `output_tokens`, `tags` (`session_id`, `project`) | model spend through the gateway |
| `system.billing.usage` | `record_id`, `usage_date`, `sku_name`, `usage_unit`, `usage_quantity`, `identity_metadata.run_as`, `custom_tags` (`session_id`, `project`) | compute and agent spend |
| `system.billing.list_prices` | a price per SKU | pricing the billing rows |

The billing columns follow the documented table. **The gateway table's column names are an
assumption**: this repository has not seen the real table. `server/cost/fixtures.py` is the one place
to adjust once they are known (see the live step below).

## How a row becomes a cost

- Gateway cost is `(input * input_rate + cache_read * cache_rate + output * output_rate) / 1,000,000`,
  with the rates of the endpoint in the price table.
- Billing cost is `usage_quantity * SKU price`.
- A row whose endpoint or SKU has no price is not zero. It is counted in `unpriced` (row count and
  `source:id` refs) in every answer, so a missing price cannot read as a cheap day.
- Person is the requester or `run_as`. Team comes from a person-to-team map, not from the tables;
  a person not in the map is `unassigned`. A row without a session or project tag is `untagged`.
  Every view therefore sums to the same grand total.

## Tools

JSON-RPC 2.0 on `POST /mcp` (`initialize`, `tools/list`, `tools/call`), plus `GET /healthz`. Each
tool takes optional `since` and `until` (`YYYY-MM-DD`, inclusive).

| Tool | Returns |
|---|---|
| `cost_by_person`, `cost_by_team`, `cost_by_session`, `cost_by_project` | rows of `name`, `total`, `gateway`, `billing`, `lines`; the grand `total`; `unpriced` |
| `budget_status` | each budget in `budgets.json` with spend, fraction and level (`ok`, `warning`, `over`), and the `alerts` list; with no `since`/`until` the window is the `period` of `budgets.json` (`day`, `month` or `year`) that holds today in UTC, and an absent or unknown period means all time |

Budgets only report. Blocking spend stays in the gateway limits and the Omnigent `cost_budget` policy.

Run it locally with `python3 server/cost/cost_mcp.py` (serves the fixtures on 127.0.0.1, port from
`PORT`, default 8091). `COST_FIXTURE_FILE` points at a JSON file in the shape of
`fixtures.build()`; `COST_BUDGETS_FILE` replaces the budgets.

## Not done: the live query

Reading the real tables is a later human step, not part of this change. Before it:

1. Estimate the cost: size the scan of the two system tables for the period (a metadata
   `DESCRIBE` and a row count are the cheap first look) and the warehouse time the aggregation needs,
   and write the figure down. A SQL warehouse must be authorized for this use.
2. `DESCRIBE system.ai_gateway.usage`, then adjust `fixtures.py` and add a reader with the same
   `read()` contract as `FixtureReader`, filtering the period in SQL.
3. Prefer `system.billing.list_prices` over the static price table.
4. Only then register the service in `server/mcp/registry.json` and the gateway allowlist, which are
   separate changes.

The service has no caller authentication yet; keep it on the internal network.
