"""Check the screen's numbers against a second SEC source.

The screen is built from the SEC's cross-company "frames". This re-derives the same
figures for the largest companies from each company's own full filing history
("companyfacts"), using only annual 10-K values, and reports every disagreement.

    python3 -m value.audit [how_many]      (default 150 largest by sales)
"""
import json
import sys
import time
from datetime import date

from . import data


def d(s):
    return date(int(s[:4]), int(s[5:7]), int(s[8:10]))


def annual(facts, tags, unit="USD"):
    """{end_date: value} from 10-K filings, full-year periods, first tag wins per date."""
    out = {}
    for tag in tags:
        for f in ((facts.get(tag) or {}).get("units") or {}).get(unit, []):
            if not str(f.get("form", "")).startswith("10-K") or "start" not in f:
                continue
            if not 340 <= (d(f["end"]) - d(f["start"])).days <= 380:
                continue
            key = f["end"]
            if key not in out or f.get("filed", "") > out[key][1]:
                if key not in out or out[key][2] == tag:
                    out[key] = (f["val"], f.get("filed", ""), tag)
    return {k: v[0] for k, v in out.items()}


def instant(facts, tags):
    out = {}
    for tag in tags:
        for f in ((facts.get(tag) or {}).get("units") or {}).get("USD", []):
            if "start" in f:
                continue
            key = f["end"]
            if key not in out or (out[key][2] == tag and f.get("filed", "") > out[key][1]):
                out[key] = (f["val"], f.get("filed", ""), tag)
    return {k: v[0] for k, v in out.items()}


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 150
    db = data.connect()
    ciks = [r[0] for r in db.execute(
        """SELECT c.cik FROM company c JOIN flow f ON f.cik=c.cik AND f.item='revenue' AND f.year=?
           WHERE c.ticker IS NOT NULL ORDER BY f.val DESC LIMIT ?""", (data.LAST_YEAR - 1, n))]
    totals, bad = {}, []
    for i, cik in enumerate(ciks):
        cf = data.get("%s/api/xbrl/companyfacts/CIK%010d.json" % (data.SEC, cik))
        time.sleep(0.13)
        if not cf:
            continue
        facts = (cf.get("facts") or {}).get("us-gaap") or {}
        name = cf.get("entityName")
        for item in ("revenue", "net_income", "op_cash", "capex", "dep_amort", "dividends", "buybacks"):
            truth = annual(facts, data.FLOWS[item])
            for year, end, val in db.execute("SELECT year, end, val FROM flow WHERE cik=? AND item=?", (cik, item)):
                t = totals.setdefault(item, [0, 0, 0])
                if end not in truth:
                    t[2] += 1
                    continue
                t[0] += 1
                if abs(truth[end] - val) > 0.005 * max(abs(truth[end]), 1):
                    t[1] += 1
                    bad.append((name, item, year, end, val, truth[end]))
        ends = {r[0] for r in db.execute("SELECT end FROM flow WHERE cik=? AND item='net_income'", (cik,))}
        for item in ("equity", "debt", "assets"):
            truth = instant(facts, data.INSTANTS[item])
            for end, val in db.execute("SELECT end, val FROM inst WHERE cik=? AND item=?", (cik, item)):
                if end not in ends:
                    continue
                t = totals.setdefault(item, [0, 0, 0])
                if end not in truth:
                    t[2] += 1
                    continue
                t[0] += 1
                if abs(truth[end] - val) > 0.005 * max(abs(truth[end]), 1):
                    t[1] += 1
                    bad.append((name, item, None, end, val, truth[end]))
        if i % 25 == 0:
            print(time.strftime("%H:%M:%S"), "checked", i + 1, "of", len(ciks), flush=True)
    print("\nRESULT (%d companies)" % len(ciks))
    for item, (cmp_, wrong, missing) in totals.items():
        print("  %-11s compared %5d   disagree %4d (%.1f%%)   not in the company's 10-K history %d" % (
            item, cmp_, wrong, 100.0 * wrong / max(cmp_, 1), missing))
    json.dump(bad, open(data.DB.replace("value.db", "value_audit.json"), "w"))
    print("\nFirst disagreements:")
    for b in bad[:40]:
        print("  %-32s %-10s %s %s  screen %.4g  10-K %.4g" % ((b[0] or "")[:32], b[1], b[2] or "", b[3], b[4], b[5]))


if __name__ == "__main__":
    main()
