# Handoff (written 2026-11-20)

Read this first in a new session.

## Where things live

- Master copy of the code: `~/Desktop/Vibe Code/Arbitrage Calculator` (iCloud Desktop; it stalls, so nothing runs from there).
- Running copy: `~/arb-scanner/run` (`scanner/` and `value/`). After editing, copy both folders there and restart.
- Live data: `~/arb-scanner/data` (`scanner.db`, `value.db`, logs). Trimmed to 7 days of price history.
- The owner's answers, holdings and journal: `~/arb-scanner/data/portfolio.db`. Never write test rows there without removing them. Never commit it.
- Backtest data: `/Volumes/s/arb-scanner-data` (external drive).
- This git clone: `~/arb-scanner/repo`. The Desktop folder is not a git repo. To publish: copy `scanner/` and `value/` from the running copy into the clone, commit, push.

## Running

The scanner starts at login and restarts itself (LaunchAgent `com.sasha.arbscanner`, script `~/Library/Application Support/arb-scanner/start.sh`; copies are in this repo as `autostart-*`). Never also start it by hand.

    launchctl kickstart -k gui/$(id -u)/com.sasha.arbscanner     # restart after a code change

Dashboard http://127.0.0.1:8787/ , stocks `/stocks`, portfolio `/portfolio`.

## Betting side: state

- Scanner sweeps Kalshi and Polymarket every 4 to 5 minutes. About 92 leads since Nov 18, none self-proving; all need a rules check. 1,662 matched pairs wait for review on the dashboard; only confirmed pairs can alert the phone.
- Price-only prediction model: retired. Backtest version 2 (177,035 Kalshi markets, no volume filter) showed prices are fair. See `scanner/BACKTEST_PLAN.md` and `scanner/BACKTEST_RESULTS.md`. Do not revive it.
- Bonus-bet converter works on the dashboard. Sportsbook plays (`scanner/books.py`) are built but off and untested on live lines: they need `odds_api_key` (the-odds-api.com, free) in `scanner/config.json`.
- Two paper bets tagged `frozen_v2` never settled; not investigated.

## Stock side: state

- Figures come from each company's own 10-K history (`value/data.py`, precise pass). Twelve known figures match 12 of 12. `python3 -m value.audit` and `python3 -m value.test_metrics` re-check data and arithmetic; run both after any change.
- Universe: $100M+ sales and 8+ years of filings (about 2,200). The download of the smaller companies was still running when this was written; check the end of `~/arb-scanner/data/value.log` for "done", and rerun `python3 -m value.data extend` from `~/arb-scanner/run` if it did not finish.
- Ownership gate, statement digest, portfolio, hold-or-sell review, decision journal and index comparison all work and were tested on the live server.

## Open items

1. Annual-report text (business description, risk factors) and Berkshire's own holdings need `www.sec.gov`, which refuses requests without a declared contact address. Waiting on the owner's yes or no to sending a contact email in the request header. Do not disguise the request as a browser.
2. All 20 ownership questions now show help (`study.guide`): what the numbers show, plus where in the filings to find the rest. Pulling the report text itself still needs item 1.
3. Odds key for the sportsbook side (owner's action).
4. This repository is public. Whether to make it private is the owner's call.

## What the owner has been told, so it is not re-argued

No tool can guarantee beating the market or winning at betting; the app gives no buy or sell advice and places no orders or bets; it cannot judge his written answers.
