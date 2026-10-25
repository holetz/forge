"""forge cost model: spend per person, team, session and project.

Stdlib only. Rows shaped like system.ai_gateway.usage and system.billing.usage come from an
injected reader and are loaded into an in-memory SQLite database; the views are SQL GROUP BYs.
Nothing here opens a connection to Databricks: a live reader is a later, human-approved step
(docs/cost.md).
"""
import json
import sqlite3

UNASSIGNED = "unassigned"
UNTAGGED = "untagged"
VIEWS = {"person": "person", "team": "team", "session": "session", "project": "project"}

SCHEMA = """
CREATE TABLE lines (
  source TEXT, ref TEXT, day TEXT, person TEXT, team TEXT, session TEXT, project TEXT, cost REAL
)
"""


class FixtureReader:
    """The only reader: data already in memory, or a JSON file shaped like fixtures.build()."""

    def __init__(self, data=None, path=None):
        if data is None:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
        self.data = data

    def read(self):
        return self.data


def _gateway_cost(row, rates):
    rate = rates.get(row["endpoint_name"])
    if rate is None:
        return None
    return (row["input_tokens"] * rate["input"] + row["cache_read_tokens"] * rate["cache_read"]
            + row["output_tokens"] * rate["output"]) / 1_000_000


def load(reader):
    """Build the lines table: one priced (or unpriced, cost NULL) line per source row."""
    data = reader.read()
    teams, prices = data["teams"], data["prices"]
    db = sqlite3.connect(":memory:")
    db.execute(SCHEMA)
    lines = []
    for row in data["gateway"]:
        tags = row.get("tags") or {}
        person = row["requester"]
        lines.append(("gateway", row["request_id"], row["event_time"][:10], person, teams.get(person, UNASSIGNED),
                      tags.get("session_id", UNTAGGED), tags.get("project", UNTAGGED),
                      _gateway_cost(row, prices["endpoints"])))
    for row in data["billing"]:
        tags = row.get("custom_tags") or {}
        person = (row.get("identity_metadata") or {}).get("run_as") or UNASSIGNED
        price = prices["skus"].get(row["sku_name"])
        lines.append(("billing", row["record_id"], row["usage_date"], person, teams.get(person, UNASSIGNED),
                      tags.get("session_id", UNTAGGED), tags.get("project", UNTAGGED),
                      None if price is None else row["usage_quantity"] * price))
    db.executemany("INSERT INTO lines VALUES (?,?,?,?,?,?,?,?)", lines)
    return db


def _window(since, until):
    clauses, args = [], []
    if since:
        clauses.append("day >= ?")
        args.append(since)
    if until:
        clauses.append("day <= ?")
        args.append(until)
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", args


def cost_by(db, view, since=None, until=None):
    """Totals per person, team, session or project, split by source, with the unpriced rows named."""
    if view not in VIEWS:
        raise ValueError("unknown view %r" % view)
    where, args = _window(since, until)
    rows = db.execute(
        "SELECT %s, COALESCE(SUM(cost),0), COALESCE(SUM(CASE WHEN source='gateway' THEN cost END),0), "
        "COALESCE(SUM(CASE WHEN source='billing' THEN cost END),0), COUNT(*) FROM lines%s "
        "GROUP BY 1 ORDER BY 2 DESC, 1" % (VIEWS[view], where), args).fetchall()
    total = db.execute("SELECT COALESCE(SUM(cost),0) FROM lines" + where, args).fetchone()[0]
    unpriced = db.execute("SELECT source, ref FROM lines%s%s cost IS NULL ORDER BY source, ref"
                          % (where, " AND" if where else " WHERE"), args).fetchall()
    return {
        "view": view,
        "currency": "USD",
        "since": since,
        "until": until,
        "rows": [{"name": r[0], "total": round(r[1], 6), "gateway": round(r[2], 6), "billing": round(r[3], 6), "lines": r[4]}
                 for r in rows],
        "total": round(total, 6),
        "unpriced": {"rows": len(unpriced), "refs": ["%s:%s" % u for u in unpriced]},
    }


def budget_status(db, budgets, since=None, until=None):
    """Spend against each budget: level is ok below the warning fraction, warning from it, over at the cap."""
    spend = {}
    for scope in ("person", "team", "project"):
        spend[scope] = {r["name"]: r["total"] for r in cost_by(db, scope, since, until)["rows"]}
    out = []
    for entry in budgets["budgets"]:
        used = spend.get(entry["scope"], {}).get(entry["name"], 0.0)
        fraction = used / entry["cap"]
        level = "over" if fraction >= 1 else "warning" if fraction >= entry["warn"] else "ok"
        out.append({"scope": entry["scope"], "name": entry["name"], "cap": entry["cap"], "spend": round(used, 6),
                    "fraction": round(fraction, 4), "level": level})
    return {"currency": budgets.get("currency", "USD"), "since": since, "until": until,
            "alerts": [b for b in out if b["level"] != "ok"], "budgets": out}
