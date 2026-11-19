"""One scored, gated, explained opportunity per ticker per day, and its store."""
import json
import sqlite3
import threading
from datetime import date

from features.build import FEATURE_VERSION
from market_data.earnings import earnings_note
from risk.assess import assess
from scoring import components as c
from scoring.aggregate import aggregate
from scoring.explain import explain, gates
from scoring.profiles import LONG_TERM, LONG_TERM_NOTE, SCORE_VERSION, SWING


def build_opportunity(ticker, bars, features, results, regime, quality, earnings_day, today: date,
                      max_holds=None, fundamentals=None, reliability=None) -> dict:
    """`results`: {strategy: latest result}; `quality`: data-quality status; `features` from features.build."""
    risk = assess(bars, results, regime, max_holds)
    comps = {
        "trend": c.strategy_component(results.get("trend")),
        "breakout": c.strategy_component(results.get("breakout")),
        "momentum": c.momentum(features), "price_action": c.price_action(features), "volume": c.volume(features),
        "relative_strength": c.relative_strength(features), "regime": c.regime(regime),
        "risk_reward": c.risk_reward(risk["riskReward"]),
        "alignment": c.alignment(features),
        "reliability": _plan_reliability(risk["planSource"], reliability or {}),
    }
    agg = aggregate(comps, SWING, quality)
    stored = fundamentals or {}
    long_term = aggregate({**comps, "fundamental": c.fundamental(stored.get("values"))}, LONG_TERM, quality)
    failed = gates(risk, agg["coverage"], quality, bars[-1].date if bars else None, today)
    note = earnings_note(earnings_day, today)
    warnings = [note] if note else []
    return {
        "id": f"{ticker}-{features.get('asOf')}", "ticker": ticker, "asOf": features.get("asOf"),
        "qualityScore": agg["score"], "direction": agg["direction"], "bullishScore": agg["score"],
        "bearishScore": None, "calibratedProbability": None,  # bearish not scored yet (Q4); no calibration until R9
        "coverage": agg["coverage"], "eligible": not failed, "breakdown": agg["breakdown"],
        "strategies": {k: {"direction": r["direction"], "score": r["confidence"], "version": r.get("version"),
                           "checklist": r.get("checklist", [])}
                       for k, r in results.items() if r},
        "risk": risk, "dataQuality": quality, "timeframes": features.get("timeframes"),
        "reliability": {"strategy": risk["planSource"], "score": comps["reliability"][0],
                        "detail": comps["reliability"][1]},
        "longTerm": {"score": long_term["score"], "coverage": long_term["coverage"], "breakdown": long_term["breakdown"],
                     "metrics": c.fundamental_metrics(stored.get("values")) if stored else [],
                     "fetchedAt": stored.get("fetchedAt"), "note": LONG_TERM_NOTE}, "earningsDate": earnings_day.isoformat() if earnings_day else None,
        "explanation": explain(agg, results, risk, failed, warnings, comps),
        "versions": {"score": SCORE_VERSION, "features": FEATURE_VERSION,
                     "strategies": {k: r.get("version") for k, r in results.items() if r}, "model": None},
    }


def _plan_reliability(source, by_strategy):
    """The reliability of the strategy whose plan is used; none for the default plan (weight 0 for now)."""
    r = by_strategy.get(source)
    if not r:
        return None, "no strategy plan" if source in (None, "default") else "no backtest yet"
    return r["score"], r["detail"]


def rank(opportunities) -> list:
    """Eligible first, by score; then the rest by score. A missing score sorts last."""
    return sorted(opportunities, key=lambda o: (not o["eligible"], -(o["qualityScore"] if o["qualityScore"] is not None else -1)))


class OpportunityStore:
    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()
        with self._connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS opportunities (ticker TEXT NOT NULL, date TEXT NOT NULL, "
                         "score_version TEXT NOT NULL, doc TEXT NOT NULL, PRIMARY KEY (ticker, date, score_version))")

    def _connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def save(self, rows):
        with self._lock, self._connect() as conn:
            conn.executemany("INSERT OR REPLACE INTO opportunities VALUES (?, ?, ?, ?)",
                             [(o["ticker"], o["asOf"], SCORE_VERSION, json.dumps(o)) for o in rows])

    def latest(self, tickers=None) -> list:
        """Ranked opportunities: the newest day's, or with `tickers` (the universe) each ticker's newest.

        With `tickers`, a ticker whose bars stopped updating still shows: the daily pass rebuilds it every day with
        its "Stale data" gate, filed under its last bar's date, so the newest day alone would hide it.
        """
        with self._connect() as conn:
            if tickers is None:
                day = conn.execute("SELECT MAX(date) FROM opportunities WHERE score_version = ?",
                                   (SCORE_VERSION,)).fetchone()[0]
                rows = conn.execute("SELECT doc FROM opportunities WHERE date = ? AND score_version = ?",
                                    (day, SCORE_VERSION)).fetchall() if day else []
            elif not tickers:
                rows = []
            else:
                marks = ",".join("?" * len(tickers))
                rows = conn.execute(
                    f"SELECT o.doc FROM opportunities o WHERE o.score_version = ? AND o.ticker IN ({marks}) AND o.date = "
                    "(SELECT MAX(date) FROM opportunities WHERE ticker = o.ticker AND score_version = ?)",
                    (SCORE_VERSION, *tickers, SCORE_VERSION)).fetchall()
        return rank([json.loads(r[0]) for r in rows])

    def latest_for(self, ticker) -> dict | None:
        """The ticker's newest opportunity, or None."""
        with self._connect() as conn:
            row = conn.execute("SELECT doc FROM opportunities WHERE ticker = ? AND score_version = ? ORDER BY date DESC "
                               "LIMIT 1", (ticker, SCORE_VERSION)).fetchone()
        return json.loads(row[0]) if row else None

    def get(self, opportunity_id) -> dict | None:
        ticker, day = opportunity_id[:-11], opportunity_id[-10:]  # "<TICKER>-YYYY-MM-DD"; tickers can hold dashes
        with self._connect() as conn:
            row = conn.execute("SELECT doc FROM opportunities WHERE ticker = ? AND date = ? AND score_version = ?",
                               (ticker, day, SCORE_VERSION)).fetchone()
        return json.loads(row[0]) if row else None
