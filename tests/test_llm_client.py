from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from config import Config
from utils.llm_client import LlmClient

FALLBACK = "Unable to generate AI analysis at this time."


def _client(content):
    with patch("utils.llm_client.Groq") as groq:
        create = groq.return_value.chat.completions.create
        create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
        )
        return LlmClient(), create


def test_sends_configured_model_and_budget():
    llm, create = _client("Pros: x")
    assert llm.analyze_stock("AAPL", "s", {}) == "Pros: x"
    kwargs = create.call_args.kwargs
    assert kwargs["model"] == Config.GROQ_MODEL == "openai/gpt-oss-120b"
    assert kwargs["max_tokens"] == 1024
    assert kwargs["reasoning_effort"] == "low"


def test_empty_reply_gives_fallback():
    for empty in (None, "", "   "):
        llm, _ = _client(empty)
        assert llm.analyze_stock("AAPL", "s", {}) == FALLBACK


def test_asks_for_the_fixed_plain_pros_cons_verdict_format():
    # Every card parses the same way (api/market_source.split_pros_cons) when every reply has the same shape.
    llm, create = _client("Pros:\n- a: b\nCons:\n- c: d\nVerdict: e")
    llm.analyze_stock("AAPL", "s", {})
    prompt = create.call_args.kwargs["messages"][1]["content"]
    assert "no markdown" in prompt
    assert "\nPros:\n- " in prompt and "\nCons:\n- " in prompt and "Verdict:" in prompt
