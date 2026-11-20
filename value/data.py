"""Company financials from SEC filings (XBRL "frames"), prices from Yahoo, 10-year
Treasury yield from FRED. All free and public, no keys.

    python3 -m value.data            (full refresh: financials, tickers, prices)
    python3 -m value.data prices     (prices and Treasury yield only)
"""
import gzip
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

DB = os.path.expanduser("~/arb-scanner/data/value.db")
SEC = "https://data.sec.gov"
UA = "value-screen-tool/0.1"
FIRST_YEAR, LAST_YEAR = 2014, 2025

# Yearly totals. Several tags per idea because companies label the same line differently;
# the first tag that has a value for a company-year wins.
FLOWS = {
    "revenue": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet",
                "RevenueFromContractWithCustomerIncludingAssessedTax"],
    "net_income": ["NetIncomeLoss"],
    "gross_profit": ["GrossProfit"],
    "op_cash": ["NetCashProvidedByUsedInOperatingActivities"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"],
    "dep_amort": ["DepreciationDepletionAndAmortization", "DepreciationAndAmortization",
                  "DepreciationAmortizationAndAccretionNet", "DepreciationAmortizationAndOther"],
    "dividends": ["PaymentsOfDividends", "PaymentsOfDividendsCommonStock"],
    "buybacks": ["PaymentsForRepurchaseOfCommonStock"],
    "shares": ["WeightedAverageNumberOfDilutedSharesOutstanding"],
}
# Balance-sheet values, taken at each company's own fiscal year end.
INSTANTS = {
    "equity": ["StockholdersEquity"],
    "debt": ["LongTermDebt", "LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligations",
             "LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities",
             "DebtLongtermAndShorttermCombinedAmount", "LongTermNotesPayable", "SeniorNotes",
             "NotesPayable", "DebtInstrumentCarryingAmount"],
    "assets": ["Assets"],
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS flow(cik INTEGER, item TEXT, year INTEGER, end TEXT, val REAL, rank INTEGER,
  PRIMARY KEY(cik, item, year));
CREATE TABLE IF NOT EXISTS inst(cik INTEGER, item TEXT, end TEXT, val REAL, rank INTEGER,
  PRIMARY KEY(cik, item, end));
CREATE TABLE IF NOT EXISTS flow_f(cik INTEGER, item TEXT, year INTEGER, end TEXT, val REAL, rank INTEGER,
  PRIMARY KEY(cik, item, year));
CREATE TABLE IF NOT EXISTS inst_f(cik INTEGER, item TEXT, end TEXT, val REAL, rank INTEGER,
  PRIMARY KEY(cik, item, end));
CREATE TABLE IF NOT EXISTS company(cik INTEGER PRIMARY KEY, name TEXT, ticker TEXT, sic INTEGER,
  sic_desc TEXT, price REAL, price_ts INTEGER, looked_up INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
"""


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def connect():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    db = sqlite3.connect(DB, timeout=60)
    db.executescript(SCHEMA)
    return db


def get(url, ua=UA, tries=5):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept-Encoding": "gzip"})
            with urllib.request.urlopen(req, timeout=40) as r:
                body = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    body = gzip.decompress(body)
                return json.loads(body)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if i == tries - 1:
                raise
            time.sleep(3 * (i + 1))
        except (OSError, ValueError):
            if i == tries - 1:
                raise
            time.sleep(3 * (i + 1))


def frame(taxonomy, tag, unit, period):
    d = get("%s/api/xbrl/frames/%s/%s/%s/%s.json" % (SEC, taxonomy, tag, unit, period))
    time.sleep(0.13)                       # SEC asks for under 10 requests a second
    return (d or {}).get("data") or []


def pull_financials(db):
    names = {}
    for item, tags in FLOWS.items():
        unit = "shares" if item == "shares" else "USD"
        for rank, tag in enumerate(tags):
            for year in range(FIRST_YEAR, LAST_YEAR + 1):
                rows = frame("us-gaap", tag, unit, "CY%d" % year)
                for r in rows:
                    names[r["cik"]] = r.get("entityName") or ""
                    # keep the earliest-listed tag that has a value
                    db.execute("""INSERT INTO flow_f VALUES(?,?,?,?,?,?)
                                  ON CONFLICT(cik,item,year) DO UPDATE SET end=excluded.end,
                                    val=excluded.val, rank=excluded.rank WHERE excluded.rank <= flow_f.rank""",
                               (r["cik"], item, year, r["end"], r["val"], rank))
            db.commit()
            log("pulled %s / %s" % (item, tag))
    for item, tags in INSTANTS.items():
        for rank, tag in enumerate(tags):
            for year in range(FIRST_YEAR, LAST_YEAR + 2):
                for q in (1, 2, 3, 4):
                    for r in frame("us-gaap", tag, "USD", "CY%dQ%dI" % (year, q)):
                        db.execute("""INSERT INTO inst_f VALUES(?,?,?,?,?)
                                      ON CONFLICT(cik,item,end) DO UPDATE SET val=excluded.val,
                                        rank=excluded.rank WHERE excluded.rank <= inst_f.rank""",
                                   (r["cik"], item, r["end"], r["val"], rank))
            db.commit()
            log("pulled %s / %s" % (item, tag))
    db.executemany("INSERT INTO company(cik,name) VALUES(?,?) ON CONFLICT(cik) DO UPDATE SET name=excluded.name",
                   list(names.items()))
    db.commit()


def pull_share_counts(db):
    """Shares outstanding from the cover page of the most recent filings (current, so it
    reflects stock splits). One number per company; multi-class companies may be missing."""
    year = time.gmtime().tm_year
    periods = ["CY%dQ%dI" % (y, q) for y in (year - 1, year) for q in (1, 2, 3, 4)]
    for p in periods:                      # oldest first, so newer values overwrite
        for r in frame("dei", "EntityCommonStockSharesOutstanding", "shares", p):
            db.execute("""INSERT INTO inst_f VALUES(?,?,?,?,0) ON CONFLICT(cik,item,end)
                          DO UPDATE SET val=excluded.val""", (r["cik"], "shares_out", r["end"], r["val"]))
    db.commit()


def universe(db, min_revenue=1e8, min_years=8):
    """Companies big enough and with enough history to judge."""
    return [r[0] for r in db.execute(
        """SELECT f.cik FROM flow_f f WHERE f.item='net_income' GROUP BY f.cik
           HAVING COUNT(*) >= ? AND MAX(f.year) >= ?
              AND (SELECT MAX(val) FROM flow_f r WHERE r.cik=f.cik AND r.item='revenue'
                   AND r.year >= ?) >= ?""",
        (min_years, LAST_YEAR - 1, LAST_YEAR - 1, min_revenue))]


def pull_tickers(db):
    todo = [c for c in universe(db)
            if not db.execute("SELECT looked_up FROM company WHERE cik=?", (c,)).fetchone()[0]]
    log("looking up tickers for %d companies" % len(todo))
    for i, cik in enumerate(todo):
        d = get("%s/submissions/CIK%010d.json" % (SEC, cik)) or {}
        time.sleep(0.13)
        tickers = d.get("tickers") or []
        sic = d.get("sic")
        db.execute("UPDATE company SET ticker=?, sic=?, sic_desc=?, looked_up=1 WHERE cik=?",
                   (tickers[0] if tickers else None, int(sic) if sic and str(sic).isdigit() else None,
                    d.get("sicDescription"), cik))
        if i % 100 == 0:
            db.commit()
    db.commit()


def pull_prices(db):
    rows = db.execute("SELECT cik, ticker FROM company WHERE ticker IS NOT NULL").fetchall()
    log("pricing %d tickers" % len(rows))
    ok = 0
    for i, (cik, ticker) in enumerate(rows):
        sym = urllib.parse.quote(ticker.replace(".", "-"))
        try:
            d = get("https://query1.finance.yahoo.com/v8/finance/chart/%s?range=5d&interval=1d" % sym,
                    ua="Mozilla/5.0", tries=2)
            price = d["chart"]["result"][0]["meta"].get("regularMarketPrice")
        except Exception:
            price = None
        if price:
            db.execute("UPDATE company SET price=?, price_ts=? WHERE cik=?", (price, int(time.time()), cik))
            db.commit()                       # one row at a time, so nothing else waits on this job
            ok += 1
        time.sleep(0.25)
    db.commit()
    log("priced %d of %d" % (ok, len(rows)))


def pull_treasury(db):
    req = urllib.request.Request("https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10",
                                 headers={"User-Agent": "curl/8.4.0", "Accept": "*/*"})
    lines = urllib.request.urlopen(req, timeout=40).read().decode().strip().splitlines()
    for line in reversed(lines):
        date, _, v = line.partition(",")
        try:
            db.execute("INSERT OR REPLACE INTO meta VALUES('treasury10', ?)",
                       (json.dumps({"date": date, "pct": float(v)}),))
            db.commit()
            return
        except ValueError:
            continue


def main():
    db = connect()
    if "precise" in sys.argv:
        return
    if "prices" not in sys.argv:
        pull_financials(db)
        pull_tickers(db)
        pull_precise(db)
    pull_share_counts(db)
    try:
        pull_treasury(db)
    except OSError as e:
        log("treasury yield not updated:", repr(e)[:80])
    pull_prices(db)
    db.execute("INSERT OR REPLACE INTO meta VALUES('refreshed', ?)", (json.dumps(int(time.time())),))
    db.commit()
    log("done")




# ---------------------------------------------------------------------------
# Precise pass: rebuild each company's numbers from its own annual reports.
#
# The cross-company "frames" above are quick but keep only one figure per company and
# year, and an audit (value/audit.py) found that figure was sometimes the wrong one
# (profit including outside shareholders, or a filing with a scale error). Here every
# figure comes from the company's 10-K filings. When filings give more than one value
# for the same year, the one in the most recent filing wins; if that filing itself has
# two, the value repeated most often across all filings wins. Every case where this
# pass and the frames disagree is written to the "dispute" table and shown on the page.
# ---------------------------------------------------------------------------
from collections import Counter
from datetime import date as _date

DISPUTE_SCHEMA = """
CREATE TABLE IF NOT EXISTS dispute(cik INTEGER, item TEXT, year INTEGER, used REAL, other REAL,
  PRIMARY KEY(cik, item, year));
"""


def _d(s):
    return _date(int(s[:4]), int(s[5:7]), int(s[8:10]))


def _pick(cands):
    """cands: list of (val, filed). Returns (value, ambiguous)."""
    latest = max(f for _, f in cands)
    now = {v for v, f in cands if f == latest}
    if len(now) == 1:
        return now.pop(), False
    counts = Counter(v for v, _ in cands)
    best = sorted(now, key=lambda v: -counts[v])
    return best[0], counts[best[0]] == counts[best[1]]


def _annual(facts, tags, unit):
    out = {}                                  # end -> (val, start, ambiguous)
    for tag in tags:
        by_end = {}
        for f in ((facts.get(tag) or {}).get("units") or {}).get(unit, []):
            if not str(f.get("form", "")).startswith("10-K") or "start" not in f:
                continue
            if 340 <= (_d(f["end"]) - _d(f["start"])).days <= 380:
                by_end.setdefault(f["end"], []).append((f["val"], f.get("filed", ""), f["start"]))
        for end, c in by_end.items():
            if end not in out:
                v, amb = _pick([(x[0], x[1]) for x in c])
                out[end] = (v, c[0][2], amb)
    return out


def _instants(facts, tags):
    out = {}
    for tag in tags:
        by_end = {}
        for f in ((facts.get(tag) or {}).get("units") or {}).get("USD", []):
            if "start" not in f and str(f.get("form", "")).startswith("10-K"):
                by_end.setdefault(f["end"], []).append((f["val"], f.get("filed", "")))
        for end, c in by_end.items():
            if end not in out:
                out[end] = _pick(c)[0]
    return out


def fiscal_label(end):
    """Fiscal years are named for the year they end in (Microsoft's year to June 2026 is
    fiscal 2026). Ten days of slack keeps a 52/53-week year that ends on January 1-7 with
    the year it mostly belongs to, so two fiscal years never share a label."""
    return _date.fromordinal(_d(end).toordinal() - 10).year


def pull_precise(db, ciks=None):
    db.executescript(DISPUTE_SCHEMA)
    ciks = ciks or universe(db)
    log("precise pass for %d companies" % len(ciks))
    done = fallback = 0
    for cik in ciks:
        cf = get("%s/api/xbrl/companyfacts/CIK%010d.json" % (SEC, cik))
        time.sleep(0.12)
        gaap = ((cf or {}).get("facts") or {}).get("us-gaap") or {}
        dei = ((cf or {}).get("facts") or {}).get("dei") or {}
        frames = {(i, e): v for i, e, v in db.execute("SELECT item, end, val FROM flow_f WHERE cik=?", (cik,))}
        rows, disputes, inst_rows = [], [], []
        if len(_annual(gaap, FLOWS["net_income"], "USD")) >= 5:
            for item, tags in FLOWS.items():
                for end, (val, start, amb) in _annual(gaap, tags, "shares" if item == "shares" else "USD").items():
                    year = fiscal_label(end)
                    if year < FIRST_YEAR:
                        continue
                    rows.append((cik, item, year, end, val, 0))
                    o = frames.get((item, end))
                    if o is not None and item != "shares" and abs(o - val) > 0.005 * max(abs(val), 1):
                        disputes.append((cik, item, year, val, o))
            for item, tags in INSTANTS.items():
                inst_rows += [(cik, item, end, v, 0) for end, v in _instants(gaap, tags).items()]
            cover = [(f["end"], f["val"]) for f in ((dei.get("EntityCommonStockSharesOutstanding") or {})
                                                    .get("units") or {}).get("shares", [])]
            if cover:
                end, v = max(cover)
                inst_rows.append((cik, "shares_out", end, v, 0))
        else:
            # Too little 10-K history (foreign filers and the like): fall back to the frames.
            fallback += 1
            rows = [(cik, i, fiscal_label(e), e, v, 1) for (i, e), v in frames.items()]
            inst_rows = [(cik, i, e, v, 1) for i, e, v in
                         db.execute("SELECT item, end, val FROM inst_f WHERE cik=?", (cik,))]
        db.execute("DELETE FROM flow WHERE cik=?", (cik,))
        db.execute("DELETE FROM inst WHERE cik=?", (cik,))
        db.execute("DELETE FROM dispute WHERE cik=?", (cik,))
        db.executemany("INSERT OR REPLACE INTO flow VALUES(?,?,?,?,?,?)", rows)
        db.executemany("INSERT OR REPLACE INTO inst VALUES(?,?,?,?,?)", inst_rows)
        db.executemany("INSERT OR REPLACE INTO dispute VALUES(?,?,?,?,?)", disputes)
        done += 1
        if done % 25 == 0:
            db.commit()                           # short transactions: other jobs share this file
        if done % 200 == 0:
            log("precise pass: %d of %d" % (done, len(ciks)))
    db.commit()
    db.execute("INSERT OR REPLACE INTO meta VALUES('precise', ?)", (json.dumps(int(time.time())),))
    db.commit()
    log("precise pass done: %d companies (%d on frames only), %d disagreements recorded" % (
        done, fallback, db.execute("SELECT COUNT(*) FROM dispute").fetchone()[0]))


def extend(db):
    """Bring in companies that are in the universe but have no rebuilt figures yet."""
    db.executescript(DISPUTE_SCHEMA)
    pull_tickers(db)
    have = {r[0] for r in db.execute("SELECT DISTINCT cik FROM flow")}
    pull_precise(db, [c for c in universe(db) if c not in have])
    pull_prices(db)
    db.execute("INSERT OR REPLACE INTO meta VALUES('refreshed', ?)", (json.dumps(int(time.time())),))
    db.commit()
    log("done")


if __name__ == "__main__":
    if "precise" in sys.argv:
        pull_precise(connect())
    elif "extend" in sys.argv:
        extend(connect())
    else:
        main()
