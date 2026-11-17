I've consolidated both strategies into one structured, AI-agent-ready trading strategy document. I've also corrected a few technical inaccuracies and reconciled the overlapping rules so your agent has a consistent trading methodology.

The document includes:

- MACD fundamentals and standard settings.
- 200 EMA/SMA trend filtering.
- Entry rules and crossover confirmation.
- Support/resistance and breakout validation.
- First and second pullback strategies.
- Exit signals and profit targets.
- Stop-loss and risk management.
- Sideways-market and false-breakout filters.
- Trading psychology and overtrading prevention.
- A step-by-step decision process your AI agent can follow.

# MACD-Based Trading Strategy

## Trend, Momentum, Price Action & Risk Management Framework

**Purpose:** Provide a systematic trading methodology for identifying high-quality trade opportunities, avoiding false breakouts, confirming momentum, and managing risk.

**Strategy type:** Trend-following and momentum-based trading\
**Primary indicator:** MACD (Moving Average Convergence Divergence)\
**Trend filter:** 200-period Moving Average\
**Additional confirmation:** Support, resistance, price action, and breakout structure\
**Applicable markets:** Stocks, ETFs, Forex, Futures, and Cryptocurrency

---

# 1. Strategy Overview

This strategy combines three primary components:

1. **Trend identification:** Use the 200-period moving average to determine the dominant market direction.
2. **Momentum confirmation:** Use MACD crossovers and the zero line to identify momentum shifts.
3. **Price action confirmation:** Use support, resistance, breakout levels, and pullbacks to validate trade entries.

The objective is to trade in the direction of the dominant trend while entering during favorable momentum conditions.

The strategy emphasizes:

- Trading with the prevailing trend.
- Entering during strong initial moves and early pullbacks.
- Avoiding weak breakouts and sideways markets.
- Exiting when momentum deteriorates.
- Maintaining consistent risk management.
- Avoiding unnecessary trades.

**Important:** No combination of indicators guarantees a high win rate. Strategy performance must be validated through historical backtesting and forward testing.

# 2. Technical Indicators and Settings

## 2.1 MACD

Use standard MACD settings:

| Parameter  | Setting       |
| ---------- | ------------- |
| Fast EMA   | 12            |
| Slow EMA   | 26            |
| Signal EMA | 9             |
| Source     | Closing Price |

MACD consists of three primary components:

**MACD Line**

Difference between the 12-period EMA and 26-period EMA.

MACD = EMA(12) - EMA(26)

**Signal Line**

9-period EMA of the MACD line.

**Histogram**

Difference between MACD and Signal Line.

Histogram = MACD Line - Signal Line

### MACD Interpretation

| Condition                 | Interpretation           |
| ------------------------- | ------------------------ |
| MACD above Signal Line    | Bullish momentum         |
| MACD below Signal Line    | Bearish momentum         |
| MACD crosses above Signal | Bullish momentum shift   |
| MACD crosses below Signal | Bearish momentum shift   |
| MACD above Zero Line      | Positive momentum regime |
| MACD below Zero Line      | Negative momentum regime |
| Histogram expanding       | Momentum strengthening   |
| Histogram contracting     | Momentum weakening       |

### Important Considerations

- A bullish crossover below the zero line can indicate an early bullish momentum reversal.
- A bearish crossover above the zero line can indicate an early bearish momentum reversal.
- Crossovers occurring near the zero line can be useful transition signals.
- Crossovers far from the zero line can also be valid, but should be evaluated in the context of the prevailing trend.
- MACD is a lagging indicator and should not be treated as a standalone entry signal.

Keep standard MACD settings consistent rather than frequently optimizing parameters.

## 2.2 200-Period Moving Average

Use a 200-period moving average as the primary trend filter.

| Price Position                   | Market Bias        | Trading Direction       |
| -------------------------------- | ------------------ | ----------------------- |
| Price above 200 MA               | Bullish            | Prefer long positions   |
| Price below 200 MA               | Bearish            | Prefer short positions  |
| Price repeatedly crossing 200 MA | Unclear / sideways | Avoid or reduce trading |

The 200 MA identifies the broader trend and prevents taking trades against the prevailing market direction.

**Timeframe clarification:** On daily charts, this is the 200-day moving average. On intraday charts, it is the 200-period moving average of that chart's timeframe.

## 2.3 Additional Moving Averages

Optional moving averages for short-term trend analysis:

