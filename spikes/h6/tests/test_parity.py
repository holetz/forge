"""Parity: each corpus case runs through the baseline hook (subprocess) and the policy.

Verdicts must match, the policy must add no false positive, and every divergence must be listed
in the corpus as `known_divergence` with a reason. Stdlib only, synthetic values only.
"""
import json
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "policy"))

import h6_policies  # noqa: E402

with open(os.path.join(ROOT, "corpus.json"), encoding="utf-8") as fh:
    CASES = json.load(fh)["cases"]
with open(os.path.join(ROOT, "registry.synthetic.json"), encoding="utf-8") as fh:
    REGISTRY = json.load(fh)

HOOKS = {"compute": "compute_guard.py", "column": "column_override_guard.py"}
POLICIES = {
    "compute": h6_policies.compute_guard(approved_clusters=REGISTRY["approved_clusters"]),
    "column": h6_policies.column_override_guard(registry=REGISTRY["column_registry"]),
}


def guards(case):
    return list(HOOKS) if case["guard"] == "both" else [case["guard"]]


def hook_verdict(case):
    event = json.dumps({"tool_name": case["tool"], "tool_input": case["arguments"]})
    for guard in guards(case):
        run = subprocess.run([sys.executable, "-B", os.path.join(ROOT, "hooks", HOOKS[guard])],
                             input=event, capture_output=True, text=True,
                             env={**os.environ, "H6_REGISTRY": os.path.join(ROOT, "registry.synthetic.json")})
        if run.returncode == 2:
            return "DENY"
        if run.returncode != 0:
            raise AssertionError(f"{case['id']}: hook {guard} exited {run.returncode}: {run.stderr}")
    return "ALLOW"


def policy_verdict(case):
    event = {"type": "tool_call", "target": case["tool"],
             "data": {"name": case["tool"], "arguments": case["arguments"]}}
    for guard in guards(case):
        verdict = POLICIES[guard](event)
        if verdict and verdict.get("result") == "DENY":
            return "DENY"
    return "ALLOW"


class ParityTest(unittest.TestCase):
    def test_corpus_shape(self):
        ids = [c["id"] for c in CASES]
        self.assertEqual(len(ids), len(set(ids)), "duplicate case id")
        for case in CASES:
            self.assertIn(case["expect"], ("ALLOW", "DENY"), case["id"])
            self.assertIn(case["guard"], ("compute", "column", "both"), case["id"])

    def test_every_case(self):
        for case in CASES:
            with self.subTest(case=case["id"]):
                hook, policy = hook_verdict(case), policy_verdict(case)
                div = case.get("known_divergence")
                if div is None:
                    self.assertEqual(hook, case["expect"], "hook disagrees with expect")
                    self.assertEqual(policy, case["expect"], "policy disagrees with expect")
                    continue
                self.assertTrue(div.get("reason"), "divergence without a reason")
                self.assertEqual((hook, policy), (div["hook"], div["policy"]), "stale divergence")
                self.assertNotEqual(hook, policy, "listed divergence no longer diverges")

    def test_no_policy_false_positive(self):
        for case in CASES:
            with self.subTest(case=case["id"]):
                if case["expect"] == "ALLOW" and policy_verdict(case) == "DENY":
                    self.fail("policy blocks a call that should be allowed")


if __name__ == "__main__":
    unittest.main()
