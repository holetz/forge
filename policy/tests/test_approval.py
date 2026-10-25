import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from forge_policies import params  # noqa: E402
from forge_policies.approval import approval_gate  # noqa: E402


def shell(command, name="Bash"):
    return {"type": "tool_call", "target": name, "data": {"name": name, "arguments": {"command": command}}}


class ApprovalTest(unittest.TestCase):
    def setUp(self):
        cfg = params.load_defaults()["approval"]
        self.ev = approval_gate(**cfg)

    def verdict(self, event):
        out = self.ev(event)
        return out["result"] if out else None

    def test_push_asks(self):
        for cmd in ("git push origin main", "git -C /tmp/x push", "cd a && git push",
                    "/usr/bin/git push", "sudo terraform apply", "FOO=1 helm upgrade x y",
                    "databricks bundle deploy -t prod", "kubectl -n ns apply -f a.yaml"):
            self.assertEqual(self.verdict(shell(cmd)), "ASK", cmd)

    def test_benign_allowed(self):
        for cmd in ("git status", "git log --oneline", "ls -la", "terraform plan", "docker build ."):
            self.assertIsNone(self.ev(shell(cmd)), cmd)

    def test_other_shell_names_and_tools(self):
        self.assertEqual(self.verdict(shell("git push", "sys_os_shell")), "ASK")
        ev = {"type": "tool_call", "target": "mcp__git__push_branch", "data": {"name": "mcp__git__push_branch", "arguments": {}}}
        self.assertEqual(self.verdict(ev), "ASK")

    def test_other_phases_ignored(self):
        self.assertIsNone(self.ev({"type": "response", "data": "git push"}))

    def test_unparsable_with_head_asks(self):
        self.assertEqual(self.verdict(shell("echo 'unterminated; git push")), "ASK")

    def test_deny_action(self):
        ev = approval_gate(commands=["git push"], tools=[], action="DENY")
        self.assertEqual(ev(shell("git push"))["result"], "DENY")

    def test_reason_omits_command_arguments(self):
        out = self.ev(shell("git push https://user:tok@host/r.git"))
        self.assertNotIn("tok", out["reason"])


if __name__ == "__main__":
    unittest.main()
