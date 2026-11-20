"""Run the tests in BACKTEST_PLAN.md on the downloaded data.

    python3 -m scanner.backtest_run
"""
import os
import random
import sqlite3
import sys
from collections import defaultdict

from . import model

PM_SPREAD = 0.01          # assumed cost of crossing the spread on Polymarket
MAX_SPREAD = 0.10
EDGE = 0.03
BOOT = 2000


def load(db):
    rows = []
    dropped = defaultdict(int)
    for r in db.execute("""SELECT id, site, event, title, category, open_ts, result, fee_rate,
                                  yes_bid, yes_ask, price FROM bt WHERE priced=1 ORDER BY open_ts, id"""):
        mid_, site, event, title, cat, open_ts, result, rate, bid, ask, price = r
        if site == "kalshi":
            if bid is None or ask is None:
                dropped["kalshi: no two-sided quote at bet time"] += 1
                continue
            if ask - bid > MAX_SPREAD:
                dropped["kalshi: bid/ask gap over 10c"] += 1
                continue
            mid, yes_cost, no_cost = (bid + ask) / 2, ask, 1 - bid
        else:
            if price is None:
                dropped["polymarket: no price at bet time"] += 1
                continue
            mid, yes_cost, no_cost = price, price + PM_SPREAD, 1 - price + PM_SPREAD
        if mid < 0.02 or mid > 0.98:
            dropped["price under 2c or over 98c"] += 1
            continue
        rows.append({"id": mid_, "site": site, "event": site + ":" + event, "open_ts": open_ts,
                     "y": result, "rate": rate if rate is not None else 0.07, "mid": mid,
                     "yes_cost": min(yes_cost, 0.999), "no_cost": min(no_cost, 0.999), "cat": cat,
                     "sports": (cat or "").lower().startswith("sports")})
    return rows, dropped


def bet(r, side):
    """One contract held to settlement. Returns (stake including fee, profit)."""
    cost = r["yes_cost"] if side == "yes" else r["no_cost"]
    fee = r["rate"] * cost * (1 - cost)
    won = (r["y"] == 1) == (side == "yes")
    return cost + fee, (1.0 if won else 0.0) - cost - fee, won


def summarize(bets, label):
    """bets: list of (event, stake, profit, won). Return on stake with an event-level bootstrap."""
    if not bets:
        print("  %-34s no bets" % label)
        return
    by_event = defaultdict(lambda: [0.0, 0.0])
    for b in bets:
        ev, stake, profit = b[0], b[1], b[2]
        by_event[ev][0] += stake
        by_event[ev][1] += profit
    ev = list(by_event.values())
    stake, profit = sum(e[0] for e in ev), sum(e[1] for e in ev)
    rng = random.Random(13)
    sims = []
    n = len(ev)
    for _ in range(BOOT):
        s = p = 0.0
        for _ in range(n):
            e = ev[rng.randrange(n)]
            s += e[0]
            p += e[1]
        sims.append(p / s)
    sims.sort()
    lo, hi = sims[int(0.025 * BOOT)], sims[int(0.975 * BOOT) - 1]
    wins = sum(1 for b in bets if b[3])
    print("  %-34s bets %6d  events %5d  won %5.1f%%  staked $%9.2f  profit $%8.2f  return %+6.2f%%  (95%% range %+.2f%% to %+.2f%%)"
          % (label, len(bets), n, 100.0 * wins / len(bets), stake, profit, 100 * profit / stake, 100 * lo, 100 * hi))


def test1(rows):
    print("\nTEST 1: back heavy favourites (YES at 85c+ -> buy YES; YES at 15c or less -> buy NO)")
    for label, keep in (("ALL (main result)", lambda r: True),
                        ("kalshi", lambda r: r["site"] == "kalshi"),
                        ("polymarket", lambda r: r["site"] == "polymarket"),
                        ("sports only", lambda r: r["sports"]),
                        ("kalshi sports", lambda r: r["sports"] and r["site"] == "kalshi"),
                        ("polymarket sports", lambda r: r["sports"] and r["site"] == "polymarket"),
                        ("everything except sports", lambda r: not r["sports"])):
        bets = []
        for r in rows:
            if not keep(r):
                continue
            side = "yes" if r["mid"] >= 0.85 else "no" if r["mid"] <= 0.15 else None
            if side:
                stake, profit, won = bet(r, side)
                bets.append((r["event"], stake, profit, won))
        summarize(bets, label)


