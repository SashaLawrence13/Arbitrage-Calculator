"""A Buffett-style read of a company from its filings.

This is a screen, not advice. It measures the things Berkshire's letters keep returning to
that can be measured: steady earnings, high returns on capital without much debt, cash
that the owners could actually take out, and a price that leaves a margin of safety.
The things that cannot be measured (is the business understandable, is there a durable
moat, is management honest and good at allocating capital) are left as questions.

Every number is computed from totals, not per-share figures, because per-share history
in filings is not adjusted for stock splits.
"""
import json

from . import data

DISCOUNT = 0.10           # required return used in the valuation
TERMINAL_GROWTH = 0.025
MAX_GROWTH = 0.08         # never assume faster growth than this, whatever the past shows
YEARS = 10


def series(db, cik):
    """{year: {item: value}} with balance-sheet items read at that year's fiscal year end."""
    out = {}
    for item, year, end, val in db.execute("SELECT item, year, end, val FROM flow WHERE cik=?", (cik,)):
        out.setdefault(year, {})[item] = val
        if item == "net_income":
            out[year]["_end"] = out[year]["end"] = end
    inst = {}
    for item, end, val in db.execute("SELECT item, end, val FROM inst WHERE cik=?", (cik,)):
        inst[(item, end)] = val
    for year, row in out.items():
        for item in data.INSTANTS:
            v = inst.get((item, row.get("_end")))
            if v is not None:
                row[item] = v
    return out


def cagr(first, last, years):
    if first is None or last is None or first <= 0 or last <= 0 or years <= 0:
        return None
    return (last / first) ** (1.0 / years) - 1


def check(name, passed, value, rule):
    return {"name": name, "pass": passed, "value": value, "rule": rule}


def pct(x):
    return None if x is None else round(100 * x, 1)


