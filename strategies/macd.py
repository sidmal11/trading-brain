from strategies.indicators import checklist, macd_buy


def evaluate(ticker, bars, volume_score, threshold):
    if volume_score is None:
        ok, detail = False, "No volume reading"
    else:
        ok = volume_score >= threshold
        detail = f"Volume {volume_score:.2f}x the average (threshold {threshold:.1f}x)"
    result = checklist(bars, None, ok, detail)
    if not macd_buy(result):
        return None
    return {"strategy": "macd", "signalType": "MACD_CROSSOVER", **result}