def feats(r):
    lg = model.logit(r["mid"])
    poly = 1.0 if r["site"] == "polymarket" else 0.0
    return [1.0, lg, poly, poly * lg]


def test2(rows):
    print("\nTEST 2: the model (train on oldest 70%, test on newest 30%)")
    cut = int(len(rows) * 0.7)
    tr, te = rows[:cut], rows[cut:]
    if len(te) < model.MIN_TEST:
        print("  not enough markets")
        return
    coef = model.fit([feats(r) for r in tr], [r["y"] for r in tr])
    ys = [r["y"] for r in te]
    pm = [model.predict(coef, feats(r)) for r in te]
    pk = [r["mid"] for r in te]
    print("  train %d markets, test %d markets" % (len(tr), len(te)))
    print("  coefficients [intercept, log-odds, polymarket, polymarket x log-odds]:",
          [round(c, 3) for c in coef], "(log-odds above 1 means favourites are underpriced)")
    print("  log loss  model %.5f   market price %.5f   -> %s" % (
        model.logloss(pm, ys), model.logloss(pk, ys),
        "model better" if model.logloss(pm, ys) < model.logloss(pk, ys) else "market better"))
    bets, taken = [], set()
    for r, p in zip(te, pm):
        if r["event"] in taken:
            continue
        for side, prob in (("yes", p), ("no", 1 - p)):
            cost = r["yes_cost"] if side == "yes" else r["no_cost"]
            if prob - cost - r["rate"] * cost * (1 - cost) >= EDGE:
                stake, profit, won = bet(r, side)
                bets.append((r["event"], stake, profit, won, r["sports"]))
                taken.add(r["event"])
                break
    summarize(bets, "model picks (edge 3c+ after fees)")
    summarize([b for b in bets if b[4]], "  of which sports")
    summarize([b for b in bets if not b[4]], "  of which not sports")


def describe(rows):
    print("\nFOR DESCRIPTION: how often YES happened, by price band")
    bands = [(0.02, 0.05), (0.05, 0.10), (0.10, 0.15), (0.15, 0.25), (0.25, 0.40), (0.40, 0.60),
             (0.60, 0.75), (0.75, 0.85), (0.85, 0.90), (0.90, 0.95), (0.95, 0.981)]
    for site in ("kalshi", "polymarket"):
        print("  " + site)
        for lo, hi in bands:
            g = [r for r in rows if r["site"] == site and lo <= r["mid"] < hi]
            if g:
                print("    %2.0f-%2.0fc  markets %6d  average price %5.1fc  YES happened %5.1f%%" % (
                    lo * 100, hi * 100, len(g), 100 * sum(r["mid"] for r in g) / len(g),
                    100.0 * sum(r["y"] for r in g) / len(g)))


def main():
    data_dir = os.environ.get("BT_DIR", "/Volumes/s/arb-scanner-data")
    db = sqlite3.connect(":memory:")
    db.execute("ATTACH ? AS p", (os.path.join(data_dir, "backtest.db"),))
    db.execute("ATTACH ? AS k", (os.path.join(data_dir, "backtest_kalshi.db"),))
    db.execute("""CREATE TEMP VIEW bt AS SELECT * FROM p.bt WHERE site='polymarket'
                  UNION ALL SELECT * FROM k.bt WHERE site='kalshi'""")
    print("COHORT")
    for site, n, priced in db.execute("SELECT site, COUNT(*), SUM(priced) FROM bt GROUP BY 1"):
        print("  %-11s met the inclusion rules: %d   price lookup done: %d" % (site, n, priced))
    rows, dropped = load(db)
    for k, v in sorted(dropped.items()):
        print("  dropped, %s: %d" % (k, v))
    for site in ("kalshi", "polymarket"):
        g = [r for r in rows if r["site"] == site]
        print("  %-11s analysed: %d markets in %d events" % (site, len(g), len({r["event"] for r in g})))
    test1(rows)
    test2(rows)
    describe(rows)


if __name__ == "__main__":
    main()
