"""SQLite storage. The SQL is kept plain so it can move to Postgres (Supabase) later."""
import json
import os
import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS markets(
  id TEXT PRIMARY KEY, platform TEXT, event_id TEXT, event_title TEXT, title TEXT,
  outcome TEXT, rules TEXT, close_ts INTEGER, fee_rate REAL, exclusive INTEGER,
  category TEXT, url TEXT, extra TEXT, first_seen INTEGER, last_seen INTEGER,
  result TEXT, resolved_ts INTEGER, checked_ts INTEGER);
CREATE INDEX IF NOT EXISTS markets_event ON markets(event_id);
CREATE INDEX IF NOT EXISTS markets_open ON markets(result, last_seen);
CREATE TABLE IF NOT EXISTS quotes(
  market_id TEXT, ts INTEGER, yes_bid REAL, yes_ask REAL, no_bid REAL, no_ask REAL,
  yes_ask_size REAL, no_ask_size REAL, vol24 REAL);
CREATE INDEX IF NOT EXISTS quotes_m ON quotes(market_id, ts);
CREATE TABLE IF NOT EXISTS opps(
  id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT UNIQUE, kind TEXT, title TEXT,
  first_ts INTEGER, last_ts INTEGER, legs TEXT, contracts REAL, cost REAL, profit REAL,
  roi REAL, days REAL, note TEXT, status TEXT, alerted_ts INTEGER, best_profit REAL);
CREATE TABLE IF NOT EXISTS pairs(
  id INTEGER PRIMARY KEY AUTOINCREMENT, a_id TEXT, b_id TEXT, score REAL,
  status TEXT DEFAULT 'candidate', inverted INTEGER DEFAULT 0, created_ts INTEGER,
  a_text TEXT, b_text TEXT, a_rules TEXT, b_rules TEXT, a_url TEXT, b_url TEXT,
  UNIQUE(a_id, b_id));
CREATE TABLE IF NOT EXISTS paper(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, market_id TEXT, event_id TEXT,
  side TEXT, price REAL, fee REAL, contracts REAL, model_p REAL, market_p REAL,
  result TEXT, pnl REAL);
CREATE TABLE IF NOT EXISTS model_runs(
  ts INTEGER, n_train INTEGER, n_test INTEGER, ll_model REAL, ll_market REAL,
  brier_model REAL, brier_market REAL, coef TEXT, active INTEGER, note TEXT);
CREATE TABLE IF NOT EXISTS series_fee(series TEXT PRIMARY KEY, rate REAL, fee_type TEXT, ts INTEGER);
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
"""


def connect(data_dir, init=True):
    os.makedirs(data_dir, exist_ok=True)
    db = sqlite3.connect(os.path.join(data_dir, "scanner.db"), timeout=30)
    db.row_factory = sqlite3.Row
    if init:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=NORMAL")
        db.executescript(SCHEMA)
    return db


def set_meta(db, k, v):
    db.execute("INSERT INTO meta(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
               (k, json.dumps(v)))


def get_meta(db, k, default=None):
    r = db.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
    return json.loads(r["v"]) if r else default


def quote_tuple(m):
    return (m["yes_bid"], m["yes_ask"], m["no_bid"], m["no_ask"],
            m["yes_ask_size"], m["no_ask_size"])


def save_snapshot(db, markets, last_quotes, now=None, seen=None):
    """Upsert markets; write a quote row only when the quote changed."""
    now = now or int(time.time())
    seen = seen if seen is not None else {}
    rows, qrows = [], []
    for m in markets:
        # Only markets someone can actually trade both sides of; rule text is kept
        # with matched pairs instead of here (it would be hundreds of MB).
        if not (m["yes_ask"] and m["no_ask"]):
            continue
        # Touch a market's row at most once an hour; rewriting all of them every sweep
        # was most of the disk traffic.
        if now - seen.get(m["id"], 0) >= 3600:
            seen[m["id"]] = now
            rows.append((m["id"], m["platform"], m["event_id"], m["event_title"], m["title"],
                         m["outcome"], "", m["close_ts"], m["fee_rate"],
                         int(m["exclusive"]), m["category"], m["url"], "",
                         now, now))
        # Save a quote only when a price moved by half a cent or more, and only for
        # markets that traded in the last day. Size-only changes would fill the disk.
        q = quote_tuple(m)
        old = last_quotes.get(m["id"])
        moved = old is None or any(abs((a or 0) - (b or 0)) >= 0.005
                                   for a, b in zip(q[:4], old[:4]))
        if moved:
            last_quotes[m["id"]] = q
            if m["vol24"] > 0:
                qrows.append((m["id"], now) + q + (m["vol24"],))
    db.executemany(
        """INSERT INTO markets(id,platform,event_id,event_title,title,outcome,rules,close_ts,
             fee_rate,exclusive,category,url,extra,first_seen,last_seen)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(id) DO UPDATE SET last_seen=excluded.last_seen,
             fee_rate=excluded.fee_rate,
             exclusive=excluded.exclusive""", rows)
    db.executemany("INSERT INTO quotes VALUES(?,?,?,?,?,?,?,?,?)", qrows)
    db.commit()
    return len(qrows)
