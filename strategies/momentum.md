# Momentum (version 1)

Long only. Looks for momentum that is positive, accelerating and stronger than the market, without being stretched, on completed daily bars.

Checks (points): RSI(14) between 55 and 70 (20); MACD histogram above 0 and higher than the day before (25); 20-day return above 0 and above SPY's (25); Stochastic RSI above 50 and rising (15); RSI higher than 5 days ago (15).

BUY when the MACD and relative-return checks pass plus at least one other. A request is raised only on the day the setup first appears. Stop: 1.5 ATR below entry. Target: twice the risk. Invalidation: a close below the stop. Time exit: 20 trading days.

Judge the trade on whether the move is broad or one news spike, how far it is above its averages, and earnings.
