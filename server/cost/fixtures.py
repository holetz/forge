"""Synthetic fixtures shaped like system.ai_gateway.usage and system.billing.usage.

Every name is invented and every value is derived from loop indices, so two calls return the same
data. The column names are the ones docs/cost.md lists as assumed; this module is the one place to
adjust when the live tables are described.
"""

PEOPLE = {"person-a": "team-x", "person-b": "team-x", "person-c": "team-y", "person-d": "team-y", "person-e": "team-z"}
PROJECTS = ["project-1", "project-2"]

# Per-million-token rates (input, cache read, output); same figures as the routing table of the epic.
ENDPOINT_PRICES = {
    "ep-flash": {"input": 0.30, "cache_read": 0.03, "output": 1.20},
    "ep-opus": {"input": 4.00, "cache_read": 0.20, "output": 20.00},
    "ep-haiku": {"input": 0.10, "cache_read": 0.01, "output": 0.50},
}
# Price per usage unit (DBU), as system.billing.list_prices would return for the workspace region.
SKU_PRICES = {"sku-jobs": 0.15, "sku-sql": 0.22, "sku-agent": 0.07}
PRICES_AS_OF = "2026-10-01"

ENDPOINTS = ["ep-flash", "ep-flash", "ep-opus", "ep-haiku", "ep-unlisted"]


def _gateway():
    rows = []
    for p, person in enumerate(PEOPLE):
        for s in range(2):
            session = "session-%s-%d" % (person[-1], s)
            for i in range(3):
                n = p * 100 + s * 10 + i
                tagged = not (p == 4 and s == 1)
                rows.append({
                    "request_id": "req-%04d" % n,
                    "event_time": "2026-10-%02dT10:00:00Z" % (1 + (n % 20)),
                    "requester": person,
                    "endpoint_name": ENDPOINTS[n % len(ENDPOINTS)],
                    "input_tokens": 10000 + 1000 * n,
                    "cache_read_tokens": 200000 + 5000 * n,
                    "output_tokens": 2000 + 100 * n,
                    "tags": {"session_id": session, "project": PROJECTS[(p + s) % 2]} if tagged else {},
                })
    return rows


def _billing():
    rows = []
    for p, person in enumerate(PEOPLE):
        for k, sku in enumerate(list(SKU_PRICES) + ["sku-unlisted"]):
            n = p * 10 + k
            rows.append({
                "record_id": "rec-%04d" % n,
                "usage_date": "2026-10-%02d" % (1 + (n % 20)),
                "sku_name": sku,
                "usage_unit": "DBU",
                "usage_quantity": 5.0 + n,
                "identity_metadata": {"run_as": person},
                "custom_tags": {"session_id": "session-%s-0" % person[-1], "project": PROJECTS[k % 2]} if k != 3 else {},
            })
    return rows


def build():
    return {
        "gateway": _gateway(),
        "billing": _billing(),
        "prices": {"as_of": PRICES_AS_OF, "endpoints": ENDPOINT_PRICES, "skus": SKU_PRICES},
        "teams": dict(PEOPLE),
    }
