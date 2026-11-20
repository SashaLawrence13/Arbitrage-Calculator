"""Download settled markets and the price 24h after each opened. See BACKTEST_PLAN.md.

    python3 -m scanner.backtest_collect        (safe to stop and rerun; it resumes)
"""
import calendar
import json
import os
import sqlite3
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

from . import feeds

START = calendar.timegm((2026, 9, 19, 0, 0, 0))
ENTRY_AFTER_OPEN = 24 * 3600
K_MIN_VOL = 1000
P_MIN_VOL = 5000

SCHEMA = """
CREATE TABLE IF NOT EXISTS bt(
  id TEXT PRIMARY KEY, site TEXT, event TEXT, series TEXT, title TEXT, category TEXT,
  open_ts INTEGER, close_ts INTEGER, entry_ts INTEGER, result INTEGER, volume REAL,
  fee_rate REAL, token TEXT, yes_bid REAL, yes_ask REAL, price REAL, priced INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
"""


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def meta(db, k, v=None):
    if v is None:
        r = db.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        return json.loads(r[0]) if r else None
    db.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (k, json.dumps(v)))
    db.commit()


def ts(s):
    return feeds.iso((s or "").replace(" ", "T"))


def kalshi_list(db, now):
    if meta(db, "k_list_done"):
        return
    cursor, pages, kept, seen = meta(db, "k_cursor"), 0, 0, 0
    while True:
        p = {"status": "settled", "limit": 1000, "mve_filter": "exclude", "min_settled_ts": START}
        if cursor:
            p["cursor"] = cursor
        d = feeds.get(feeds.K + "/markets", p, tries=8)
        rows = []
        for m in d.get("markets") or []:
            seen += 1
            if m.get("market_type", "binary") != "binary" or m.get("result") not in ("yes", "no"):
                continue
            o, c, last = ts(m.get("open_time")), ts(m.get("close_time")), ts(m.get("latest_expiration_time"))
            vol = feeds.num(m.get("volume_fp")) or 0
            if not o or not c or o < START or vol < K_MIN_VOL:
                continue
            if last is None or last > now - 86400 or c <= o + ENTRY_AFTER_OPEN:
                continue
            ev = m.get("event_ticker") or ""
            rows.append(("K:" + m["ticker"], "kalshi", ev, ev.split("-")[0], (m.get("title") or "")[:200],
                         "", o, c, o + ENTRY_AFTER_OPEN, 1 if m["result"] == "yes" else 0, vol,
                         None, m["ticker"]))
        db.executemany("""INSERT OR IGNORE INTO bt(id,site,event,series,title,category,open_ts,
                          close_ts,entry_ts,result,volume,fee_rate,token) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
        kept += len(rows)
        cursor = d.get("cursor")
        pages += 1
        meta(db, "k_cursor", cursor)
        if pages % 100 == 0:
            log("kalshi list: %d pages, %d seen, %d kept" % (pages, seen, kept))
        if not cursor or not d.get("markets"):
            break
        time.sleep(0.03)
    meta(db, "k_list_done", True)
    log("kalshi list done: %d seen this run, %d kept" % (seen, kept))


def kalshi_prices(db):
    rows = db.execute("SELECT id, token, entry_ts FROM bt WHERE site='kalshi' AND priced=0").fetchall()
    groups = defaultdict(list)
    for mid, ticker, entry in rows:
        groups[entry].append((mid, ticker))
    log("kalshi prices: %d markets in %d time groups" % (len(rows), len(groups)))
    done = 0
    for entry, items in groups.items():
        for i in range(0, len(items), 100):
            batch = items[i:i + 100]
            try:
                d = feeds.get(feeds.K + "/markets/candlesticks", {
                    "market_tickers": ",".join(t for _, t in batch), "start_ts": entry - 3600,
                    "end_ts": entry, "period_interval": 60, "include_latest_before_start": "true"}, tries=8)
            except Exception as e:
                log("candles failed", entry, repr(e)[:100])
                continue
            got = {}
            for mk in d.get("markets") or []:
                cs = [c for c in mk.get("candlesticks") or [] if c.get("end_period_ts", 0) <= entry]
                if cs:
                    c = max(cs, key=lambda x: x["end_period_ts"])
                    got[mk["market_ticker"]] = (feeds.px((c.get("yes_bid") or {}).get("close_dollars")),
                                                feeds.px((c.get("yes_ask") or {}).get("close_dollars")))
            for mid, ticker in batch:
                b, a = got.get(ticker, (None, None))
                db.execute("UPDATE bt SET yes_bid=?, yes_ask=?, priced=1 WHERE id=?", (b, a, mid))
            done += len(batch)
            time.sleep(0.05)
        if done and done % 2000 < len(items):
            db.commit()
            log("kalshi prices: %d of %d" % (done, len(rows)))
    db.commit()


def kalshi_fees(db):
    series = [r[0] for r in db.execute(
        "SELECT DISTINCT series FROM bt WHERE site='kalshi' AND fee_rate IS NULL AND yes_ask IS NOT NULL")]
    log("kalshi fees: %d series" % len(series))
    for s in series:
        try:
            rate, _ = feeds.k_series_rate(s)
        except Exception:
            rate = feeds.KALSHI_BASE_RATE
        db.execute("UPDATE bt SET fee_rate=? WHERE site='kalshi' AND series=?", (rate, s))
        time.sleep(0.05)
    db.commit()


def kalshi_categories(db):
    series = [r[0] for r in db.execute(
        "SELECT DISTINCT series FROM bt WHERE site='kalshi' AND category='' AND priced=1")]
    log("kalshi categories: %d series" % len(series))
    for i, s in enumerate(series):
        try:
            d = feeds.get(feeds.K + "/series/" + s, tries=6)["series"]
            mult = feeds.num(d.get("fee_multiplier"))
            rate = feeds.KALSHI_BASE_RATE * (1.0 if mult is None else mult)
            db.execute("UPDATE bt SET category=?, fee_rate=? WHERE site='kalshi' AND series=?",
                       (d.get("category") or "unknown", rate, s))
        except Exception:
            db.execute("UPDATE bt SET category='unknown' WHERE site='kalshi' AND series=?", (s,))
        if i % 200 == 0:
            db.commit()
        time.sleep(0.05)
    db.commit()


def poly_list(db, now):
    if meta(db, "p_list_done"):
        return
    cursor, kept, seen = meta(db, "p_cursor"), 0, 0
    iso = lambda t: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))
    while True:
        p = {"closed": "true", "limit": 100, "volume_num_min": P_MIN_VOL,
             "start_date_min": iso(START), "end_date_max": iso(now - 2 * 86400),
             "order": "endDate", "ascending": "false"}
        if cursor:
            p["after_cursor"] = cursor
        d = feeds.get(feeds.G + "/markets/keyset", p, tries=8)
        ms = d.get("markets") or []
        rows = []
        for m in ms:
            seen += 1
            try:
                outcomes = json.loads(m.get("outcomes") or "[]")
                prices = [float(x) for x in json.loads(m.get("outcomePrices") or "[]")]
                tokens = json.loads(m.get("clobTokenIds") or "[]")
            except ValueError:
                continue
            if len(outcomes) != 2 or len(prices) != 2 or len(tokens) != 2 or max(prices) < 0.99:
                continue
            o, c, end = ts(m.get("startDate")), ts(m.get("closedTime")), ts(m.get("endDate"))
            vol = feeds.num(m.get("volumeNum")) or 0
            if not o or not c or not end or o < START or end > now - 2 * 86400 or vol < P_MIN_VOL:
                continue
            if c <= o + ENTRY_AFTER_OPEN:
                continue
            rate = (feeds.num((m.get("feeSchedule") or {}).get("rate")) or 0.0) if m.get("feesEnabled") else 0.0
            evs = m.get("events") or []
            ev = str(evs[0].get("id")) if evs else str(m["id"])
            rows.append(("P:" + str(m["id"]), "polymarket", ev, "", (m.get("question") or "")[:200],
                         m.get("feeType") or "", o, c, o + ENTRY_AFTER_OPEN,
                         1 if prices[0] > prices[1] else 0, vol, rate, tokens[0]))
        db.executemany("""INSERT OR IGNORE INTO bt(id,site,event,series,title,category,open_ts,
                          close_ts,entry_ts,result,volume,fee_rate,token) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
        kept += len(rows)
        cursor = d.get("next_cursor")
        meta(db, "p_cursor", cursor)
        if seen % 5000 < 100:
            log("polymarket list: %d seen, %d kept" % (seen, kept))
        if not cursor or len(ms) < 100:
            break
        time.sleep(0.03)
    meta(db, "p_list_done", True)
    log("polymarket list done: %d seen this run, %d kept" % (seen, kept))


def poly_price(args):
    mid, token, entry = args
    try:
        h = feeds.get(feeds.C + "/prices-history", {"market": token, "startTs": entry - 6 * 3600,
                                                    "endTs": entry + 60, "fidelity": 60}, tries=5)
        pts = [x for x in h.get("history") or [] if x["t"] <= entry + 60]
        return mid, (feeds.px(max(pts, key=lambda x: x["t"])["p"]) if pts else None), True
    except Exception:
        return mid, None, False


def poly_prices(db):
    rows = db.execute("SELECT id, token, entry_ts FROM bt WHERE site='polymarket' AND priced=0").fetchall()
    log("polymarket prices: %d markets" % len(rows))
    done = 0
    with ThreadPoolExecutor(6) as ex:
        for mid, price, ok in ex.map(poly_price, rows):
            if ok:
                db.execute("UPDATE bt SET price=?, priced=1 WHERE id=?", (price, mid))
            done += 1
            if done % 2000 == 0:
                db.commit()
                log("polymarket prices: %d of %d" % (done, len(rows)))
    db.commit()


def main():
    data_dir = "/Volumes/s/arb-scanner-data"
    konly = "--kalshi-only" in sys.argv
    db = sqlite3.connect(os.path.join(data_dir, "backtest_kalshi.db" if konly else "backtest.db"),
                         timeout=60)
    db.executescript(SCHEMA)
    if konly and not meta(db, "download_ts"):
        src = sqlite3.connect(os.path.join(data_dir, "backtest.db"), timeout=60)
        meta(db, "download_ts", json.loads(src.execute(
            "SELECT v FROM meta WHERE k='download_ts'").fetchone()[0]))
        src.close()
    if "--categories" in sys.argv:
        return kalshi_categories(db)
    now = meta(db, "download_ts")
    if not now:
        now = int(time.time())
        meta(db, "download_ts", now)
    if not konly:
        poly_list(db, now)
        poly_prices(db)
    if konly or not os.path.exists(os.path.join(data_dir, "backtest_kalshi.db")):
        kalshi_list(db, now)
        kalshi_prices(db)
        kalshi_categories(db)
    log("collection finished:", [tuple(r) for r in db.execute(
        "SELECT site, COUNT(*), SUM(priced), SUM(COALESCE(yes_ask, price) IS NOT NULL) FROM bt GROUP BY 1")])


if __name__ == "__main__":
    main()
