import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.performance import recommendations_report, trades_report


def _order(oid, side, price, day, ticker="AAPL", qty=1, status="FILLED"):
    return {"id": oid, "alertId": f"a{oid}" if side == "BUY" else None, "ticker": ticker, "side": side,
            "quantity": qty, "status": status, "fillPrice": price if status == "FILLED" else None,
            "reason": None, "broker": "mock", "strategy": "volume", "stopPrice": None, "targetPrice": None,
            "tradingDate": day, "createdAt": f"{day}T15:00:00+00:00"}


def _newest_first(*orders):
    return sorted(orders, key=lambda o: o["id"], reverse=True)


def test_closed_trade_profit_and_hindsight():
    orders = _newest_first(_order(1, "BUY", 100.0, "2026-11-10", qty=2), _order(2, "SELL", 110.0, "2026-11-12", qty=2))
    bars = {"AAPL": [("2026-11-10", 104.0, 99.0, 103.0), ("2026-11-11", 115.0, 101.0, 112.0),
                     ("2026-11-12", 112.0, 108.0, 110.0), ("2026-11-13", 130.0, 111.0, 128.0)]}
    report = trades_report(orders, {"AAPL": 125.0}, bars)
    [t] = report["trades"]
    assert t == {"orderId": 1, "ticker": "AAPL", "quantity": 2, "buyPrice": 100.0, "buyDate": "2026-11-10",
                 "sellPrice": 110.0, "sellDate": "2026-11-12", "exitPrice": 110.0, "open": False,
                 "pnl": 20.0, "pnlPercent": 10.0, "bestHigh": 115.0, "bestHighDate": "2026-11-11",
                 "bestExitPnl": 30.0, "missedPnl": 10.0, "priceAsOf": None}


def test_open_trade_uses_current_price_and_bars_to_date():
    orders = [_order(2, "BUY", 153.7, "2026-11-17", ticker="NVDA", qty=3)]
    bars = {"NVDA": [("2026-11-14", 170.0, 150.0, 151.0), ("2026-11-17", 155.0, 152.0, 154.0),
                     ("2026-11-18", 158.2, 153.0, 156.67)]}
    [t] = trades_report(orders, {"NVDA": 156.67}, bars)["trades"]
    assert t["open"] is True and t["sellPrice"] is None and t["sellDate"] is None
    assert t["exitPrice"] == 156.67 and t["pnl"] == 8.91 and t["pnlPercent"] == 1.93
    assert t["bestHigh"] == 158.2 and t["bestHighDate"] == "2026-11-18"
    assert t["bestExitPnl"] == 13.5 and t["missedPnl"] == 4.59


def test_same_day_round_trip_uses_one_bar():
    orders = _newest_first(_order(1, "BUY", 100.0, "2026-11-12"), _order(2, "SELL", 101.0, "2026-11-12"))
    bars = {"AAPL": [("2026-11-11", 150.0, 90.0, 100.0), ("2026-11-12", 105.0, 99.0, 101.0),
                     ("2026-11-13", 140.0, 100.0, 120.0)]}
    [t] = trades_report(orders, {}, bars)["trades"]
    assert t["bestHigh"] == 105.0 and t["bestHighDate"] == "2026-11-12"


def test_rebuy_after_close_is_two_trades():
    orders = _newest_first(_order(1, "BUY", 100.0, "2026-11-10"), _order(2, "SELL", 110.0, "2026-11-11"),
                           _order(3, "BUY", 108.0, "2026-11-12"))
    first_and_second = trades_report(orders, {"AAPL": 112.0}, {})["trades"]
    second, first = first_and_second
    assert (first["orderId"], first["open"], first["sellPrice"], first["pnl"]) == (1, False, 110.0, 10.0)
    assert (second["orderId"], second["open"], second["sellPrice"], second["pnl"]) == (3, True, None, 4.0)


def test_failed_and_pending_orders_are_not_trades():
    orders = _newest_first(_order(1, "BUY", 100.0, "2026-11-10", status="FAILED"),
                           _order(2, "BUY", 100.0, "2026-11-10", status="PENDING"),
                           _order(3, "BUY", 100.0, "2026-11-10"),
                           _order(4, "SELL", 120.0, "2026-11-11", status="FAILED"))
    [t] = trades_report(orders, {"AAPL": 101.0}, {})["trades"]
    assert t["orderId"] == 3 and t["open"] is True and t["pnl"] == 1.0


