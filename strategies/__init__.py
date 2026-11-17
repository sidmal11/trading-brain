from pathlib import Path


def strategy_notes(name):
    """The strategy's written notes (strategies/<name>.md), capped for the LLM prompt; "" when none."""
    try:
        return (Path(__file__).parent / f"{name}.md").read_text(encoding="utf-8")[:6000]
    except OSError:
        return ""
