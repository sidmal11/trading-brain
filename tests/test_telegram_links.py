import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import Config  # noqa: E402
from utils.telegram_links import TelegramLinks, owner_aware_email  # noqa: E402


def test_code_is_single_use_and_expires(tmp_path):
    l = TelegramLinks(str(tmp_path / "m.db"))
    code = l.new_code("a@x.com", now=1000)
    assert l.redeem(code, "42", now=1599) == "a@x.com"
    assert l.redeem(code, "43", now=1600) is None            # used
    late = l.new_code("a@x.com", now=1000)
    assert l.redeem(late, "44", now=1601) is None            # expired after 600 s
    assert l.email_for_chat("42") == "a@x.com" and l.email_for_chat("44") is None

def test_relinking_moves_the_chat_and_unlink_removes_it(tmp_path):
    l = TelegramLinks(str(tmp_path / "m.db"))
    l.redeem(l.new_code("a@x.com"), "42"); l.redeem(l.new_code("b@x.com"), "42")
    assert l.email_for_chat("42") == "b@x.com" and l.chats_for_email("a@x.com") == []
    l.unlink_email("b@x.com"); assert l.email_for_chat("42") is None

def test_admin_chats_are_the_owner(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "OWNER_EMAIL", "o@x.com")
    assert owner_aware_email(TelegramLinks(str(tmp_path / "m.db")), "7", {"7"}) == "o@x.com"
