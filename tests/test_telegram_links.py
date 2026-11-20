import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import Config  # noqa: E402
from utils.telegram_links import TelegramLinks, owner_aware_email  # noqa: E402


def test_code_is_single_use_and_expires(tmp_path):
    l = TelegramLinks(str(tmp_path / "m.db"))
    code = l.new_code("a@x.com", now=1000)
    assert l.redeem(code, "42", now=1599) == "a@x.com"
    assert l.redeem(code, "43", now=1500) is None            # used, inside the window
    late = l.new_code("a@x.com", now=1000)
    assert l.redeem(late, "44", now=1601) is None            # expired after 600 s
    assert l.email_for_chat("42") == "a@x.com" and l.email_for_chat("44") is None

def test_unknown_code_and_exact_expiry_boundary(tmp_path):
    l = TelegramLinks(str(tmp_path / "m.db"))
    assert l.redeem("no-such-code", "42", now=1000) is None
    edge = l.new_code("a@x.com", now=1000)
    assert l.redeem(edge, "42", now=1600) is None            # expires at exactly 1600
    assert l.redeem(l.new_code("a@x.com", now=1000), "42", now=1599) == "a@x.com"

def test_single_use_holds_across_instances(tmp_path):
    path = str(tmp_path / "m.db")
    code = TelegramLinks(path).new_code("a@x.com")
    # Separate instances, as the factory makes per call: the instance lock cannot be what keeps this single use.
    results = [TelegramLinks(path).redeem(code, str(chat)) for chat in (1, 2)]
    assert sorted(r is None for r in results) == [False, True]

def test_concurrent_redeems_of_one_code_yield_one_winner(tmp_path):
    import threading
    path = str(tmp_path / "m.db")
    TelegramLinks(path)  # create tables once
    for _ in range(5):
        code = TelegramLinks(path).new_code("a@x.com")
        barrier = threading.Barrier(8)
        results = []
        def go(chat):
            barrier.wait()
            results.append(TelegramLinks(path).redeem(code, str(chat)))
        threads = [threading.Thread(target=go, args=(c,)) for c in range(8)]
        for t in threads: t.start()
        for t in threads: t.join()
        assert sum(r is not None for r in results) == 1

def test_relinking_moves_the_chat_and_unlink_removes_it(tmp_path):
    l = TelegramLinks(str(tmp_path / "m.db"))
    l.redeem(l.new_code("a@x.com"), "42"); l.redeem(l.new_code("b@x.com"), "42")
    assert l.email_for_chat("42") == "b@x.com" and l.chats_for_email("a@x.com") == []
    l.unlink_email("b@x.com"); assert l.email_for_chat("42") is None

def test_admin_chats_are_the_owner(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "OWNER_EMAIL", "o@x.com")
    assert owner_aware_email(TelegramLinks(str(tmp_path / "m.db")), "7", {"7"}) == "o@x.com"
