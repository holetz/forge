import json
import os
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, HTTPServer

import egress

HERE = os.path.dirname(os.path.abspath(__file__))
UPSTREAMS = os.path.join(HERE, "upstreams.json")
CLIENT_SECRET = "client-credential-fixture"
UPSTREAM_SECRET = "upstream-credential-fixture"


class LoadTest(unittest.TestCase):
    def env(self, **extra):
        env = {"EGRESS_MODELS_DEFAULT_BASE_URL": "http://stub:8080/v1", "EGRESS_MODELS_DEFAULT_TOKEN": "t"}
        env.update(extra)
        return env

    def test_unset_base_url_disables_the_upstream(self):
        ups = egress.load_upstreams(UPSTREAMS, self.env())
        self.assertEqual(ups["git"]["base"], "")
        self.assertEqual(ups["models-default"]["base"], "http://stub:8080/v1")

    def test_plain_http_to_a_real_host_refused(self):
        with self.assertRaises(ValueError):
            egress.load_upstreams(UPSTREAMS, self.env(EGRESS_GIT_BASE_URL="http://git.invalid", EGRESS_GIT_TOKEN="t"))

    def test_base_url_without_token_refused(self):
        with self.assertRaises(ValueError):
            egress.load_upstreams(UPSTREAMS, self.env(EGRESS_GIT_BASE_URL="https://git.invalid"))

    def test_split_target_and_credential(self):
        self.assertEqual(egress.split_target("/u/git/a/b?x=1"), ("git", "/a/b", "x=1"))
        self.assertIsNone(egress.split_target("/other"))
        self.assertTrue(egress.credential({"auth": "basic", "token": "t"}).startswith("Basic "))
        self.assertEqual(egress.credential({"auth": "bearer", "token": "t"}), "Bearer t")


class GatewayTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.seen = {}
        seen = cls.seen

        class Upstream(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                seen["auth"] = self.headers.get("Authorization")
                seen["user"] = self.headers.get("X-Forge-User")
                seen["path"] = self.path
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                data = json.dumps({"usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        cls.upstream = HTTPServer(("127.0.0.1", 0), Upstream)
        threading.Thread(target=cls.upstream.serve_forever, daemon=True).start()
        cls.tmp = tempfile.TemporaryDirectory()
        cls.audit_path = os.path.join(cls.tmp.name, "egress.jsonl")
        ups = {"models-default": {"base": "http://127.0.0.1:%d/v1" % cls.upstream.server_port,
                                  "token": UPSTREAM_SECRET, "auth": "bearer", "record_model": True},
               "git": {"base": "", "token": "", "auth": "basic", "record_model": False}}
        audit = egress.Audit(cls.audit_path, 1 << 20)
        cls.gateway = HTTPServer(("127.0.0.1", 0), egress.make_handler(ups, audit))
        threading.Thread(target=cls.gateway.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.gateway.shutdown()
        cls.upstream.shutdown()
        cls.tmp.cleanup()

    def call(self, method, path, body=None, headers=None):
        conn = HTTPConnection("127.0.0.1", self.gateway.server_port)
        conn.request(method, path, body, headers or {})
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp.status, data

    def audit_lines(self):
        with open(self.audit_path, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle]

    def test_injects_upstream_credential_and_drops_the_clients(self):
        status, _ = self.call("POST", "/u/models-default/chat/completions", b'{"model":"flash"}',
                              {"Authorization": "Bearer " + CLIENT_SECRET, "Content-Type": "application/json",
                               "X-Forge-User": "dev@example.invalid"})
        self.assertEqual(status, 200)
        self.assertEqual(self.seen["auth"], "Bearer " + UPSTREAM_SECRET)
        self.assertEqual(self.seen["path"], "/v1/chat/completions")
        self.assertIsNone(self.seen["user"])

    def test_audit_line_has_identity_model_and_usage_but_no_secret(self):
        self.call("POST", "/u/models-default/chat/completions", b'{"model":"flash"}',
                  {"Authorization": "Bearer " + CLIENT_SECRET, "X-Forge-User": "dev@example.invalid",
                   "X-Forge-Session": "s-1"})
        record = self.audit_lines()[-1]
        self.assertEqual((record["upstream"], record["model"], record["user"], record["session"]),
                         ("models-default", "flash", "dev@example.invalid", "s-1"))
        self.assertEqual(record["usage"]["total_tokens"], 5)
        with open(self.audit_path, encoding="utf-8") as handle:
            text = handle.read()
        self.assertNotIn(CLIENT_SECRET, text)
        self.assertNotIn(UPSTREAM_SECRET, text)

    def test_refusals(self):
        self.assertEqual(self.call("GET", "/u/unknown/x")[0], 403)
        self.assertEqual(self.call("GET", "/elsewhere")[0], 403)
        self.assertEqual(self.call("GET", "/u/git/x")[0], 503)
        self.assertEqual(self.call("CONNECT", "example.invalid:443")[0], 405)
        self.assertEqual(self.call("GET", "/healthz")[0], 200)

    def test_audit_rotates_at_the_cap(self):
        path = os.path.join(self.tmp.name, "rot.jsonl")
        audit = egress.Audit(path, 100)
        for _ in range(5):
            audit.write({"k": "x" * 40})
        self.assertTrue(os.path.exists(path + ".1"))
        self.assertLessEqual(os.path.getsize(path), 100)


if __name__ == "__main__":
    unittest.main()