def analyze(db, cik, treasury=None):
    co = db.execute("SELECT name, ticker, sic, sic_desc, price, price_ts FROM company WHERE cik=?",
                    (cik,)).fetchone()
    if not co:
        return None
    name, ticker, sic, sic_desc, price, price_ts = co
    s = series(db, cik)
    years = sorted(y for y in s if s[y].get("net_income") is not None)[-YEARS:]
    if len(years) < 5:
        return None
    rows = [dict(s[y], year=y) for y in years]
    financial = sic is not None and 6000 <= sic <= 6799
    flags, notes = [], []

    ni = [r["net_income"] for r in rows]
    ever_debt = any(r.get("debt") is not None for r in rows)
    used_fcf = False
    for r in rows:
        rev = r.get("revenue")
        if rev and rev > 1e8 and 0 < abs(r["net_income"]) < 1e5:
            flags.append("Filing data looks wrong in %d (profit of $%d on $%.1fB of sales, probably reported in the wrong units)." % (r["year"], r["net_income"], rev / 1e9))
        if rev and abs(r["net_income"]) > 1.5 * abs(rev):
            flags.append("Filing data looks wrong in %d (profit larger than sales). Treat every number here with suspicion." % r["year"])
        r["owner_earnings"] = None
        if r.get("dep_amort") is not None and r.get("capex") is not None:
            r["owner_earnings"] = r["net_income"] + r["dep_amort"] - r["capex"]
        elif r.get("op_cash") is not None and r.get("capex") is not None:
            r["owner_earnings"] = r["op_cash"] - r["capex"]     # free cash flow as a stand-in
            used_fcf = True
        r["roe"] = r["net_income"] / r["equity"] if r.get("equity") and r["equity"] > 0 else None
        debt_y = r.get("debt")
        if debt_y is None and not ever_debt:
            debt_y = 0.0                      # never reported long-term debt in any year
        cap = (r.get("equity") or 0) + (debt_y or 0)
        r["roc"] = (r["net_income"] / cap
                    if r.get("equity") is not None and debt_y is not None and cap > 0 else None)
        r["net_margin"] = r["net_income"] / rev if rev and rev > 0 else None
        r["gross_margin"] = r["gross_profit"] / rev if rev and rev > 0 and r.get("gross_profit") is not None else None

    def avg(key, rs=rows):
        v = [r[key] for r in rs if r.get(key) is not None]
        return sum(v) / len(v) if v else None

    n = len(rows)
    last = rows[-1]
    checks = []

    profitable = sum(1 for x in ni if x > 0)
    checks.append(check("Steady earnings", profitable == n, "%d of %d years profitable" % (profitable, n),
                        "profitable every year"))

    roc = avg("roc")
    checks.append(check("High return on capital", roc is not None and roc >= 0.12,
                        None if roc is None else "%.1f%% a year on equity plus debt" % (100 * roc),
                        "12% or more, averaged"))

    recent_ni = sum(ni[-3:]) / len(ni[-3:])
    debt_rows = [r for r in rows[-3:] if r.get("debt") is not None]
    debt = debt_rows[-1]["debt"] if debt_rows else (0.0 if not ever_debt else None)
    if debt is None:
        checks.append(check("Little debt", None, "long-term debt not found in filings", "debt under 5 years of profit"))
    else:
        yrs = debt / recent_ni if recent_ni > 0 else None
        checks.append(check("Little debt", yrs is not None and yrs <= 5,
                            "debt is %.1f years of profit" % yrs if yrs is not None else "no profit to repay debt",
                            "debt under 5 years of profit"))

    nm = avg("net_margin")
    checks.append(check("Healthy margins", nm is not None and nm >= 0.10 and (last.get("net_margin") or 0) >= 0.7 * nm,
                        None if nm is None else "%.1f%% average net margin, %.1f%% latest" % (100 * nm, 100 * (last.get("net_margin") or 0)),
                        "10% or more and not collapsing"))

    oe = [r["owner_earnings"] for r in rows if r["owner_earnings"] is not None]
    if len(oe) >= max(5, n - 2):
        ni_same = [r["net_income"] for r in rows if r["owner_earnings"] is not None]
        ratio = sum(oe) / sum(ni_same) if sum(ni_same) > 0 else None
        checks.append(check("Profit turns into cash for owners",
                            ratio is not None and ratio >= 0.7 and sum(1 for x in oe if x > 0) >= len(oe) - 1,
                            None if ratio is None else "owner earnings were %.0f%% of reported profit" % (100 * ratio),
                            "70% or more, positive nearly every year"))
    else:
        ratio = None
        checks.append(check("Profit turns into cash for owners", None, "depreciation or capital spending not found", "70% or more"))

    span = years[-1] - years[0]
    k = 3 if n >= 8 else 2                 # average the ends so one odd year does not set the rate
    early, late = sum(ni[:k]) / k, sum(ni[-k:]) / k
    g = cagr(early, late, max(span - (k - 1), 1))
    checks.append(check("Growing profit", g is not None and g >= 0.05,
                        None if g is None else "%.1f%% a year" % (100 * g), "5% a year or more"))

    retained = sum(r["net_income"] - (r.get("dividends") or 0) for r in rows[:-1])
    inc = (late - early) / retained if retained > 0 else None
    checks.append(check("Kept profits earned their keep", inc is not None and inc >= 0.12,
                        None if inc is None else "each $1 kept added %.0f cents of yearly profit" % (100 * inc),
                        "12 cents or more per $1 kept"))

    bb = sum((r.get("buybacks") or 0) for r in rows)
    sh = [r["shares"] for r in rows if r.get("shares")]
    checks.append(check("Buying back shares, not printing them", bb > 0.1 * sum(max(x, 0) for x in ni) if sum(max(x, 0) for x in ni) > 0 else None,
                        "$%.1fB spent on buybacks over %d years" % (bb / 1e9, n),
                        "buybacks above 10% of total profit (share counts are not split-adjusted, so dollars are used)"))

    try:
        disputes = db.execute("SELECT item, year, used, other FROM dispute WHERE cik=? AND year>=? ORDER BY year",
                              (cik, years[0])).fetchall()
    except Exception:
        disputes = []
    big = [x for x in disputes if abs(x[2] - x[3]) > 0.10 * max(abs(x[2]), 1)]
    if big:
        names = {"net_income": "profit", "revenue": "sales", "op_cash": "operating cash", "capex": "capital spending",
                 "dep_amort": "depreciation", "dividends": "dividends", "buybacks": "buybacks"}
        flags.append("Two SEC sources give different figures for " + ", ".join(
            "%s in %d ($%.2fB used, from the annual report; $%.2fB in the other)" % (names.get(i, i), y, u / 1e9, o / 1e9)
            for i, y, u, o in big[:4]) + (" and %d more" % (len(big) - 4) if len(big) > 4 else "") + ". Check the annual report.")
    elif disputes:
        notes.append("%d small differences (under 10%%) between two SEC sources; the annual-report figure is used." % len(disputes))
    if financial:
        flags.append("Bank, insurer or other financial company (%s). Debt and owner-earnings checks do not mean the same thing here." % (sic_desc or "financial"))

    # ---- price and value ----
    if used_fcf:
        flags.append("Depreciation was not found in some years, so free cash flow (operating cash minus capital spending) stands in for owner earnings there.")
    cover = db.execute("SELECT val FROM inst WHERE cik=? AND item='shares_out' ORDER BY end DESC LIMIT 1",
                       (cik,)).fetchone()
    cover = cover[0] if cover and cover[0] and cover[0] > 1e6 else None
    diluted = sh[-1] if sh and sh[-1] > 1e6 else None
    shares = cover or diluted
    if cover and diluted and not (0.5 <= cover / diluted <= 2.0):
        flags.append("Two share counts in the filings disagree (stock split or several share classes). Valuation left out.")
        shares = None
    cap = price * shares if price and shares else None
    oe3 = [r["owner_earnings"] for r in rows[-3:] if r["owner_earnings"] is not None]
    base = sum(oe3) / len(oe3) if oe3 else None
    value = None
    if cap and base and base > 0:
        hist = max(0.0, min(g or 0.0, MAX_GROWTH))
        scen = {}
        for label, gr in (("no growth", 0.0), ("half of past growth", hist / 2), ("past growth, capped at 8%", hist)):
            pv, cash = 0.0, base
            for t in range(1, 11):
                cash *= 1 + gr
                pv += cash / (1 + DISCOUNT) ** t
            pv += cash * (1 + TERMINAL_GROWTH) / (DISCOUNT - TERMINAL_GROWTH) / (1 + DISCOUNT) ** 10
            scen[label] = {"growth_pct": pct(gr), "value_per_share": round(pv / shares, 2),
                           "price_to_value": round(cap / pv, 2)}
        value = {"owner_earnings_yield_pct": pct(base / cap), "pe": round(cap / recent_ni, 1) if recent_ni > 0 else None,
                 "scenarios": scen, "treasury_pct": (treasury or {}).get("pct"),
                 "note": "Value = next 10 years of owner earnings plus a terminal value, discounted at 10%. "
                         "Share count comes from the cover page of the latest filing when available, otherwise the latest annual diluted average."}
        if value["pe"] is not None and (value["pe"] > 400 or value["pe"] < 1):
            flags.append("Price and share count do not fit together (possible stock split or multiple share classes). Ignore the valuation.")
            value = None

    summary = {"roc_pct": pct(roc), "net_margin_pct": pct(nm), "latest_margin_pct": pct(last.get("net_margin")),
               "debt_years": round(debt / recent_ni, 1) if debt is not None and recent_ni > 0 else None,
               "growth_pct": pct(g), "owner_ratio_pct": pct(ratio), "revenue": last.get("revenue"),
               "latest_profit": last["net_income"], "latest_owner_earnings": last["owner_earnings"],
               "latest_roc_pct": pct(last.get("roc")), "latest_end": last.get("end"),
               "failing": [c["name"] for c in checks if c["pass"] is False]}
    score = sum(1 for c in checks if c["pass"])
    known = sum(1 for c in checks if c["pass"] is not None)
    return {"cik": cik, "name": name, "ticker": ticker, "industry": sic_desc, "financial": financial,
            "price": price, "market_cap": cap, "score": score, "of": known, "checks": checks,
            "value": value, "flags": flags, "notes": notes, "summary": summary,
            "years": [{"year": r["year"], "end": r.get("end"), "revenue": r.get("revenue"), "net_income": r["net_income"],
                       "owner_earnings": r["owner_earnings"], "roc_pct": pct(r["roc"]), "roe_pct": pct(r["roe"]),
                       "net_margin_pct": pct(r["net_margin"]), "gross_margin_pct": pct(r["gross_margin"]),
                       "debt": r.get("debt"), "equity": r.get("equity")} for r in rows]}


