# Arbitrage Calculator, Arb Scanner and Value Screen

Three things that grew out of one calculator.

- `Arbitrage Calculator.html`: the original manual calculator (sportsbook, prediction market, tracker).
- `scanner/`: watches Kalshi and Polymarket, finds arbitrage with the calculator's fee math, converts sportsbook bonus bets, and serves a local dashboard. It alerts; it never places a bet. Start at `scanner/README.md`.
- `value/`: a Buffett-style stock screen built from SEC filings, with an ownership questionnaire, a portfolio page, hold-or-sell reviews and an index-fund comparison. It is a screen and a record, not advice, and it never places an order.

Python 3.9, standard library only. Nothing to install.

    python3 -m scanner.run            # dashboard at http://127.0.0.1:8787/ (stocks at /stocks, portfolio at /portfolio)
    python3 -m value.data             # download company financials (about 30 minutes)
    python3 -m value.test_metrics     # hand-computed checks of the arithmetic

`HANDOFF.md` says where everything runs and what is unfinished. Copy `scanner/config.example.json` to `scanner/config.json` before the first run. Personal data (the config with its alert topic and keys, the databases, your answers and holdings) is deliberately not in this repository.
