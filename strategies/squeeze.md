# Volatility squeeze (version 1)

Long only. Looks for a quiet spell that ends with a wide up day, on completed daily bars.

Checks (points): Bollinger bandwidth at or below its 20th percentile on one of the last 10 days (30); today's true range at least 1.5x ATR(14) (25); close in the top quarter of today's range and above the open (20); ATR % below its 80th percentile (10); close above the 50-day average (15).

BUY when the squeeze, wide day and strong close checks pass. Stop: today's low. Target: twice the risk. Invalidation: a close below the stop. Time exit: 20 trading days.

Judge the trade on what caused the wide day (news, earnings) and whether it can follow through.
