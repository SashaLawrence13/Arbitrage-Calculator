# Backtest results (run 2026-11-18, rules fixed beforehand in BACKTEST_PLAN.md)

Raw output: /Volumes/s/arb-scanner-data/backtest_results.txt

## Markets

Kalshi: 349,579 settled markets met the rules. 89,050 were dropped for a bid/ask gap over 10 cents at bet time and 26,826 for having no two-sided quote. Polymarket: 70,251 met the rules. Across both sites 7,292 more were dropped for a price under 2 cents or over 98 cents. Analysed: 229,564 Kalshi markets in 52,238 events and 67,098 Polymarket markets in 27,437 events. All opened on or after 2026-09-19.

## Test 1, backing heavy favourites: failed

79,258 bets in 29,368 events. Won 88.8% of bets. Staked $74,477.20, lost $4,063.20. Return -5.46% (95% range -5.76% to -5.15%).

Sports only: -5.73% (range -6.07% to -5.38%), 68,067 bets. Everything except sports: -3.80% (range -4.59% to -3.03%), 11,191 bets. Kalshi -5.32%, Polymarket -6.86%.

The hypothesis was that cheap contracts are overpriced. In this data the opposite held. On Kalshi, contracts priced 5 to 10 cents (average 7.1 cents, 25,011 markets) paid out 10.5% of the time, and contracts priced 90 to 95 cents (average 92.5 cents, 6,193 markets) paid out 90.4% of the time. Fees and the bid/ask gap did the rest.

## Test 2, the model: passed its bar, narrowly, with a caveat that matters

Trained on the oldest 207,663 markets, tested on the newest 88,999. Log loss 0.55926 for the model against 0.56289 for the market price. The model learned one thing: prices 24 hours after open are too extreme, so it shades them toward 50% (log-odds coefficient 0.87).

Its bets: 526, one per event, almost all on cheap contracts. Won 26.8%. Staked $120.21 (one contract per bet), profit $20.79. Return +17.30% (95% range +1.30% to +33.47%).
Sports: 403 bets, +7.48% (range -10.20% to +26.31%), which includes zero. Not sports: 123 bets, +50.93% (range +13.65% to +91.19%).

## Why this is not yet a reason to bet real money

1. The known flaw points the same way as the result. Markets were included only if their lifetime volume reached 1,000 contracts or $5,000. A market where the longshot starts coming in attracts volume, so the sample over-counts longshots that won. That alone could create both the Test 1 loss and the Test 2 profit.
2. The range nearly touches zero (+1.30%), the sports part includes zero, and the non-sports part rests on 123 bets.
3. $120 was staked in total. Whether these prices were available for more than a few contracts is unknown.
4. It is one stretch of about two months.

## What happens next

The model's coefficients are frozen (model.py, FROZEN). The live scanner now places paper bets with it 24 hours after each market opens, using only what is visible at that moment (live bid/ask gap, traded in the last day), with no lifetime-volume filter. That removes flaw 1. If the profit survives a few hundred settled paper bets, it is real. If it vanishes, the backtest profit was the volume filter.

## Not tested

Arbitrage (needs live order books; measured by the scanner). Sportsbook bonus conversions (arithmetic, not prediction: the profit is locked in when both sides are placed).

# Version 2 results (run 2026-11-20, rules fixed beforehand in BACKTEST_PLAN.md)

Raw output: /Volumes/s/arb-scanner-data/backtest2_results.txt

Markets: 177,035 Kalshi markets met the rules (one-in-four sample, no volume filter). Dropped: 50,708 with no two-sided quote at bet time, 44,062 with a bid/ask gap over 10 cents, 1,534 priced under 2 or over 98 cents, 61,554 with open interest under 100 contracts at bet time. Analysed: 19,177 markets in 12,333 events.

Test A, the frozen model on markets it never saw (7,124 markets): it placed no bets. Nothing cleared cost plus fee by 3 cents. Its log loss was 0.50855 against 0.50726 for the market price, so it was slightly worse than just reading the price. Test A failed.

Test B, backing heavy favourites: 6,649 bets in 5,189 events, won 92.7%, staked $6,260.57, lost $94.57. Return -1.51% (95% range -2.19% to -0.83%). That is about the size of the fee and the bid/ask gap.

Prices matched outcomes closely. Contracts averaging 3.3 cents paid 3.3% of the time (1,534 markets), 7.2 cents paid 7.5% (2,094), 49.6 cents paid 48.9% (4,758), 92.6 cents paid 94.8% (597).

Conclusion: the version 1 model profit came from the lifetime-volume filter, as suspected. As committed in the plan, the model is retired (model.py, RETIRED = True). On Kalshi, a day after a market opens, the price is fair and a taker loses about the cost of trading. Not covered: Polymarket (no real bid/ask history), other bet times, and anything using information beyond the price.
