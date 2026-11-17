from pathlib import Path


def strategy_notes(name):
    """The strategy's written notes (strategies/<name>.md) from the first `# ` heading, capped for the
    LLM prompt; "" when none. The text before that heading is a chat preamble, not strategy."""
    try:
        text = (Path(__file__).parent / f"{name}.md").read_text(encoding="utf-8")
    except OSError:
        return ""
    start = 0 if text.startswith("# ") else text.find("\n# ") + 1
    return text[start:][:6000]