def test_missing_price_and_bars_give_nulls():
    orders = _newest_first(_order(1, "BUY", 50.0, "2026-11-10", ticker="MSFT", qty=2),
                           _order(2, "BUY", 100.0, "2026-11-10"))
    report = trades_report(orders, {"AAPL": None}, {"MSFT": [("2026-11-10", 60.0, 49.0, 55.0)]})
    aapl, msft = report["trades"]
    assert aapl["exitPrice"] is None and aapl["pnl"] is None and aapl["pnlPercent"] is None
    assert aapl["bestHigh"] is None and aapl["bestHighDate"] is None
    assert aapl["bestExitPnl"] is None and aapl["missedPnl"] is None
    # MSFT has bars but no current price: hindsight works, missedPnl needs pnl.
    assert msft["bestHigh"] == 60.0 and msft["bestExitPnl"] == 20.0 and msft["missedPnl"] is None
    totals = report["totals"]
    # An open trade without a price makes the open-trade sums unknown, not partial.
    assert totals["unrealizedPnl"] is None and totals["currentValue"] is None and totals["moneyIn"] == 200.0


def test_totals():
    orders = _newest_first(_order(1, "BUY", 100.0, "2026-11-10", qty=2), _order(2, "SELL", 110.0, "2026-11-11", qty=2),
                           _order(3, "BUY", 50.0, "2026-11-10", ticker="MSFT", qty=3),
                           _order(4, "SELL", 47.5, "2026-11-11", ticker="MSFT", qty=3),
                           _order(5, "BUY", 153.7, "2026-11-17", ticker="NVDA", qty=3))
    totals = trades_report(orders, {"NVDA": 156.67, "AAPL": 999.0, "MSFT": 999.0}, {})["totals"]
    assert totals == {"moneyIn": 461.1, "currentValue": 470.01, "realizedPnl": 12.5, "unrealizedPnl": 8.91,
                      "closedTrades": 2, "winRate": 50.0, "averageWin": 20.0, "averageLoss": -7.5}


def test_break_even_is_neither_win_nor_loss():
    even = _newest_first(_order(1, "BUY", 100.0, "2026-11-10"), _order(2, "SELL", 100.0, "2026-11-11"))
    totals = trades_report(even, {}, {})["totals"]
    assert totals["closedTrades"] == 1 and totals["winRate"] is None
    mixed = _newest_first(_order(1, "BUY", 100.0, "2026-11-10"), _order(2, "SELL", 100.0, "2026-11-11"),
                          _order(3, "BUY", 100.0, "2026-11-10", ticker="MSFT"),
                          _order(4, "SELL", 110.0, "2026-11-11", ticker="MSFT"),
                          _order(5, "BUY", 100.0, "2026-11-10", ticker="NVDA"),
                          _order(6, "SELL", 90.0, "2026-11-11", ticker="NVDA"))
    totals = trades_report(mixed, {}, {})["totals"]
    assert totals["closedTrades"] == 3 and totals["winRate"] == 50.0


def test_price_as_of_is_the_newest_bar_date():
    orders = _newest_first(_order(1, "BUY", 100.0, "2026-11-10"), _order(2, "BUY", 50.0, "2026-11-10", ticker="MSFT"),
                           _order(3, "BUY", 10.0, "2026-11-10", ticker="NVDA"),
                           _order(4, "SELL", 12.0, "2026-11-11", ticker="NVDA"))
    bars = {"AAPL": [("2026-11-10", 104.0, 99.0, 103.0), ("2026-11-13", 106.0, 101.0, 105.0)],
            "NVDA": [("2026-11-10", 11.0, 9.0, 10.0), ("2026-11-13", 13.0, 11.0, 12.5)]}
    nvda, msft, aapl = trades_report(orders, {"AAPL": 105.0, "NVDA": 12.5}, bars)["trades"]
    assert (aapl["priceAsOf"], msft["priceAsOf"], nvda["priceAsOf"]) == ("2026-11-13", None, None)
    alerts = [_alert("a1", "AAPL", "PENDING", "volume", "2026-11-10", 100.0),
              _alert("a2", "MSFT", "PENDING", "volume", "2026-11-10", 50.0)]
    items = recommendations_report(alerts, {"AAPL": 105.0}, bars)["items"]
    assert {i["ticker"]: i["priceAsOf"] for i in items} == {"AAPL": "2026-11-13", "MSFT": None}


