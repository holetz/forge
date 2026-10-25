import os
import sqlite3
import unittest

import cost
import fixtures

HERE = os.path.dirname(os.path.abspath(__file__))


def small():
    """Two requests and two billing rows whose costs are easy to do by hand."""
    return {
        "gateway": [
            {"request_id": "r1", "event_time": "2026-10-01T00:00:00Z", "requester": "p1", "endpoint_name": "ep",
             "input_tokens": 1_000_000, "cache_read_tokens": 0, "output_tokens": 0,
             "tags": {"session_id": "s1", "project": "pr1"}},
            {"request_id": "r2", "event_time": "2026-10-02T00:00:00Z", "requester": "p2", "endpoint_name": "ep",
             "input_tokens": 0, "cache_read_tokens": 1_000_000, "output_tokens": 500_000, "tags": {}},
        ],
        "billing": [
            {"record_id": "b1", "usage_date": "2026-10-01", "sku_name": "sku", "usage_unit": "DBU", "usage_quantity": 10,
             "identity_metadata": {"run_as": "p1"}, "custom_tags": {"session_id": "s1", "project": "pr1"}},
            {"record_id": "b2", "usage_date": "2026-10-03", "sku_name": "nosku", "usage_unit": "DBU", "usage_quantity": 4,
             "identity_metadata": {"run_as": "p3"}, "custom_tags": {}},
        ],
        "prices": {"as_of": "x", "endpoints": {"ep": {"input": 2.0, "cache_read": 0.5, "output": 10.0}}, "skus": {"sku": 0.25}},
        "teams": {"p1": "t1", "p2": "t1"},
    }


def db_of(data):
    return cost.load(cost.FixtureReader(data))


def by_name(result):
    return {r["name"]: r for r in result["rows"]}


class PricingTest(unittest.TestCase):
    def test_gateway_and_billing_costs_match_hand_arithmetic(self):
        rows = by_name(cost.cost_by(db_of(small()), "person"))
        self.assertAlmostEqual(rows["p1"]["gateway"], 2.0)    # 1M input at 2.0 per million
        self.assertAlmostEqual(rows["p1"]["billing"], 2.5)    # 10 DBU at 0.25
        self.assertAlmostEqual(rows["p2"]["gateway"], 5.5)    # 0.5 cache read + 5.0 output
        self.assertAlmostEqual(rows["p1"]["total"], 4.5)

    def test_unpriced_rows_are_named_and_not_counted_as_zero_cost(self):
        result = cost.cost_by(db_of(small()), "person")
        self.assertEqual(result["unpriced"], {"rows": 1, "refs": ["billing:b2"]})
        self.assertAlmostEqual(result["total"], 10.0)


class AttributionTest(unittest.TestCase):
    def test_person_without_team_is_unassigned_and_missing_tags_are_untagged(self):
        db = db_of(small())
        self.assertIn("unassigned", by_name(cost.cost_by(db, "team")))
        self.assertIn("untagged", by_name(cost.cost_by(db, "session")))
        self.assertIn("untagged", by_name(cost.cost_by(db, "project")))

    def test_session_and_project_views_collect_both_sources(self):
        db = db_of(small())
        self.assertAlmostEqual(by_name(cost.cost_by(db, "session"))["s1"]["total"], 4.5)
        self.assertAlmostEqual(by_name(cost.cost_by(db, "project"))["pr1"]["total"], 4.5)

    def test_every_view_reconciles_to_the_same_total(self):
        db = db_of(fixtures.build())
        totals = {v: cost.cost_by(db, v)["total"] for v in cost.VIEWS}
        self.assertGreater(totals["person"], 0)
        for view, total in totals.items():
            self.assertAlmostEqual(total, totals["person"], places=6, msg=view)
            rows = cost.cost_by(db, view)["rows"]
            self.assertAlmostEqual(sum(r["total"] for r in rows), total, places=4)

    def test_unpriced_rows_are_reported_for_both_sources(self):
        data = fixtures.build()
        data["gateway"].append(dict(data["gateway"][0], request_id="req-unlisted", endpoint_name="ep-unlisted"))
        refs = cost.cost_by(db_of(data), "person")["unpriced"]["refs"]
        self.assertTrue(any(r.startswith("gateway:") for r in refs))
        self.assertTrue(any(r.startswith("billing:") for r in refs))


class WindowTest(unittest.TestCase):
    def test_since_and_until_bound_by_day(self):
        db = db_of(small())
        self.assertAlmostEqual(cost.cost_by(db, "person", until="2026-10-01")["total"], 4.5)
        self.assertAlmostEqual(cost.cost_by(db, "person", since="2026-10-02")["total"], 5.5)

    def test_unknown_view_refused(self):
        with self.assertRaises(ValueError):
            cost.cost_by(db_of(small()), "planet")


class BudgetTest(unittest.TestCase):
    def test_levels_at_warning_and_cap(self):
        budgets = {"budgets": [
            {"scope": "person", "name": "p1", "cap": 4.5, "warn": 0.8},
            {"scope": "person", "name": "p2", "cap": 6.5, "warn": 0.8},
            {"scope": "team", "name": "t1", "cap": 100.0, "warn": 0.8},
        ]}
        status = cost.budget_status(db_of(small()), budgets)
        levels = {b["name"]: b["level"] for b in status["budgets"]}
        self.assertEqual(levels, {"p1": "over", "p2": "warning", "t1": "ok"})
        self.assertEqual([a["name"] for a in status["alerts"]], ["p1", "p2"])

    def test_shipped_budgets_run_against_the_fixture(self):
        import json
        with open(os.path.join(HERE, "budgets.json"), encoding="utf-8") as handle:
            budgets = json.load(handle)
        status = cost.budget_status(db_of(fixtures.build()), budgets)
        self.assertEqual(len(status["budgets"]), len(budgets["budgets"]))


class IsolationTest(unittest.TestCase):
    def test_module_opens_no_connection_but_the_in_memory_database(self):
        with open(os.path.join(HERE, "cost.py"), encoding="utf-8") as handle:
            source = handle.read()
        for word in ("import socket", "databricks", "urllib", "http.client", "requests"):
            self.assertNotIn(word, source)
        self.assertIsInstance(db_of(small()), sqlite3.Connection)


if __name__ == "__main__":
    unittest.main()
