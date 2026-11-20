"""Version 2 analysis (rules in BACKTEST_PLAN.md, "Version 2").

    python3 -m scanner.backtest2_run
"""
import os
import sqlite3

from . import backtest_run as v1
from . import model
from .backtest2_collect import DB

MIN_OI = 100
DATA = "/Volumes/s/arb-scanner-data"


def v1_training_cutoff():
    """Open time of the first market in version 1's test set."""
    db = sqlite3.connect(":memory:")
    db.execute("ATTACH ? AS p", (os.path.join(DATA, "backtest.db"),))
    db.execute("ATTACH ? AS k", (os.path.join(DATA, "backtest_kalshi.db"),))
    db.execute("""CREATE TEMP VIEW bt AS SELECT * FROM p.bt WHERE site='polymarket'
                  UNION ALL SELECT * FROM k.bt WHERE site='kalshi'""")
    rows, _ = v1.load(db)
    return rows[int(len(rows) * 0.7)]["open_ts"]


def main():
    cutoff = v1_training_cutoff()
    db = sqlite3.connect(DB, timeout=120)
    total, priced = db.execute("SELECT COUNT(*), SUM(priced) FROM bt").fetchone()
    print("COHORT (Kalshi, 1-in-4 sample, no volume filter)")
    print("  met the inclusion rules: %d   price lookup done: %d" % (total, priced))
    rows, drop = [], {"no two-sided quote at bet time": 0, "bid/ask gap over 10c": 0,
                      "price under 2c or over 98c": 0, "open interest under %d at bet time" % MIN_OI: 0}
    for tid, event, series, open_ts, result, bid, ask, oi, cat, rate in db.execute(
            """SELECT b.id, b.event, b.series, b.open_ts, b.result, b.yes_bid, b.yes_ask, b.oi,
                      s.category, s.fee_rate FROM bt b LEFT JOIN series s ON s.series=b.series
               WHERE b.priced=1 ORDER BY b.open_ts, b.id"""):
        if bid is None or ask is None:
            drop["no two-sided quote at bet time"] += 1
            continue
        if ask - bid > v1.MAX_SPREAD:
            drop["bid/ask gap over 10c"] += 1
            continue
        mid = (bid + ask) / 2
        if mid < 0.02 or mid > 0.98:
            drop["price under 2c or over 98c"] += 1
            continue
        if (oi or 0) < MIN_OI:
            drop["open interest under %d at bet time" % MIN_OI] += 1
            continue
        rows.append({"id": tid, "site": "kalshi", "event": event, "open_ts": open_ts, "y": result,
                     "rate": rate if rate is not None else 0.07, "mid": mid, "yes_cost": ask,
                     "no_cost": 1 - bid, "sports": (cat or "").lower().startswith("sports")})
    for k, v in drop.items():
        print("  dropped, %s: %d" % (k, v))
    print("  analysed: %d markets in %d events" % (len(rows), len({r["event"] for r in rows})))

    fresh = [r for r in rows if r["open_ts"] >= cutoff]
    print("\nTEST A: frozen version 1 model %s on markets opened after its training period" % model.FROZEN)
    print("  markets available: %d" % len(fresh))
    bets, taken = [], set()
    for r in fresh:
        if r["event"] in taken:
            continue
        p = model.predict(model.FROZEN, [1.0, model.logit(r["mid"]), 0.0, 0.0])
        for side, prob in (("yes", p), ("no", 1 - p)):
            cost = r["yes_cost"] if side == "yes" else r["no_cost"]
            if prob - cost - r["rate"] * cost * (1 - cost) >= v1.EDGE:
                stake, profit, won = v1.bet(r, side)
                bets.append((r["event"], stake, profit, won, r["sports"]))
                taken.add(r["event"])
                break
    v1.summarize(bets, "frozen model picks (MAIN RESULT)")
    v1.summarize([b for b in bets if b[4]], "  of which sports")
    v1.summarize([b for b in bets if not b[4]], "  of which not sports")
    if fresh:
        ys = [r["y"] for r in fresh]
        print("  log loss on those markets: model %.5f   market price %.5f" % (
            model.logloss([model.predict(model.FROZEN, [1.0, model.logit(r["mid"]), 0.0, 0.0]) for r in fresh], ys),
            model.logloss([r["mid"] for r in fresh], ys)))

    print("\nTEST B: back heavy favourites (all analysed markets)")
    fav = []
    for r in rows:
        side = "yes" if r["mid"] >= 0.85 else "no" if r["mid"] <= 0.15 else None
        if side:
            stake, profit, won = v1.bet(r, side)
            fav.append((r["event"], stake, profit, won))
    v1.summarize(fav, "favourites")

    print("\nFOR DESCRIPTION: how often YES happened, by price band")
    for lo, hi in [(0.02, 0.05), (0.05, 0.10), (0.10, 0.15), (0.15, 0.25), (0.25, 0.40), (0.40, 0.60),
                   (0.60, 0.75), (0.75, 0.85), (0.85, 0.90), (0.90, 0.95), (0.95, 0.981)]:
        g = [r for r in rows if lo <= r["mid"] < hi]
        if g:
            print("    %2.0f-%2.0fc  markets %6d  average price %5.1fc  YES happened %5.1f%%" % (
                lo * 100, hi * 100, len(g), 100 * sum(r["mid"] for r in g) / len(g),
                100.0 * sum(r["y"] for r in g) / len(g)))


if __name__ == "__main__":
    main()
