import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils import news_client  # noqa: E402

PAYLOAD = {
    "data": {
        "data": [
            {"news_id": "N2", "title": "NVDA beats", "source": "Wire", "publish_time": 1794790000000,
             "tag_list": [{"code": "NVDA"}, {"code": "AMD"}]},
            {"news_id": "N1", "title": "Other", "publish_time": 1794780000000, "tag_list": [{"code": "XYZ"}]},
            {"news_id": "N0", "title": None, "publish_time": 1, "tag_list": [{"code": "NVDA"}]},
        ]
    }
}


def test_parse_groups_headlines_under_each_wanted_ticker():
    news = news_client._parse(PAYLOAD, ["NVDA", "AMD"])
    assert set(news) == {"NVDA", "AMD"}
    assert news["NVDA"] == [
        {"id": "N2", "title": "NVDA beats", "source": "Wire", "publishedAt": "2026-11-16T00:46:40+00:00"}
    ]


def test_no_key_means_no_news_and_no_network(monkeypatch):
    monkeypatch.setattr(news_client.Config, "AINVEST_API_KEY", "")
    monkeypatch.setattr(news_client.requests, "get", lambda *a, **k: 1 / 0)
    assert news_client.latest_by_ticker(["NVDA"]) == {}


def test_failure_serves_nothing_rather_than_raising(monkeypatch):
    monkeypatch.setattr(news_client.Config, "AINVEST_API_KEY", "k")

    def boom(*a, **k):
        raise news_client.requests.ConnectionError()

    monkeypatch.setattr(news_client.requests, "get", boom)
    assert news_client.latest_by_ticker(["ZZZZ"]) == {}
