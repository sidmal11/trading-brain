# Breakout (version 1)

Long only. Looks for a closing break of the 20-day high on strong volume after a quiet spell, without chasing. Intraday moves never count.

Checks (points): close above the highest high of the previous 20 days (30); relative volume at least 1.5 (25); Bollinger bandwidth at or below its 30th percentile on one of the 5 days before (15); close at most 3% above the broken level (15); market trend not bear (15).

BUY when the break, volume and not-extended checks pass. Stop: the broken level minus half an ATR. Target: twice the risk. Invalidation: a close back below the stop. Time exit: 20 trading days. Many breakouts fail: watch for a close back under the level in the next 3 days.
