import re
from pathlib import Path

CAP = 6000
SECTIONS = {"macd": ("3.1", "6.", "7.", "8.", "12.")}  # long setup, entry/exit, risk, chop, constraints


def _sections(text, prefixes):
    """The markdown sections whose headings start with one of `prefixes`, in file order. A section runs
    to the next heading of the same or higher level."""
    lines = text.splitlines(keepends=True)
    out, i = [], 0
    while i < len(lines):
        m = re.match(r"(#+)\s+(.*)", lines[i])
        if not (m and m.group(2).startswith(prefixes)):
            i += 1
            continue
        level, j = len(m.group(1)), i + 1
        while j < len(lines) and not ((h := re.match(r"(#+)\s", lines[j])) and len(h.group(1)) <= level):
            j += 1
        out.append("".join(lines[i:j]))
        i = j
    return "".join(out)


def strategy_notes(name):
    """The strategy's written notes (strategies/<name>.md) for the LLM prompt, capped; "" when none.
    Only the listed sections when found; otherwise from the first `# ` heading (the text before it
    is a chat preamble, not strategy)."""
    try:
        text = (Path(__file__).parent / f"{name}.md").read_text(encoding="utf-8")
    except OSError:
        return ""
    picked = _sections(text, SECTIONS.get(name, ()))
    if picked:
        return picked[:CAP]
    start = 0 if text.startswith("# ") else text.find("\n# ") + 1
    return text[start:][:CAP]
