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
