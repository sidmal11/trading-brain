import importlib
import os
import unittest

import config


class TestFeatureFlags(unittest.TestCase):
    def test_flags_default_to_off(self):
        self.assertFalse(config.Config.FEATURE_MULTI_AGENT_MODE)
        self.assertFalse(config.Config.FEATURE_UI_DASHBOARD)

    def test_flag_reads_environment(self):
        os.environ["FEATURE_MULTI_AGENT_MODE"] = "true"
        try:
            importlib.reload(config)
            self.assertTrue(config.Config.FEATURE_MULTI_AGENT_MODE)
        finally:
            os.environ.pop("FEATURE_MULTI_AGENT_MODE", None)
            importlib.reload(config)

    def test_flags_live_on_the_canonical_config(self):
        # Guards the config/ package that shadowed config.py and made the
        # flags unreachable: the canonical module must stay a module.
        self.assertFalse(hasattr(config, "__path__"))
        self.assertTrue(hasattr(config.Config, "FEATURE_MULTI_AGENT_MODE"))

    def test_multi_agent_workflow_runs(self):
        from graph import run_workflow
        from utils.langgraph_state import TradingBrainState

        result = run_workflow(TradingBrainState())
        self.assertIn("technical_data", result)
        self.assertIn("external_context", result)


if __name__ == "__main__":
    unittest.main()
