"""Version 2 download: Kalshi, no volume filter, 1-in-4 sample. See BACKTEST_PLAN.md.

    python3 -m scanner.backtest2_collect      (resumable)
"""
import json
import os
import sqlite3
import time
import zlib
from collections import defaultdict

from . import feeds
from .backtest_collect import ENTRY_AFTER_OPEN, START, log, meta, ts

DB = "/Volumes/s/arb-scanner-data/backtest2_kalshi.db"
SCHEMA = """
CREATE TABLE IF NOT EXISTS bt(
  id TEXT PRIMARY KEY, event TEXT, series TEXT, open_ts INTEGER, close_ts INTEGER,
  entry_ts INTEGER, result INTEGER, volume REAL, yes_bid REAL, yes_ask REAL, oi REAL,
  priced INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS series(series TEXT PRIMARY KEY, category TEXT, fee_rate REAL);
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
"""


def listing(db, now):
    if meta(db, "list_done"):
        return
    cursor, pages, kept = meta(db, "cursor"), 0, 0
    while True:
        p = {"status": "settled", "limit": 1000, "mve_filter": "exclude", "min_settled_ts": START}
        if cursor:
            p["cursor"] = cursor
        d = feeds.get(feeds.K + "/markets", p, tries=10)
        rows = []
        for m in d.get("markets") or []:
            if m.get("market_type", "binary") != "binary" or m.get("result") not in ("yes", "no"):
                continue
            if zlib.crc32(m["ticker"].encode()) % 4:
                continue
            o, c, last = ts(m.get("open_time")), ts(m.get("close_time")), ts(m.get("latest_expiration_time"))
            if not o or not c or o < START or last is None or last > now - 86400:
                continue
            if c <= o + ENTRY_AFTER_OPEN:
                continue
            ev = m.get("event_ticker") or ""
            rows.append((m["ticker"], ev, ev.split("-")[0], o, c, o + ENTRY_AFTER_OPEN,
                         1 if m["result"] == "yes" else 0, feeds.num(m.get("volume_fp")) or 0))
        db.executemany("""INSERT OR IGNORE INTO bt(id,event,series,open_ts,close_ts,entry_ts,result,
                          volume) VALUES(?,?,?,?,?,?,?,?)""", rows)
        kept += len(rows)
        cursor = d.get("cursor")
        pages += 1
        if pages % 25 == 0:
            meta(db, "cursor", cursor)          # commits rows and position together
        if pages % 200 == 0:
            log("list: %d pages this run, %d kept" % (pages, kept))
        if not cursor or not d.get("markets"):
            break
        time.sleep(0.03)
    meta(db, "list_done", True)
    log("list done: %d kept this run" % kept)


def prices(db):
    rows = db.execute("SELECT id, entry_ts FROM bt WHERE priced=0").fetchall()
    groups = defaultdict(list)
    for ticker, entry in rows:
        groups[entry].append(ticker)
    log("prices: %d markets in %d time groups" % (len(rows), len(groups)))
    done = 0
    for entry, items in groups.items():
        for i in range(0, len(items), 100):
            batch = items[i:i + 100]
            try:
                d = feeds.get(feeds.K + "/markets/candlesticks", {
                    "market_tickers": ",".join(batch), "start_ts": entry - 3600, "end_ts": entry,
                    "period_interval": 60, "include_latest_before_start": "true"}, tries=10)
            except Exception as e:
                log("candles failed", entry, repr(e)[:100])
                continue
            got = {}
            for mk in d.get("markets") or []:
                cs = [c for c in mk.get("candlesticks") or [] if c.get("end_period_ts", 0) <= entry]
                if cs:
                    c = max(cs, key=lambda x: x["end_period_ts"])
                    got[mk["market_ticker"]] = (feeds.px((c.get("yes_bid") or {}).get("close_dollars")),
                                                feeds.px((c.get("yes_ask") or {}).get("close_dollars")),
                                                feeds.num(c.get("open_interest_fp")))
            db.executemany("UPDATE bt SET yes_bid=?, yes_ask=?, oi=?, priced=1 WHERE id=?",
                           [got.get(t, (None, None, None)) + (t,) for t in batch])
            done += len(batch)
            if done % 5000 < 100:
                db.commit()
                log("prices: %d of %d" % (done, len(rows)))
            time.sleep(0.04)
    db.commit()


def series_info(db):
    todo = [r[0] for r in db.execute("""SELECT DISTINCT series FROM bt WHERE yes_ask IS NOT NULL
                                        AND series NOT IN (SELECT series FROM series)""")]
    log("series: %d to look up" % len(todo))
    for i, s in enumerate(todo):
        try:
            d = feeds.get(feeds.K + "/series/" + s, tries=6)["series"]
            mult = feeds.num(d.get("fee_multiplier"))
            row = (s, d.get("category") or "unknown", feeds.KALSHI_BASE_RATE * (1.0 if mult is None else mult))
        except Exception:
            row = (s, "unknown", feeds.KALSHI_BASE_RATE)
        db.execute("INSERT OR REPLACE INTO series VALUES(?,?,?)", row)
        if i % 100 == 0:
            db.commit()
        time.sleep(0.04)
    db.commit()


def main():
    db = sqlite3.connect(DB, timeout=120)
    db.executescript(SCHEMA)
    db.execute("CREATE INDEX IF NOT EXISTS bt_series ON bt(series)")
    now = meta(db, "download_ts")
    if not now:
        now = int(time.time())
        meta(db, "download_ts", now)
    listing(db, now)
    prices(db)
    series_info(db)
    log("collection finished:", db.execute(
        "SELECT COUNT(*), SUM(priced), SUM(yes_ask IS NOT NULL) FROM bt").fetchone())


if __name__ == "__main__":
    main()
