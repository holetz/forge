import copy
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from forge_policies import params  # noqa: E402


class ParamsTest(unittest.TestCase):
    def setUp(self):
        self.defaults = params.load_defaults()

    def test_defaults_are_valid(self):
        self.assertEqual(params.validate(self.defaults), [])

    def test_merge_overrides_key_by_key(self):
        merged = params.merge(self.defaults, {"egress": {"allow_hosts": ["git.example.com"]}})
        self.assertEqual(merged["egress"]["allow_hosts"], ["git.example.com"])
        self.assertEqual(merged["egress"]["action_unparsed"], "ASK")
        self.assertEqual(merged["budget"], self.defaults["budget"])
        self.assertEqual(params.validate(merged), [])

    def test_merge_add_and_remove_edit_the_default_list(self):
        merged = params.merge(self.defaults, {"approval": {"commands_add": ["pulumi up", "git push"],
                                                           "commands_remove": ["npm publish"]}})
        commands = merged["approval"]["commands"]
        self.assertIn("pulumi up", commands)
        self.assertNotIn("npm publish", commands)
        self.assertEqual(commands.count("git push"), 1)
        self.assertEqual(len(commands), len(self.defaults["approval"]["commands"]))
        self.assertNotIn("commands_add", merged["approval"])
        self.assertEqual(params.validate(merged), [])

    def test_merge_add_applies_after_a_replacement(self):
        merged = params.merge(self.defaults, {"egress": {"allow_hosts": ["a.example.com"],
                                                         "allow_hosts_add": ["b.example.com"]}})
        self.assertEqual(merged["egress"]["allow_hosts"], ["a.example.com", "b.example.com"])

    def test_merge_add_keeps_the_loopback_defaults(self):
        merged = params.merge(self.defaults, {"egress": {"allow_hosts_add": ["git.example.com"]}})
        self.assertEqual(merged["egress"]["allow_hosts"], ["localhost", "127.0.0.1", "::1", "git.example.com"])

    def test_edit_keys_on_a_scalar_are_unknown(self):
        merged = params.merge(self.defaults, {"approval": {"action_add": ["x"]}})
        self.assertTrue(any("action_add" in e for e in params.validate(merged)))

    def test_edit_key_with_a_non_list_value_is_refused(self):
        merged = params.merge(self.defaults, {"approval": {"commands_add": "git push"}})
        self.assertTrue(any("commands_add" in e for e in params.validate(merged)))

    def test_merge_does_not_mutate_defaults(self):
        before = copy.deepcopy(self.defaults)
        params.merge(self.defaults, {"budget": {"max_tool_calls": 5}})
        self.assertEqual(self.defaults, before)

    def test_unknown_group_and_key_refused(self):
        merged = params.merge(self.defaults, {"bogus": {}, "egress": {"nope": 1}})
        errors = params.validate(merged)
        self.assertTrue(any("bogus" in e for e in errors))
        self.assertTrue(any("egress.nope" in e for e in errors))

    def test_wrong_types_refused(self):
        for override, needle in [
            ({"approval": {"action": "ALLOW"}}, "approval.action"),
            ({"approval": {"commands": "git push"}}, "approval.commands"),
            ({"budget": {"session_max_usd": -1}}, "session_max_usd"),
            ({"budget": {"max_tool_calls": 1.5}}, "max_tool_calls"),
            ({"output": {"detect": ["nope"]}}, "unknown category"),
            ({"output": {"phases": ["request"]}}, "unknown phase"),
            ({"output": {"extra_patterns": ["("]}}, "extra_patterns"),
        ]:
            errors = params.validate(params.merge(self.defaults, override))
            self.assertTrue(any(needle in e for e in errors), (override, errors))

    def test_thresholds_must_be_below_limit_and_increasing(self):
        bad = params.merge(self.defaults, {"budget": {"session_ask_usd": [10]}})
        self.assertTrue(any("below" in e for e in params.validate(bad)))
        bad = params.merge(self.defaults, {"budget": {"daily_ask_usd": [20, 10]}})
        self.assertTrue(any("increasing" in e for e in params.validate(bad)))
        bad = params.merge(self.defaults, {"budget": {"session_max_usd": None}})
        self.assertTrue(any("needs session_max_usd" in e for e in params.validate(bad)))

    def test_render_names_real_handlers(self):
        cfg = params.render(self.defaults)
        self.assertEqual(cfg["policy_modules"], ["forge_policies"])
        handlers = {n: p["handler"] for n, p in cfg["policies"].items()}
        self.assertEqual(list(handlers)[:2], ["forge__egress", "forge__approval"])
        self.assertEqual(handlers["forge__session_budget"],
                         "omnigent.policies.builtins.cost.cost_budget")
        self.assertEqual(handlers["forge__daily_budget"],
                         "omnigent.policies.builtins.cost.user_daily_cost_budget")
        self.assertEqual(handlers["forge__tool_call_limit"],
                         "omnigent.policies.builtins.safety.max_tool_calls_per_session")
        fp = cfg["policies"]["forge__session_budget"]["factory_params"]
        self.assertEqual(fp, {"max_cost_usd": 10, "ask_thresholds_usd": [5]})

    def test_null_budget_drops_the_policy(self):
        merged = params.merge(self.defaults, {"budget": {
            "daily_max_usd": None, "daily_ask_usd": [], "max_tool_calls": None}})
        names = params.render(merged)["policies"]
        self.assertNotIn("forge__daily_budget", names)
        self.assertNotIn("forge__tool_call_limit", names)
        self.assertIn("forge__session_budget", names)

    def test_render_refuses_invalid(self):
        with self.assertRaises(ValueError):
            params.render(params.merge(self.defaults, {"egress": {"action_unparsed": "x"}}))

    def test_render_text_is_json(self):
        json.loads(params.render_text(self.defaults))


if __name__ == "__main__":
    unittest.main()