- 9 EMA: Short-term momentum.
- 20 EMA: Short-term trend direction.
- 200 MA: Long-term trend direction.

A bullish 9 EMA / 20 EMA crossover provides additional confirmation of short-term strength.

These are secondary indicators and should not override the primary MACD and 200 MA rules.

# 3. Primary Trading Rules

## 3.1 Long (Buy) Setup

A long trade should satisfy the following conditions:

**Trend Confirmation**

- Price is above the 200 MA.
- Market structure shows higher highs and higher lows, preferably.
- Avoid entering when the 200 MA is flat and price is repeatedly crossing it.

**MACD Confirmation**

- MACD crosses above the Signal Line.
- Prefer crossovers below or near the zero line for early momentum transitions.
- MACD should ideally begin moving toward or above the zero line.
- Histogram should turn positive and preferably expand.

**Price Action Confirmation**

- Price approaches a recognized support level or completes a valid bullish breakout.
- Look for bullish rejection, a strong closing candle, or confirmation that resistance has become support.
- Avoid chasing an extended candle far above its breakout level.

**Entry**

- Enter after the confirmation candle closes.
- Alternatively, enter on a confirmed retest of the breakout level.
- Do not enter solely because MACD crosses.

## 3.2 Short (Sell) Setup

A short trade should satisfy the following conditions:

**Trend Confirmation**

- Price is below the 200 MA.
- Market structure preferably shows lower highs and lower lows.
- Avoid trading when the 200 MA is flat and price is moving sideways around it.

**MACD Confirmation**

- MACD crosses below the Signal Line.
- Prefer crossovers above or near the zero line for early bearish momentum transitions.
- MACD should ideally move toward or below the zero line.
- Histogram should turn negative and preferably expand.

**Price Action Confirmation**

- Price approaches a recognized resistance level or completes a valid bearish breakdown.
- Look for bearish rejection or confirmation that support has become resistance.

**Entry**

- Enter after the confirmation candle closes.
- Alternatively, enter on a confirmed retest of the breakdown level.

# 4. Avoiding False Breakouts

False breakouts occur when price temporarily moves beyond a support or resistance level but fails to sustain the move.

Use MACD as a momentum filter.

## Bullish Breakout Validation

Before entering a bullish breakout:

1. Price breaks above established resistance.
2. Candle closes above resistance.
3. MACD is above its Signal Line.
4. MACD histogram is positive and preferably expanding.
5. Price is above the 200 MA.
6. The breakout is supported by a strong price structure.

A breakout with weakening MACD momentum should be treated cautiously.

## Bearish Breakdown Validation

Before entering a bearish breakdown:

1. Price breaks below established support.
2. Candle closes below support.
3. MACD is below its Signal Line.
4. MACD histogram is negative and preferably expanding.
5. Price is below the 200 MA.
6. The breakdown is supported by bearish price structure.

Avoid entering when MACD is moving against the breakout direction.

**Key principle:** Do not chase price movement. Wait for price action and momentum to confirm that the breakout is sustainable.

# 5. Trading the First and Second Pullbacks

One of the most important concepts is focusing on the initial strong move rather than trading repeatedly throughout an extended trend.

## First Pullback

During a bullish trend:

1. Price establishes a strong upward move.
2. Price temporarily pulls back toward support or the 9/20 EMA.
3. MACD remains bullish or begins recovering.
4. Price forms a bullish reversal or continuation candle.
5. Enter after confirmation.

For bearish trends, apply the opposite conditions.

## Second Pullback

The second pullback may offer another continuation opportunity if:

- The broader trend remains intact.
- Price structure has not broken down.
- MACD continues supporting the trend.
- Price finds support or resistance at a meaningful level.
- A new momentum confirmation appears.

Avoid repeatedly entering after the second pullback without a fresh structural setup.

## Momentum Deterioration

Be cautious when:

- MACD approaches a bearish crossover during a bullish trade.
- Histogram bars progressively shrink.
- Price struggles to establish new highs.
- Price begins consolidating near resistance.

These conditions indicate weakening momentum and reduced trade quality.

# 6. Entry and Exit Rules

## Entry Checklist: Long

A long trade is valid when the following conditions align:

- Price above 200 MA.
- MACD above Signal Line or a confirmed bullish crossover.
- MACD preferably below or near zero for an early reversal setup.
- Positive or improving histogram.
- Price at support, a confirmed breakout, or a valid pullback.
- Bullish price action confirmation.
- Stop-loss location identified.
- Reward-to-risk ratio is acceptable.