def test_no_trades_gives_zero_totals_and_null_rates():
    assert trades_report([], {}, {}) == {
        "totals": {"moneyIn": 0.0, "currentValue": 0.0, "realizedPnl": 0.0, "unrealizedPnl": 0.0,
                   "closedTrades": 0, "winRate": None, "averageWin": None, "averageLoss": None},
        "trades": [],
    }


def test_totals_round_once():
    orders = []
    for i in range(3):
        orders += [_order(2 * i + 1, "BUY", 100.0, "2026-11-10"), _order(2 * i + 2, "SELL", 100.004, "2026-11-11")]
    report = trades_report(_newest_first(*orders), {}, {})
    assert [t["pnl"] for t in report["trades"]] == [0.0, 0.0, 0.0]
    assert report["totals"]["realizedPnl"] == 0.01  # round(sum of unrounded 0.004s), checked in Python


def _alert(aid, ticker, status, strategy, day, price):
    return {"alertId": aid, "ticker": ticker, "status": status, "strategy": strategy,
            "tradingDate": day, "priceAtAlert": price}


def test_recommendations_items_and_groups():
    alerts = [_alert("a1", "AAPL", "APPROVED", "volume", "2026-11-10", 100.0),
              _alert("a2", "MSFT", "REJECTED", "volume", "2026-11-12", 200.0),
              _alert("a3", "NVDA", "PENDING", "macd", "2026-11-11", 150.0)]
    bars = {"AAPL": [("2026-11-07", 200.0, 50.0, 100.0), ("2026-11-10", 105.0, 98.0, 104.0),
                     ("2026-11-11", 112.0, 103.0, 110.0)],
            "MSFT": [("2026-11-12", 201.0, 195.0, 196.0), ("2026-11-13", 199.0, 188.5, 190.0)]}
    report = recommendations_report(alerts, {"AAPL": 110.0, "MSFT": 190.0, "NVDA": None}, bars)
    items = report["items"]
    assert [i["alertId"] for i in items] == ["a2", "a3", "a1"]
    assert items[2] == {"alertId": "a1", "ticker": "AAPL", "strategy": "volume", "decision": "APPROVED",
                        "tradingDate": "2026-11-10", "priceAtAlert": 100.0, "priceNow": 110.0,
                        "changePercent": 10.0, "bestPrice": 112.0, "bestDate": "2026-11-11",
                        "worstPrice": 98.0, "worstDate": "2026-11-10",
                        "priceAsOf": "2026-11-11"}
    assert (items[0]["changePercent"], items[0]["bestPrice"], items[0]["worstPrice"], items[0]["worstDate"]) == \
        (-5.0, 201.0, 188.5, "2026-11-13")
    assert items[1]["priceNow"] is None and items[1]["changePercent"] is None
    assert items[1]["bestPrice"] is None and items[1]["worstDate"] is None
    assert report["byDecision"] == [{"group": "APPROVED", "count": 1, "averageChangePercent": 10.0},
                                    {"group": "PENDING", "count": 1, "averageChangePercent": None},
                                    {"group": "REJECTED", "count": 1, "averageChangePercent": -5.0}]
    assert report["byStrategy"] == [{"group": "macd", "count": 1, "averageChangePercent": None},
                                    {"group": "volume", "count": 2, "averageChangePercent": 2.5}]


def test_alerts_without_price_or_date_are_left_out():
    alerts = [_alert("a1", "AAPL", "APPROVED", "volume", "2026-11-10", 100.0),
              {"alertId": "old", "ticker": "AAPL", "status": "APPROVED", "strategy": "volume"},
              _alert("nodate", "AAPL", "APPROVED", "volume", None, 100.0),
              _alert("noprice", "AAPL", "APPROVED", "volume", "2026-11-10", None)]
    report = recommendations_report(alerts, {"AAPL": 101.0}, {})
    assert [i["alertId"] for i in report["items"]] == ["a1"]
    assert report["byDecision"] == [{"group": "APPROVED", "count": 1, "averageChangePercent": 1.0}]
