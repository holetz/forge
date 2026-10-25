import json
import os
import tempfile
import threading
import time
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


SECRET = "s" * 40


class IdentityTest(unittest.TestCase):
    def test_vector_shared_with_the_router(self):
        self.assertEqual(egress.sign_identity("k" * 32, "alice", "s-1", 1700000000),
                         "v1.1700000000.de065d7f6953319f5adde4f34daba6f80f5611d59753ca1c1969bef8a1664163")

    def test_verify_accepts_only_a_fresh_exact_signature(self):
        now = 1700000000
        good = egress.sign_identity(SECRET, "alice", "s-1", now)
        self.assertTrue(egress.verify_identity(SECRET, "alice", "s-1", good, now + 10))
        self.assertFalse(egress.verify_identity(SECRET, "mallory", "s-1", good, now))
        self.assertFalse(egress.verify_identity(SECRET, "alice", "s-2", good, now))
        self.assertFalse(egress.verify_identity("t" * 40, "alice", "s-1", good, now))
        self.assertFalse(egress.verify_identity(SECRET, "alice", "s-1", good, now + 301))
        self.assertFalse(egress.verify_identity(SECRET, "alice", "s-1", good, now - 301))
        self.assertFalse(egress.verify_identity(None, "alice", "s-1", good, now))
        for bad in (None, "", "v1", "v1.x.y", "v2." + good[3:], good + ".z", good[:-1], "v1.%d.\u00e9" % now):
            self.assertFalse(egress.verify_identity(SECRET, "alice", "s-1", bad, now), bad)

    def test_secret_from_the_environment(self):
        self.assertIsNone(egress.identity_secret({}))
        self.assertIsNone(egress.identity_secret({"EGRESS_IDENTITY_SECRET": " "}))
        self.assertEqual(egress.identity_secret({"EGRESS_IDENTITY_SECRET": SECRET}), SECRET)
        with self.assertRaises(ValueError):
            egress.identity_secret({"EGRESS_IDENTITY_SECRET": "short"})