## Entry Checklist: Short

- Price below 200 MA.
- MACD below Signal Line or a confirmed bearish crossover.
- MACD preferably above or near zero for an early reversal setup.
- Negative or deteriorating histogram.
- Price at resistance, a confirmed breakdown, or a valid pullback.
- Bearish price action confirmation.
- Stop-loss location identified.
- Reward-to-risk ratio is acceptable.

## Exit Rules

Exit or reduce a long position when:

- MACD crosses below the Signal Line.
- Momentum deteriorates significantly.
- Price breaks important support.
- The predefined stop-loss is reached.
- The planned profit target is reached.

Exit or reduce a short position when:

- MACD crosses above the Signal Line.
- Bearish momentum deteriorates.
- Price breaks important resistance.
- The predefined stop-loss is reached.
- The planned profit target is reached.

A MACD crossover is a warning or exit confirmation, not a reason to ignore an existing stop-loss.

Avoid waiting for a crossover if price has already invalidated the trade.

# 7. Risk Management

Risk management is mandatory for every trade.

## Stop-Loss Placement

For long positions:

- Place the stop below a meaningful swing low, support level, or breakout retest low.

For short positions:

- Place the stop above a meaningful swing high, resistance level, or breakdown retest high.

The 200 MA can serve as a broader trend invalidation level, but should not automatically be used as the stop-loss for every trade. Its distance from the entry may create excessive risk.

## Profit Target

Initial reference:

**Minimum reward-to-risk ratio: 1.5:1**

Example:

| Parameter        | Amount |
| ---------------- | ------ |
| Entry            | $100   |
| Stop Loss        | $98    |
| Risk             | $2     |
| Profit Target    | $103   |
| Potential Reward | $3     |
| Reward/Risk      | 1.5:1  |

Consider taking partial profits at predefined levels and trailing the remaining position when momentum remains strong.

## Position Sizing

Determine position size based on:

- Account equity.
- Maximum permitted risk per trade.
- Distance between entry and stop-loss.

Position size = Maximum Dollar Risk / Risk Per Share

Do not increase position size simply because MACD signals appear particularly strong.

Avoid excessive leverage.

# 8. Avoiding Sideways and Congested Markets

MACD performs best when the market establishes directional movement.

It produces more false signals during consolidation.

Recognize sideways conditions when:

- The 200 MA is relatively flat.
- Price repeatedly crosses the 200 MA.
- MACD frequently crosses its Signal Line.
- Histogram alternates between positive and negative.
- Price repeatedly bounces between horizontal support and resistance.
- Breakouts fail to sustain momentum.

**Action:** Avoid trading until a clear trend or confirmed range breakout develops.

If trading within a range, require particularly strong support/resistance confirmation and use range-specific risk management rather than blindly applying trend-following entries.

# 9. Trading Discipline and Psychology

The objective is not to trade every market movement.

Focus on quality opportunities.

### Front-Side Trading

Prioritize:

- Initial strong breakout.
- First pullback.
- Second pullback, provided momentum remains intact.

Avoid chasing extended moves where momentum is already fading.

### Knowing When to Stop

- Stop trading when market conditions become unfavorable.
- Avoid revenge trading after losses.
- Do not force trades to achieve a daily profit target.
- Reduce trading frequency during low-momentum periods.
- Preserve capital and mental discipline.

The goal is consistent execution over many trading sessions, not maximizing the number of trades each day.

# 10. AI Trading Agent Decision Framework

The AI agent should follow this process for every potential trade.

## Step 1: Determine Market Direction

Evaluate:

- Current price relative to 200 MA.
- 200 MA slope.
- Overall price structure.
- Whether the market is trending or consolidating.

Determine one of:

- BULLISH
- BEARISH
- SIDEWAYS
- UNCERTAIN

If SIDEWAYS or UNCERTAIN, avoid trend-following entries.

## Step 2: Evaluate MACD

Analyze:

- MACD line value.
- Signal line value.
- Histogram value.
- MACD zero-line position.
- Recent crossover direction.
- Histogram expansion or contraction.

Determine momentum:

- STRONG_BULLISH
- WEAK_BULLISH
- NEUTRAL
- WEAK_BEARISH
- STRONG_BEARISH

## Step 3: Analyze Price Action

Identify:

