# Arb Scanner

Built on top of the Arbitrage Calculator. It watches Kalshi and Polymarket, saves prices, finds arbitrage using the calculator's fee math, and trains a model on its own history. It never places a bet and never logs in to anything. Standard library Python only, nothing to install.

## Run it

The scanner lives in `~/arb-scanner` on the Mac: code in `run/`, database and log in `data/`. It starts by itself at login and restarts itself if it stops (login item `com.sasha.arbscanner`, script in `~/Library/Application Support/arb-scanner/start.sh`). Dashboard: http://127.0.0.1:8787/

    launchctl kickstart -k gui/$(id -u)/com.sasha.arbscanner     # restart it (after a code change)
    launchctl bootout gui/$(id -u)/com.sasha.arbscanner          # stop it and turn auto-start off

This project folder on the Desktop is the master copy of the code. After editing, copy `scanner/` to `~/arb-scanner/run/` and restart. The iCloud Desktop folder stalls, and macOS blocks login items from the external drive, which is why it does not run from either. Price history older than `keep_days` (7) is trimmed daily. Backtest data stays on `/Volumes/s/arb-scanner-data`.

## Settings (`config.json`, created on first run)

- `bankroll_per_arb`: most dollars to put into one opportunity (default 200).
- `min_profit`, `min_roi`: ignore anything smaller (default $1 and 1%).
- `max_roi`: ignore anything above this (default 15%). Real arbs are a few percent. More than that has always turned out to be a bad match or a stale price.
- `max_days`: ignore anything that ties money up longer (default 120 days).
- `ntfy_topic`: the phone alert channel. Treat it like a password.

## Phone alerts

Install the ntfy app on the iPhone, tap +, and subscribe to the topic in `config.json`. Alerts are sent only for opportunities that do not depend on an unreviewed Kalshi/Polymarket match.

## What it looks for

- Same market: YES plus NO for under $1 after fees.
- NO set: NO on several outcomes of an event where only one can win.
- YES set: YES on every outcome of an event for under $1. Always flagged, because it only works if the list covers every possible outcome.
- Cross-site: opposite sides of the same question on Kalshi and Polymarket.

Every candidate is re-priced from the live order book, with the real fee rate and the number of contracts actually available, before it is recorded.

## Matching Kalshi to Polymarket

The matcher only proposes pairs. The two sites can settle "the same" event under different rules (overtime, ties, "above 90" versus "90 or more"), so each pair is confirmed by hand on the dashboard before it can trigger a phone alert.

## The model

Fixed in advance in `model.py` so the record cannot be tuned afterwards: one row per resolved market taken 24 hours before close, a split by time, log loss compared against the market price itself, and a gate that keeps paper picks off until the model beats the market on at least 300 markets it has never seen. It needs weeks of collected history before it can say anything. A model that never passes the gate means the prices were already right.

## Bonus bet converter

On the dashboard. Enter a bonus amount, the sportsbook's odds on your pick, and a team name. It lists the live Kalshi and Polymarket markets that cover the other side, and the cash locked in. The bonus must be the stake-not-returned kind. Longer odds convert better.

## Sportsbook plays

Off until `odds_api_key` in `config.json` holds a key from the-odds-api.com (free plan: 500 requests a month). Lines are pulled every `odds_hours` hours for the sports in `odds_sports`, so they can be hours old. The dashboard then shows the best use of a bonus at each book, covered-both-sides plays, and prices that beat the no-vig consensus. Not yet run against the live feed.

## Backtest

`BACKTEST_PLAN.md` holds the rules, written before any result was seen. `python3 -m scanner.backtest_collect` downloads settled markets (hours), `python3 -m scanner.backtest_run` prints the results.

## Files

- `feeds.py` public data from both sites
- `store.py` database
- `arbmath.py` fee and stake math, ported from the calculator
- `match.py` cross-site matching
- `scan.py` arbitrage detection and re-pricing
- `alerts.py` phone push
- `model.py` the model and paper picks
- `web.py`, `dashboard.html` dashboard
- `books.py` sportsbook lines and plays
- `backtest_collect.py`, `backtest_run.py` the backtest
- `run.py` the loop

## Value screen (stocks)

The `value/` folder beside `scanner/`. Page: http://127.0.0.1:8787/stocks. It reads ten years of financials for about 1,260 companies ($1B+ in sales, 8+ years of filings) straight from SEC filings, prices from Yahoo and the 10-year Treasury yield from FRED, then runs eight Buffett-style checks and a conservative price-against-value estimate. `value/metrics.py` holds every rule and its reason. Prices refresh daily and filings weekly from the scanner loop; `python3 -m value.data` refreshes by hand. Data sits in `~/arb-scanner/data/value.db`. It is a screen, not advice.

### Ownership questions, portfolio, hold and sell

- `value/study.py` holds the rules. A company can only be added to the portfolio after a graded facts quiz (80%), 20 written answers, your own value and buy-below price, and a tick that you read the annual report. There is a too-hard pile.
- Portfolio page: http://127.0.0.1:8787/portfolio. Look-through owner earnings, a hold-or-sell review of each holding against the business facts saved with your written case, and a decision journal. It records what you did; it never places an order.
- Your answers, holdings and journal are in `~/arb-scanner/data/portfolio.db`, apart from the downloaded data. Back that file up.
- Numbers come from each company's own annual reports (`value/data.py`, "precise pass"). `python3 -m value.audit` re-checks them; `python3 -m value.test_metrics` checks the arithmetic against hand-computed cases. Where two SEC sources differ by more than 10%, the company page says so.
- Berkshire's own holdings list is not included: the SEC archive refuses automated downloads without a declared contact address.