class PathTest(unittest.TestCase):
    def test_dot_segments_and_encoded_separators_refused(self):
        for rest in ("/../x", "/a/../../x", "/./x", "/%2e%2e/x", "/%2E%2e/x", "/a/%2e./x", "/%252e%252e/x",
                     "/%25252e%25252e/x", "/..;x/y", "/a%2fb", "/a%2Fb", "/a%5cb", "/a\\b", "/a%00b", "/a//b",
                     "/a%250ab", "/a#b", "/%2525252e/x"):
            self.assertFalse(egress.safe_rest(rest), rest)

    def test_ordinary_paths_pass(self):
        for rest in ("/", "/v1/chat/completions", "/a/b/", "/a.b/c..d", "/org%20name/repo.git/info/refs"):
            self.assertTrue(egress.safe_rest(rest), rest)

    def test_the_reported_escape_is_refused(self):
        target = egress.split_target("/u/models-default/../../../api/2.0/token/create")
        self.assertFalse(egress.safe_rest(target[1]))
        self.assertFalse(egress.safe_rest(egress.split_target("/u/models-default/%2e%2e/%2e%2e/api")[1]))

    def test_split_target_ignores_a_network_path_prefix(self):
        self.assertIsNone(egress.split_target("//evil/u/git/x"))
        self.assertIsNone(egress.split_target("http://evil/u/git/x"))

    def test_resolved_path_stays_under_the_base(self):
        self.assertTrue(egress.under_base("/ai-gateway/mlflow/v1", "/chat/completions"))
        self.assertTrue(egress.under_base("", "/x"))
        self.assertFalse(egress.under_base("/ai-gateway/mlflow/v1", "/../../api/2.0/token/create"))
        self.assertFalse(egress.under_base("/base", "/../other"))

    def test_allow_rules(self):
        ups = egress.load_upstreams(UPSTREAMS, {})
        models = ups["models-default"]
        self.assertTrue(egress.permitted(models, "POST", "/v1/chat/completions"))
        self.assertTrue(egress.permitted(models, "POST", "/chat/completions"))
        self.assertFalse(egress.permitted(models, "GET", "/chat/completions"))
        self.assertFalse(egress.permitted(models, "POST", "/api/2.0/token/create"))
        self.assertFalse(egress.permitted(models, "POST", "/chat/completionsx"))
        self.assertTrue(egress.permitted(ups["git"], "POST", "/org/repo.git/git-upload-pack"))
        self.assertFalse(egress.permitted(ups["git"], "DELETE", "/org/repo.git"))

    def test_upstream_without_allow_rules_fails_to_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            for allow in (None, [], [{"methods": [], "prefixes": ["/"]}], [{"methods": ["GET"], "prefixes": ["x"]}],
                          [{"methods": ["FETCH"], "prefixes": ["/"]}], ["GET /"]):
                entry = {"base_url_env": "B", "token_env": "T"}
                if allow is not None:
                    entry["allow"] = allow
                path = os.path.join(tmp, "u.json")
                with open(path, "w", encoding="utf-8") as handle:
                    json.dump({"x": entry}, handle)
                with self.assertRaises(ValueError, msg=repr(allow)):
                    egress.load_upstreams(path, {})


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
                seen["headers"] = {k.lower(): v for k, v in self.headers.items()}
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
                                  "token": UPSTREAM_SECRET, "auth": "bearer", "record_model": True,
                                  "allow": egress.parse_allow("models-default", [{"methods": ["POST"], "prefixes": ["/chat/completions"]}])},
               "git": {"base": "", "token": "", "auth": "basic", "record_model": False,
                       "allow": egress.parse_allow("git", [{"methods": ["GET"], "prefixes": ["/"]}])}}
        audit = egress.Audit(cls.audit_path, 1 << 20)
        cls.gateway = HTTPServer(("127.0.0.1", 0), egress.make_handler(ups, audit, SECRET))
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

    def test_only_allowlisted_headers_are_forwarded(self):
        dropped = {"Cookie": "c=1", "X-Api-Key": "k", "api-key": "k", "PRIVATE-TOKEN": "p", "X-Forge-Identity": "v1.1.aa",
                   "Authorization": "Bearer " + CLIENT_SECRET, "Proxy-Authorization": "x", "X-Custom-Token": "t"}
        sent = {"Content-Type": "application/json", "Accept": "application/json", "User-Agent": "probe/1"}
        self.call("POST", "/u/models-default/chat/completions", b"{}", dict(sent, **dropped))
        got = self.seen["headers"]
        for name in set(dropped) - {"Authorization"}:
            self.assertNotIn(name.lower(), got, name)
        for name, value in sent.items():
            self.assertEqual(got[name.lower()], value)
        self.assertEqual(got["authorization"], "Bearer " + UPSTREAM_SECRET)

    def signed(self, user, session, **extra):
        headers = {"X-Forge-User": user, "X-Forge-Session": session,
                   "X-Forge-Identity": egress.sign_identity(SECRET, user, session, time.time())}
        headers.update(extra)
        return headers

    def test_audit_line_has_identity_model_and_usage_but_no_secret(self):
        self.call("POST", "/u/models-default/chat/completions", b'{"model":"flash"}',
                  self.signed("dev@example.invalid", "s-1", Authorization="Bearer " + CLIENT_SECRET))
        record = self.audit_lines()[-1]
        self.assertEqual((record["upstream"], record["model"], record["user"], record["session"], record["identity"]),
                         ("models-default", "flash", "dev@example.invalid", "s-1", "router"))
        self.assertEqual(record["usage"]["total_tokens"], 5)
        with open(self.audit_path, encoding="utf-8") as handle:
            text = handle.read()
        self.assertNotIn(CLIENT_SECRET, text)
        self.assertNotIn(UPSTREAM_SECRET, text)

    def test_forged_identity_is_recorded_as_unauthenticated(self):
        stale = egress.sign_identity(SECRET, "alice", "s-9", time.time() - 1000)
        wrong = egress.sign_identity("w" * 40, "alice", "s-9", time.time())
        swapped = self.signed("alice", "s-9")
        swapped["X-Forge-User"] = "admin"
        cases = {"none": {"X-Forge-User": "admin", "X-Forge-Session": "s-9"},
                 "stale": {"X-Forge-User": "alice", "X-Forge-Session": "s-9", "X-Forge-Identity": stale},
                 "wrong key": {"X-Forge-User": "alice", "X-Forge-Session": "s-9", "X-Forge-Identity": wrong},
                 "swapped user": swapped, "garbage": {"X-Forge-User": "admin", "X-Forge-Identity": "v1.1.zz"}}
        for label, headers in cases.items():
            self.call("POST", "/u/models-default/chat/completions", b"{}", headers)
            record = self.audit_lines()[-1]
            self.assertEqual((record["user"], record["session"], record["identity"]),
                             ("unauthenticated",) * 3, label)
        with open(self.audit_path, encoding="utf-8") as handle:
            self.assertNotIn("admin", handle.read())

    def test_a_request_with_no_identity_is_unauthenticated(self):
        self.call("GET", "/u/unknown/x")
        for _ in range(100):
            if os.path.exists(self.audit_path) and self.audit_lines():
                break
            time.sleep(0.02)
        self.assertEqual(self.audit_lines()[-1]["identity"], "unauthenticated")

    def test_refusals(self):
        self.assertEqual(self.call("GET", "/u/unknown/x")[0], 403)
        self.assertEqual(self.call("GET", "/elsewhere")[0], 403)
        self.assertEqual(self.call("GET", "/u/git/x")[0], 503)
        self.assertEqual(self.call("CONNECT", "example.invalid:443")[0], 405)
        self.assertEqual(self.call("GET", "/healthz")[0], 200)

    def test_path_escape_and_disallowed_requests_never_reach_the_upstream(self):
        self.seen.clear()
        for path in ("/u/models-default/../../../api/2.0/token/create", "/u/models-default/%2e%2e/api",
                     "/u/models-default/chat%2fcompletions", "/u/models-default/%252e%252e/api"):
            self.assertEqual(self.call("POST", path, b"{}", {"Content-Type": "application/json"})[0], 400, path)
        self.assertEqual(self.call("POST", "/u/models-default/api/2.0/token/create", b"{}")[0], 403)
        self.assertEqual(self.call("GET", "/u/models-default/chat/completions")[0], 403)
        self.assertEqual(self.seen, {})
        self.assertEqual(self.audit_lines()[-1]["status"], 403)

    def test_audit_rotates_at_the_cap(self):
        path = os.path.join(self.tmp.name, "rot.jsonl")
        audit = egress.Audit(path, 100)
        for _ in range(5):
            audit.write({"k": "x" * 40})
        self.assertTrue(os.path.exists(path + ".1"))
        self.assertLessEqual(os.path.getsize(path), 100)


if __name__ == "__main__":
    unittest.main()
