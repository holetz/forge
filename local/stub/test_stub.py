import json
import threading
import unittest
from http.client import HTTPConnection

import stub


class StubTest(unittest.TestCase):
    def setUp(self):
        self.srv = stub.ThreadingHTTPServer(("127.0.0.1", 0), stub.make_handler("tok"))
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()

    def call(self, method, path, body=None, headers=None):
        conn = HTTPConnection("127.0.0.1", self.srv.server_port)
        conn.request(method, path, json.dumps(body) if body is not None else None, headers or {})
        resp = conn.getresponse()
        return resp.status, resp.read()

    def test_completion_and_last(self):
        status, data = self.call("POST", "/v1/chat/completions", {"model": "m1"},
                                 {"Authorization": "Bearer tok", "X-Forge-Task": "security"})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data)["model"], "m1")
        _, last = self.call("GET", "/_last")
        last = json.loads(last)
        self.assertEqual((last["model"], last["credential_ok"], last["task"]), ("m1", True, "security"))

    def test_wrong_credential_reported(self):
        self.call("POST", "/v1/chat/completions", {"model": "m1"}, {"Authorization": "Bearer other"})
        _, last = self.call("GET", "/_last")
        self.assertFalse(json.loads(last)["credential_ok"])

    def test_stream(self):
        status, data = self.call("POST", "/v1/chat/completions", {"model": "m1", "stream": True})
        self.assertEqual(status, 200)
        self.assertTrue(data.rstrip().endswith(b"data: [DONE]"))


if __name__ == "__main__":
    unittest.main()
