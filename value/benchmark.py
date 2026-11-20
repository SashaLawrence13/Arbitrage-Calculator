"""Are you beating an index fund? The only honest way to know.

For every purchase recorded, the same dollars are put into the S&P 500 fund (SPY) on the
same day. Both sides get their dividends, neither reinvests them. Sold shares stay in the
record at the price they were sold for, compared with the index over the same stretch,
so the record cannot be improved by quietly dropping the losers.

A few months of results say nothing. Berkshire's own yardstick is five years.
"""
import bisect
import calendar
import json
import time
import urllib.parse

from . import data

INDEX = "SPY"


def ts_of(day):
    return calendar.timegm(time.strptime(day[:10], "%Y-%m-%d"))


def history(db, symbol, start_ts):
    """Daily closes and dividends from Yahoo, cached for the day."""
    key = "px:%s:%s:%d" % (symbol, time.strftime("%Y-%m-%d"), start_ts // 86400)
    r = db.execute("SELECT v FROM u.settings WHERE k=?", (key,)).fetchone()
    if r:
        return json.loads(r[0])
    url = "https://query1.finance.yahoo.com/v8/finance/chart/%s?period1=%d&period2=%d&interval=1d&events=div" % (
        urllib.parse.quote(symbol.replace(".", "-")), start_ts - 7 * 86400, int(time.time()) + 86400)
    d = data.get(url, ua="Mozilla/5.0", tries=3)
    res = d["chart"]["result"][0]
    closes = res["indicators"]["quote"][0]["close"]
    days = [(t, c) for t, c in zip(res["timestamp"], closes) if c is not None]
    divs = sorted((int(v["date"]), float(v["amount"])) for v in ((res.get("events") or {}).get("dividends") or {}).values())
    out = {"t": [x[0] for x in days], "c": [x[1] for x in days], "div": divs}
    db.execute("DELETE FROM u.settings WHERE k LIKE ?", ("px:%s:%%" % symbol,))
    db.execute("INSERT OR REPLACE INTO u.settings VALUES(?,?)", (key, json.dumps(out)))
    db.commit()
    return out


def close_on(h, ts):
    """Close on that day, or the last trading day before it."""
    i = bisect.bisect_right(h["t"], ts + 86399) - 1
    return h["c"][max(i, 0)] if h["t"] else None


def divs_between(h, a, b):
    return sum(amt for t, amt in h["div"] if a < t <= b)


def compare(db):
    lots = db.execute("""SELECT h.cik, c.ticker, h.shares, h.cost, h.bought, h.sold_date, h.sold_price, c.price
                         FROM u.holding h JOIN company c ON c.cik=h.cik ORDER BY h.bought""").fetchall()
    if not lots:
        return None
    first = min(ts_of(l[4]) for l in lots)
    try:
        idx = history(db, INDEX, first)
    except Exception as e:
        return {"error": "Could not load index prices: %s" % repr(e)[:80]}
    now = int(time.time())
    rows, by = [], {}
    for cik, ticker, shares, cost, bought, sold_date, sold_price, price in lots:
        start = ts_of(bought)
        end = ts_of(sold_date) if sold_date else now
        try:
            h = history(db, ticker, first)
            stock_div = shares * divs_between(h, start, end)
        except Exception:
            stock_div = 0.0
        last = sold_price if sold_date else price
        i0 = close_on(idx, start)
        if not last or not i0:
            continue
        paid = shares * cost
        units = paid / i0
        i1 = close_on(idx, end) if sold_date else idx["c"][-1]
        rows.append({"ticker": ticker, "paid": paid, "yours": shares * last + stock_div,
                     "index": units * i1 + units * divs_between(idx, start, end),
                     "bought": bought, "sold": sold_date, "days": int((end - start) / 86400)})
    if not rows:
        return None
    for r in rows:
        b = by.setdefault(r["ticker"], {"ticker": r["ticker"], "paid": 0.0, "yours": 0.0, "index": 0.0, "open": False})
        for k in ("paid", "yours", "index"):
            b[k] += r[k]
        b["open"] = b["open"] or not r["sold"]
    paid, yours, index = (sum(r[k] for r in rows) for k in ("paid", "yours", "index"))
    longest = max(r["days"] for r in rows)
    return {"paid": paid, "yours": yours, "index": index, "ahead": yours - index,
            "your_return_pct": round(100 * (yours / paid - 1), 1), "index_return_pct": round(100 * (index / paid - 1), 1),
            "by_company": sorted(by.values(), key=lambda b: -(b["yours"] - b["index"])),
            "longest_days": longest,
            "caution": ("Your longest holding is %d days old. Over a stretch this short the difference is mostly luck; "
                        "judge it over five years." % longest) if longest < 3 * 365 else None,
            "index_name": "S&P 500 index fund (SPY)"}
