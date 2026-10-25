import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import forge_policies  # noqa: E402
from forge_policies import params  # noqa: E402


class RegistryTest(unittest.TestCase):
    def test_every_rendered_own_handler_is_registered_and_callable(self):
        registered = {e["handler"] for e in forge_policies.POLICY_REGISTRY}
        cfg = params.render(params.load_defaults())
        own = {p["handler"] for p in cfg["policies"].values() if p["handler"].startswith("forge_policies.")}
        self.assertEqual(own, registered)
        for handler in registered:
            self.assertTrue(callable(getattr(forge_policies, handler.rsplit(".", 1)[1])))

    def test_schema_properties_match_factory_params(self):
        cfg = params.render(params.load_defaults())["policies"]
        by_handler = {e["handler"]: e for e in forge_policies.POLICY_REGISTRY}
        for entry in cfg.values():
            if entry["handler"] in by_handler:
                props = set(by_handler[entry["handler"]]["params_schema"]["properties"])
                self.assertEqual(props, set(entry["factory_params"]))

    def test_factories_build_evaluators_from_rendered_params(self):
        cfg = params.render(params.load_defaults())["policies"]
        for entry in cfg.values():
            handler = entry["handler"]
            if handler.startswith("forge_policies."):
                fn = getattr(forge_policies, handler.rsplit(".", 1)[1])
                self.assertTrue(callable(fn(**entry["factory_params"])))


if __name__ == "__main__":
    unittest.main()