def screen(db):
    t = db.execute("SELECT v FROM meta WHERE k='treasury10'").fetchone()
    treasury = json.loads(t[0]) if t else None
    out = []
    for cik in data.universe(db):
        a = analyze(db, cik, treasury)
        if a and a["ticker"]:
            v = a["value"] or {}
            mid = (v.get("scenarios") or {}).get("half of past growth") or {}
            out.append({"cik": a["cik"], "name": a["name"], "ticker": a["ticker"], "industry": a["industry"],
                        "financial": a["financial"], "score": a["score"], "of": a["of"], "price": a["price"],
                        "market_cap": a["market_cap"], "oe_yield_pct": v.get("owner_earnings_yield_pct"),
                        "pe": v.get("pe"), "price_to_value": mid.get("price_to_value"),
                        "roc_pct": a["summary"]["roc_pct"], "net_margin_pct": a["summary"]["net_margin_pct"],
                        "debt_years": a["summary"]["debt_years"], "growth_pct": a["summary"]["growth_pct"],
                        "revenue": a["summary"]["revenue"],
                        "flagged": any(not f.startswith(("Depreciation was not found", "Bank, insurer"))
                                       for f in a["flags"]), "fails": [c["name"] for c in a["checks"] if c["pass"] is False]})
    out.sort(key=lambda r: (-r["score"], r["price_to_value"] if r["price_to_value"] is not None else 99))
    return {"treasury": treasury, "rows": out}


if __name__ == "__main__":
    import sys
    db = data.connect()
    t = sys.argv[1].upper()
    cik = db.execute("SELECT cik FROM company WHERE ticker=?", (t,)).fetchone()
    print(json.dumps(analyze(db, cik[0]) if cik else None, indent=1))
