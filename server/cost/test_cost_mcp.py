import json
import os
import re
import threading
import unittest
from http.client import HTTPConnection
from http.server import HTTPServer

import cost
import cost_mcp
import fixtures

HERE = os.path.dirname(os.path.abspath(__file__))


class ProtocolTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(HERE, "budgets.json"), encoding="utf-8") as handle:
            budgets = json.load(handle)
        reader = cost.FixtureReader(fixtures.build())
        cls.server = HTTPServer(("127.0.0.1", 0), cost_mcp.make_handler(reader, budgets))
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.port = cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def post(self, body, path="/mcp"):
        conn = HTTPConnection("127.0.0.1", self.port, timeout=5)
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        conn.request("POST", path, raw, {"Content-Type": "application/json"})
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp.status, (json.loads(data) if data else None)

    def rpc(self, method, params=None, ident=1):
        msg = {"jsonrpc": "2.0", "id": ident, "method": method}
        if params is not None:
            msg["params"] = params
        return self.post(msg)[1]

    def tool(self, name, args=None):
        reply = self.rpc("tools/call", {"name": name, "arguments": args or {}})
        return json.loads(reply["result"]["content"][0]["text"])

    def test_initialize_and_tools_list(self):
        self.assertEqual(self.rpc("initialize")["result"]["serverInfo"]["name"], "forge-cost")
        names = [t["name"] for t in self.rpc("tools/list")["result"]["tools"]]
        self.assertEqual(names, ["cost_by_person", "cost_by_team", "cost_by_session", "cost_by_project", "budget_status"])

    def test_every_view_returns_the_same_total(self):
        totals = {self.tool("cost_by_" + v)["total"] for v in ("person", "team", "session", "project")}
        self.assertEqual(len(totals), 1)
        self.assertGreater(totals.pop(), 0)

    def test_window_narrows_the_answer(self):
        full = self.tool("cost_by_person")["total"]
        early = self.tool("cost_by_person", {"until": "2026-10-05"})["total"]
        self.assertLess(early, full)

    def test_budget_status_reports_alerts(self):
        status = self.tool("budget_status")
        self.assertEqual(len(status["budgets"]), 4)
        self.assertTrue(all(a["level"] != "ok" for a in status["alerts"]))

    def test_errors_are_json_rpc_errors(self):
        self.assertEqual(self.rpc("nope")["error"]["code"], -32601)
        self.assertEqual(self.rpc("tools/call", {"name": "drop_tables"})["error"]["code"], -32602)
        self.assertEqual(self.rpc("tools/call", {"name": "cost_by_person", "arguments": {"since": "yesterday"}})["error"]["code"], -32602)
        self.assertEqual(self.rpc("tools/call", {"name": "cost_by_person", "arguments": {"x": 1}})["error"]["code"], -32602)
        self.assertEqual(self.post(b"{not json")[1]["error"]["code"], -32700)
        self.assertEqual(self.post([1])[1]["error"]["code"], -32600)

    def test_notification_gets_no_body_and_other_paths_404(self):
        status, body = self.post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.assertEqual((status, body), (202, None))
        self.assertEqual(self.post({}, path="/other")[0], 404)


class NoLiveSourceTest(unittest.TestCase):
    def test_sources_hold_no_databricks_client_host_token_or_email(self):
        token = "d" + "api"
        for name in ("cost.py", "cost_mcp.py", "fixtures.py", "budgets.json"):
            with open(os.path.join(HERE, name), encoding="utf-8") as handle:
                text = handle.read()
            self.assertNotRegex(text, r"(?i)import\s+(databricks|requests|urllib)|from\s+databricks")
            self.assertNotIn(token, text, name)
            self.assertNotRegex(text, r"azuredatabricks|\.cloud\.databricks|[\w.]+@[\w.]+\.\w+", name)
            self.assertIsNone(re.search(r"https?://", text), name)


if __name__ == "__main__":
    unittest.main()
