import json
import threading
import unittest
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, HTTPServer

import router

ENV = {
    "FORGE_DEFAULT_BASE_URL": "http://stub:8080/v1", "FORGE_DEFAULT_MODEL": "flash", "FORGE_DEFAULT_TOKEN": "t1",
    "FORGE_ESCALATION_BASE_URL": "https://gw.invalid/serving-endpoints",
    "FORGE_ESCALATION_MODEL": "opus", "FORGE_ESCALATION_TOKEN": "t2",
}


class ClassifyTest(unittest.TestCase):
    def test_default_when_absent_or_unknown(self):
        self.assertEqual(router.classify(None), "default")
        self.assertEqual(router.classify("docs"), "default")

    def test_escalation_tasks(self):
        for task in ("security", "Architecture", " repeated-failure "):
            self.assertEqual(router.classify(task), "escalation")


class RoutesTest(unittest.TestCase):
    def test_loads_both_routes(self):
        routes = router.load_routes(ENV)
        self.assertEqual(routes["escalation"]["model"], "opus")

    def test_missing_variable_refused(self):
        env = dict(ENV)
        del env["FORGE_DEFAULT_TOKEN"]
        with self.assertRaises(ValueError):
            router.load_routes(env)

    def test_plain_http_to_real_host_refused(self):
        env = dict(ENV, FORGE_DEFAULT_BASE_URL="http://gw.invalid/v1")
        with self.assertRaises(ValueError):
            router.load_routes(env)

    def test_plain_http_allowed_to_named_hosts_only(self):
        env = dict(ENV, FORGE_DEFAULT_BASE_URL="http://egress:3128/u/models-default/v1")
        with self.assertRaises(ValueError):
            router.load_routes(env)
        routes = router.load_routes(dict(env, FORGE_PLAIN_HTTP_HOSTS="stub,egress"))
        self.assertEqual(routes["default"]["base"], "http://egress:3128/u/models-default/v1")
        with self.assertRaises(ValueError):
            router.load_routes(dict(env, FORGE_PLAIN_HTTP_HOSTS="stub,egress", FORGE_ESCALATION_BASE_URL="http://other:1/v1"))

    def test_identity_headers_forwarded_and_client_credential_not(self):
        headers = router.upstream_headers({"token": "t1"}, 5, {"X-Forge-User": "u", "Authorization": "Bearer c"})
        self.assertEqual(headers["X-Forge-User"], "u")
        self.assertEqual(headers["Authorization"], "Bearer t1")
        self.assertNotIn("X-Forge-Session", headers)

    def test_vector_shared_with_the_gateway(self):
        self.assertEqual(router.sign_identity("k" * 32, "alice", "s-1", 1700000000),
                         "v1.1700000000.de065d7f6953319f5adde4f34daba6f80f5611d59753ca1c1969bef8a1664163")

    def test_signature_covers_the_forwarded_identity(self):
        secret = "k" * 32
        headers = router.upstream_headers({"token": "t1"}, 5, {"X-Forge-User": "alice", "X-Forge-Session": "s-1",
                                                                "X-Forge-Identity": "v1.1.forged"}, secret, 1700000000)
        self.assertEqual(headers["X-Forge-Identity"], router.sign_identity(secret, "alice", "s-1", 1700000000))
        bare = router.upstream_headers({"token": "t1"}, 5, {"X-Forge-Identity": "v1.1.forged"}, secret, 1700000000)
        self.assertEqual(bare["X-Forge-Identity"], router.sign_identity(secret, None, None, 1700000000))

    def test_no_secret_no_signature_and_client_signature_never_forwarded(self):
        headers = router.upstream_headers({"token": "t1"}, 5, {"X-Forge-User": "u", "X-Forge-Identity": "v1.1.x"})
        self.assertNotIn("X-Forge-Identity", headers)

    def test_identity_secret_from_env(self):
        self.assertIsNone(router.identity_secret({}))
        self.assertIsNone(router.identity_secret({"FORGE_IDENTITY_SECRET": ""}))
        self.assertEqual(router.identity_secret({"FORGE_IDENTITY_SECRET": "k" * 32}), "k" * 32)
        with self.assertRaises(ValueError):
            router.identity_secret({"FORGE_IDENTITY_SECRET": "short"})

    def test_rewrite_body_and_headers(self):
        body = json.loads(router.rewrite_body(b'{"model":"x","messages":[]}', "flash"))
        self.assertEqual(body["model"], "flash")
        self.assertEqual(router.upstream_headers({"token": "t1"}, 5)["Authorization"], "Bearer t1")
        with self.assertRaises(ValueError):
            router.rewrite_body(b"[1]", "flash")


class EndToEndTest(unittest.TestCase):
    def test_route_rewrite_and_credential_swap(self):
        seen = {}

        class Upstream(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                seen["auth"] = self.headers.get("Authorization")
                seen["task"] = self.headers.get("X-Forge-Task")
                seen["body"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                data = b'{"ok":true}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        up = HTTPServer(("127.0.0.1", 0), Upstream)
        threading.Thread(target=up.serve_forever, daemon=True).start()
        routes = {"default": {"base": "http://127.0.0.1:%d/v1" % up.server_port, "model": "flash", "token": "up-token"}}
        routes["escalation"] = routes["default"]
        srv = router.ThreadingHTTPServer(("127.0.0.1", 0), router.make_handler(routes))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        conn = HTTPConnection("127.0.0.1", srv.server_port)
        conn.request("POST", "/v1/chat/completions", json.dumps({"model": "any", "messages": []}),
                     {"Authorization": "Bearer client-secret", "Content-Type": "application/json"})
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        self.assertEqual(json.loads(resp.read()), {"ok": True})
        self.assertEqual(seen["auth"], "Bearer up-token")
        self.assertEqual(seen["body"]["model"], "flash")
        srv.shutdown()
        up.shutdown()


if __name__ == "__main__":
    unittest.main()
