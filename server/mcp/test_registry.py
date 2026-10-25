import json
import os
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from http.server import HTTPServer

import registry

HERE = os.path.dirname(os.path.abspath(__file__))
REGISTRY = os.path.join(HERE, "registry.json")
UPSTREAMS = os.path.join(HERE, "..", "egress", "upstreams.json")


def write(tmp, servers):
    path = os.path.join(tmp, "r.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"servers": servers}, handle)
    return path


class RegistryFileTest(unittest.TestCase):
    def test_shipped_registry_is_consistent_with_the_gateway_allowlist(self):
        servers = registry.load_registry(REGISTRY, UPSTREAMS)
        self.assertTrue(servers)
        self.assertTrue(all(not s["enabled"] for s in servers))

    def test_entry_without_a_gateway_upstream_refused(self):
        entry = dict(registry.load_registry(REGISTRY, UPSTREAMS)[0], upstream="not-listed")
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(ValueError):
            registry.load_registry(write(tmp, [entry]), UPSTREAMS)

    def test_repeated_name_and_missing_field_refused(self):
        entry = registry.load_registry(REGISTRY, UPSTREAMS)[0]
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                registry.load_registry(write(tmp, [entry, entry]), UPSTREAMS)
            with self.assertRaises(ValueError):
                registry.load_registry(write(tmp, [{"name": "x"}]), UPSTREAMS)


class ServiceTest(unittest.TestCase):
    def test_lists_entries_with_the_gateway_endpoint_and_no_upstream_detail(self):
        servers = registry.load_registry(REGISTRY, UPSTREAMS)
        srv = HTTPServer(("127.0.0.1", 0), registry.make_handler(servers, "http://egress:3128"))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            conn = HTTPConnection("127.0.0.1", srv.server_port)
            conn.request("GET", "/servers")
            listed = json.loads(conn.getresponse().read())["servers"]
            self.assertEqual(listed[0]["endpoint"], "http://egress:3128/u/mcp-catalog/")
            self.assertNotIn("upstream", listed[0])
            conn.request("GET", "/servers/%s" % listed[0]["name"])
            one = conn.getresponse()
            one.read()
            self.assertEqual(one.status, 200)
            conn.request("GET", "/servers/nope")
            missing = conn.getresponse()
            missing.read()
            self.assertEqual(missing.status, 404)
            conn.close()
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main()