- Support levels.
- Resistance levels.
- Recent swing highs and lows.
- Breakout or breakdown levels.
- Pullback opportunities.
- Candle closing confirmation.

Determine whether price action supports the proposed trade.

## Step 4: Validate the Setup

For a LONG:

- Bullish market bias.
- Bullish MACD confirmation.
- Valid support, breakout, or pullback.
- Acceptable stop-loss and reward-to-risk ratio.

For a SHORT:

- Bearish market bias.
- Bearish MACD confirmation.
- Valid resistance, breakdown, or pullback.
- Acceptable stop-loss and reward-to-risk ratio.

If any critical condition fails, return NO TRADE.

## Step 5: Calculate Risk

Determine:

- Entry price.
- Stop-loss price.
- Profit target.
- Dollar risk.
- Position size.
- Reward-to-risk ratio.

Reject trades that do not satisfy the configured risk parameters.

## Step 6: Generate a Trading Decision

Every analysis should return one of:

- BUY
- SELL / SHORT
- HOLD
- EXIT
- NO TRADE

The agent must explain the decision using actual indicator values and price structure.

# 11. Required AI Agent Output Format

For each analyzed trading opportunity, return:

| Field              | Description                                |
| ------------------ | ------------------------------------------ |
| Symbol             | Instrument being analyzed                  |
| Timeframe          | Chart timeframe                            |
| Market Bias        | Bullish / Bearish / Sideways               |
| Price vs 200 MA    | Above / Below                              |
| 200 MA Slope       | Rising / Falling / Flat                    |
| MACD Position      | Above / Below Signal                       |
| Zero-Line Position | Positive / Negative                        |
| Histogram          | Positive / Negative                        |
| Momentum           | Strengthening / Weakening                  |
| Price Action       | Support / Resistance / Breakout / Pullback |
| Trade Direction    | Long / Short / None                        |
| Entry              | Proposed entry                             |
| Stop Loss          | Proposed stop                              |
| Take Profit        | Proposed target                            |
| Risk/Reward        | Calculated ratio                           |
| Final Decision     | BUY / SELL / HOLD / EXIT / NO TRADE        |
| Confidence         | Evidence-based assessment                  |
| Reasoning          | Explanation of the decision                |

Confidence should reflect how many independent conditions align. It must not be presented as a statistically calibrated probability of winning unless the agent has been properly trained and validated on historical data.

# 12. Important Strategy Constraints

The AI agent must follow these principles:

1. Never take a trade based solely on a MACD crossover.
2. Never ignore the 200 MA trend filter.
3. Never chase an extended breakout without confirmation.
4. Avoid trading during sideways and congested market conditions.
5. Prefer the first and second pullbacks over late-stage trend entries.
6. Treat MACD histogram contraction as a possible momentum warning.
7. Never move a stop-loss farther away to avoid realizing a loss.
8. Never enter without identifying a stop-loss.
9. Do not force trades when conditions are not aligned.
10. Do not assume that a bullish crossover below zero guarantees a profitable long trade.
11. Confirm signals using completed candles rather than unconfirmed intrabar crossovers.
12. Account for transaction costs, slippage, and liquidity.

# 13. Backtesting and Performance Validation

Before deploying this strategy with real capital, evaluate it across different market conditions.

Track the following metrics:

- Win rate.
- Average winning trade.
- Average losing trade.
- Profit factor.
- Maximum drawdown.
- Expectancy per trade.
- Average reward-to-risk ratio.
- Number of consecutive losses.
- Performance by timeframe.
- Performance in trending versus sideways markets.

Test different instruments and timeframes independently.

Avoid selecting indicator settings solely because they performed well on historical data. Use out-of-sample testing and paper trading to reduce overfitting.

---

# Final Strategy Principle

The strategy is built around three confirmations:

**TREND + MOMENTUM + PRICE ACTION**

- The 200 MA establishes the direction.
- MACD confirms momentum.
- Support, resistance, breakouts, and pullbacks determine the entry opportunity.
- Risk management determines whether the opportunity is worth taking.
- Discipline determines when to stop trading.

The AI agent should prioritize high-quality, confirmed setups rather than generating frequent trading signals.

**When the trend, momentum, and price action do not agree, the preferred decision is NO TRADE.**

**One important recommendation:** When configuring your AI trading agent, make the 200 MA, MACD, and price-action rules mandatory filters, but let risk management have the final authority. This prevents the agent from taking an attractive-looking MACD signal when the potential loss is disproportionately large.