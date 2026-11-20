# Backtest plan (written 2026-11-18, before any outcome was compared with any price)

Question: on markets that have already settled, would a simple price rule or the model have made money after fees?

## Markets included

Kalshi: every binary market that opened on or after 2026-09-19 (the oldest date in Kalshi's live data), whose last possible expiration date is at least one day before the download, that settled yes or no, with lifetime volume of at least 1,000 contracts.

Polymarket: every closed two-outcome market that started on or after 2026-09-19, with a scheduled end at least two days before the download, resolved to one side, with lifetime volume of at least $5,000.

Why the "last possible expiration has passed" rule: it means a market is included whether it ended early or ran to its deadline. Without it, markets that ended early because the event happened would be over-counted.

## When the bet is placed

Exactly 24 hours after the market opened. The market must still be open then. This time is known in advance. "24 hours before it closed" was rejected because nobody knows the closing time ahead of time when a market can close early, and every Kalshi market can.

## Price paid

Kalshi: the real ask at that hour (YES ask to buy YES, 1 minus YES bid to buy NO). Markets with no two-sided quote or a bid/ask gap over 10 cents are dropped, because that is known at the time.
Polymarket: only one price per hour is published, so the cost is that price plus an assumed 1 cent for crossing the spread.
Fees: each site's taker fee, rate x price x (1 - price), using Kalshi's per-series multiplier and Polymarket's per-market rate. Each bet is one contract, held to settlement.
Markets priced under 2 cents or over 98 cents are dropped.

## Test 1: backing heavy favourites (the main result)

Hypothesis, one direction: cheap contracts are overpriced, so the favourite side wins more often than its price says.
Rule: if YES is at 85 cents or more, buy YES. If YES is at 15 cents or less, buy NO. Otherwise no bet.
Main number: profit per dollar staked after fees, across all bets, with a 95% range from resampling whole events (markets in the same event move together, so they are not counted as independent). It counts as a win only if the whole range is above zero.

## Test 2: the model

Train on the oldest 70% of markets by open time, test on the newest 30%. Features: log-odds of the price, site, and site x log-odds. Compare log loss against the market price itself. Then paper-bet wherever the model's probability beats cost plus fee by 3 cents or more, at most one bet per event, and report profit per dollar staked with the same range.

## Added 2026-11-18 evening, still before any result was seen: sports split

Sasha asked specifically about the sports markets on both sites. Test 1 and Test 2 are also reported for sports markets alone and for everything else alone (Kalshi: series category "Sports"; Polymarket: fee type starting "sports"). The all-markets number stays the main result. The splits are secondary: with several splits, one looking good by chance is expected, so a split only counts if its whole range is above zero and the direction matches the main result.

## Shown for description only

Actual YES rate by price band, by site.

## Known limits, stated up front

- The volume filter uses lifetime volume, which is not known at bet time.
- Polymarket's spread is assumed, not observed, and its fee rates are today's.
- Polymarket markets past their end date but still unresolved are missing.
- The price shown may not have been available for more than a few contracts.
- About two months of data. One season of sports dominates it.
- A null result is a real result: it means the prices were already fair after fees.

## Not tested here

Arbitrage. It needs both sites' order books at the same instant, which nobody publishes historically. That is measured forward by the live scanner.

## Version 2, written 2026-11-20 before any version 2 result exists

Why: version 1 only included markets whose lifetime volume passed a threshold. That is hindsight and it leans toward longshots that won, the same direction as the version 1 model profit. Version 2 removes it.

Markets: Kalshi only (it publishes the real bid and ask at bet time; Polymarket does not). Every binary market that opened on or after 2026-09-19, whose last possible expiration is at least one day before the download, settled yes or no, and was still open 24 hours after opening. No volume filter. To keep the download to a few hours, a fixed one-in-four sample is taken by a checksum of the ticker, which has nothing to do with outcome.

Bet time and price: unchanged (24 hours after open, real ask, taker fee, one contract, hold to settlement).

Filters, all visible at bet time: two-sided quote, bid/ask gap of 10 cents or less, price between 2 and 98 cents, and open interest of at least 100 contracts at bet time.

Tests:
A. The frozen version 1 model (coefficients in model.py), applied only to markets that opened after the version 1 training period ended, so none of them helped fit it. Bets where its probability beats cost plus fee by 3 cents, one per event. Main number: return on stake with the event-resampled 95% range. It counts as confirmed only if the whole range is above zero.
B. Backing heavy favourites, same rule as before, for comparison.
C. Shown for description: YES rate by price band, and result A split into sports and not sports.

If A fails, the version 1 model profit was produced by the volume filter and the model is retired.
