"""Arbitrage detection over one snapshot of both sites.

Kinds of opportunity:
  single   YES + NO on the same market cost under $1.
  no_set   NO on several outcomes of a mutually exclusive event. At most one of them can
           happen, so with k legs at least k-1 pay $1. Holds even if the list of outcomes
           is incomplete.
  yes_set  YES on every outcome of a mutually exclusive event for under $1. Only an arb
           if the list covers every possible outcome, so it is always flagged for a check.
  cross    Opposite sides of a matched Kalshi / Polymarket pair for under $1.
"""
import json
import time
from collections import defaultdict

from . import arbmath, feeds


def leg(m, side):
    price = m["yes_ask"] if side == "yes" else m["no_ask"]
    size = m["yes_ask_size"] if side == "yes" else m["no_ask_size"]
    label = " | ".join(x for x in (m["event_title"], m["outcome"] or m["title"]) if x)
    return {"id": m["id"], "side": side, "price": price, "rate": m["fee_rate"], "size": size,
            "platform": m["platform"], "label": label[:160], "url": m["url"]}


def candidate(kind, legs, payout_units, cfg, close_ts, title, note=""):
    if any(l["price"] is None for l in legs):
        return None
    sized = arbmath.size_basket(legs, payout_units, cfg["bankroll_per_arb"])
    if not sized:
        return None
    days = max((close_ts - time.time()) / 86400, 0) if close_ts else None
    key = kind + ":" + ",".join(sorted(l["id"] + "/" + l["side"] for l in legs))
    return dict(sized, key=key, kind=kind, legs=legs, payout_units=payout_units,
                days=days, title=title[:200], note=note, close_ts=close_ts)


def passes(c, cfg):
    if c["profit"] < cfg["min_profit"] or c["roi"] < cfg["min_roi"]:
        return False
    if c["roi"] > cfg["max_roi"]:       # real arbs are a few percent; more means a bad match
        return False
    if c["days"] is not None and c["days"] > cfg["max_days"]:
        return False
    return True


def find(markets, pairs, cfg):
    """markets: dict id -> market. pairs: rows with a_id, b_id, inverted, status."""
    out = []
    events = defaultdict(list)
    for m in markets.values():
        events[m["event_id"]].append(m)
        if m["yes_ask"] and m["no_ask"]:
            c = candidate("single", [leg(m, "yes"), leg(m, "no")], 1, cfg, m["close_ts"],
                          m["event_title"] or m["title"])
            if c:
                out.append(c)

    for ms in events.values():
        if len(ms) < 2 or not ms[0]["exclusive"]:
            continue
        close = max((m["close_ts"] or 0) for m in ms) or None
        quoted = [m for m in ms if m["no_ask"]]
        if 2 <= len(quoted) <= cfg["max_legs"]:
            c = candidate("no_set", [leg(m, "no") for m in quoted], len(quoted) - 1, cfg,
                          close, ms[0]["event_title"],
                          "Check the event rules say only one outcome can win.")
            if c:
                out.append(c)
        if len(ms) <= cfg["max_legs"] and len(ms) == ms[0]["event_n"] \
                and all(m["yes_ask"] for m in ms):
            c = candidate("yes_set", [leg(m, "yes") for m in ms], 1, cfg, close,
                          ms[0]["event_title"],
                          "Only an arb if these outcomes cover every possibility. Check for "
                          "a missing 'other' or tie outcome before placing anything.")
            if c:
                out.append(c)

    for p in pairs:
        a, b = markets.get(p["a_id"]), markets.get(p["b_id"])
        if not a or not b or p["status"] == "rejected":
            continue
        combos = [("yes", "yes"), ("no", "no")] if p["inverted"] else [("yes", "no"), ("no", "yes")]
        note = "" if p["status"] == "confirmed" else \
            "UNCONFIRMED MATCH: read both sites' rules and confirm the pair on the dashboard."
        for sa, sb in combos:
            c = candidate("cross", [leg(a, sa), leg(b, sb)], 1, cfg,
                          max(a["close_ts"] or 0, b["close_ts"] or 0) or None,
                          a["event_title"] or a["title"], note)
            if c:
                c["pair_id"] = p["id"]
                c["confirmed"] = p["status"] == "confirmed"
                out.append(c)

    out = [c for c in out if passes(c, cfg)]
    out.sort(key=lambda c: -c["profit"])
    return out


def kalshi_rate(db, series):
    r = db.execute("SELECT rate, ts FROM series_fee WHERE series=?", (series,)).fetchone()
    if r and time.time() - r["ts"] < 7 * 86400:
        return r["rate"]
    rate, ftype = feeds.k_series_rate(series)
    db.execute("INSERT OR REPLACE INTO series_fee VALUES(?,?,?,?)",
               (series, rate, ftype, int(time.time())))
    db.commit()
    return rate


def verify(db, c, markets, cfg):
    """Re-price every leg from the live order book and the real fee rate, then re-size."""
    legs = []
    for l in c["legs"]:
        m = markets[l["id"]]
        n = dict(l)
        if m["platform"] == "kalshi":
            n["price"], n["size"] = feeds.k_fresh(m["extra"]["ticker"])[l["side"]]
            if m["extra"].get("series"):
                n["rate"] = kalshi_rate(db, m["extra"]["series"])
        else:
            token = m["extra"]["tokens"][0 if l["side"] == "yes" else 1]
            n["price"], n["size"] = feeds.p_book(token)
        if n["price"] is None:
            return None
        legs.append(n)
        time.sleep(0.05)
    v = candidate(c["kind"], legs, c["payout_units"], cfg, c["close_ts"], c["title"], c["note"])
    if not v or not passes(v, cfg):
        return None
    v["pair_id"], v["confirmed"] = c.get("pair_id"), c.get("confirmed", True)
    return v


def record(db, verified, now=None):
    """Upsert verified opportunities; return the ones that should trigger an alert."""
    now = now or int(time.time())
    fresh = []
    for v in verified:
        row = db.execute("SELECT id, status, last_ts, best_profit FROM opps WHERE key=?",
                         (v["key"],)).fetchone()
        legs = json.dumps(v["legs"])
        if row is None:
            db.execute(
                """INSERT INTO opps(key,kind,title,first_ts,last_ts,legs,contracts,cost,profit,
                     roi,days,note,status,alerted_ts,best_profit)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,'open',?,?)""",
                (v["key"], v["kind"], v["title"], now, now, legs, v["contracts"], v["cost"],
                 v["profit"], v["roi"], v["days"], v["note"], now, v["profit"]))
            fresh.append(v)
        else:
            reopened = row["status"] != "open" and now - row["last_ts"] > 1800
            better = v["profit"] > 1.5 * (row["best_profit"] or 0)
            db.execute(
                """UPDATE opps SET last_ts=?, legs=?, contracts=?, cost=?, profit=?, roi=?, days=?,
                     note=?, status='open', best_profit=MAX(COALESCE(best_profit,0),?),
                     first_ts=CASE WHEN ? THEN ? ELSE first_ts END WHERE id=?""",
                (now, legs, v["contracts"], v["cost"], v["profit"], v["roi"], v["days"],
                 v["note"], v["profit"], int(reopened), now, row["id"]))
            if reopened or better:
                fresh.append(v)
    keys = [v["key"] for v in verified]
    q = "UPDATE opps SET status='gone' WHERE status='open'"
    if keys:
        q += " AND key NOT IN (%s)" % ",".join("?" * len(keys))
    db.execute(q, keys)
    db.commit()
    return fresh
