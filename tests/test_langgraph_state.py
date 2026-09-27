import unittest
from utils.langgraph_state import TradingBrainState, fetch_screener_data, fetch_intelligence

class TestLangGraphPipeline(unittest.TestCase):
    def test_fetch_screener(self):
        state = TradingBrainState()
        fetch_screener_data(state)
        self.assertIn("technical_data", state)
        self.assertEqual(state["technical_data"]["signal"], "breakout")

    def test_fetch_intelligence(self):
        state = TradingBrainState()
        fetch_intelligence(state)
        self.assertIn("external_context", state)

if __name__ == '__main__':
    unittest.main()
