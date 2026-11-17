from strategies.indicators import checklist


def evaluate(ticker, bars, scan_result, threshold):
    score = scan_result["volume_score"]
    if score < threshold:
        return None
    detail = f"Volume {score:.2f}x the average (threshold {threshold:.1f}x)"
    return {"strategy": "volume", "signalType": "VOLUME_BREAKTHROUGH",
            **checklist(bars, scan_result.get("price"), True, detail)}  # no live price: last close
