# Trend following (version 1)

Long only. Looks for an established uptrend with strength behind it, on completed daily bars.

Checks (points): close above the 50-day average and the 50-day above the 200-day (25); the 50-day rising over 20 days (15); 20-day EMA above the 50-day EMA (15); ADX(14) at least 25 with +DI above -DI (25); swing structure up, a higher high and a higher low (20).

BUY when the averages, ADX and structure checks pass. A request is raised only on the day the setup first appears. Stop: the last confirmed swing low, or 2 ATR below entry when that is missing or within 1 ATR. Target: twice the risk. Invalidation: a close below the stop. Time exit: 20 trading days.

Judge the trade on trend quality and on what could break it: earnings, an extended move far above the 50-day average, a market in a bear trend.
