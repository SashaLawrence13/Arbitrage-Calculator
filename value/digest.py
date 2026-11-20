"""Statement digest: the lines an owner would look for in ten years of annual reports,
pulled out and turned into plain sentences, so the reading time goes into judgement.

Every figure comes from the company's own 10-K filings (same source and rules as the
screen). Each finding is attached to the ownership question it helps answer. A finding is
evidence, not the answer: it says what happened, not why, and not whether it will last.
"""
import json
import time

from . import data

FLOWS = {
    "revenue": data.FLOWS["revenue"],
    "cost_of_sales": ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold"],
    "gross_profit": ["GrossProfit"],
    "sga": ["SellingGeneralAndAdministrativeExpense"],
    "rnd": ["ResearchAndDevelopmentExpense"],
    "op_income": ["OperatingIncomeLoss"],
    "interest": ["InterestExpense", "InterestExpenseNonoperating", "InterestExpenseDebt"],
    "pretax": ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
               "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"],
    "tax": ["IncomeTaxExpenseBenefit"],
    "net_income": ["NetIncomeLoss"],
    "op_cash": ["NetCashProvidedByUsedInOperatingActivities"],
    "capex": data.FLOWS["capex"],
    "dep_amort": data.FLOWS["dep_amort"],
    "acquisitions": ["PaymentsToAcquireBusinessesNetOfCashAcquired"],
    "buybacks": ["PaymentsForRepurchaseOfCommonStock"],
    "dividends": data.FLOWS["dividends"],
    "stock_pay": ["ShareBasedCompensation"],
}
INSTANTS = {
    "cash": ["CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"],
    "receivables": ["AccountsReceivableNetCurrent"],
    "inventory": ["InventoryNet"],
    "current_assets": ["AssetsCurrent"],
    "current_liabilities": ["LiabilitiesCurrent"],
    "goodwill": ["Goodwill"],
    "intangibles": ["IntangibleAssetsNetExcludingGoodwill"],
    "assets": ["Assets"],
    "debt": data.INSTANTS["debt"],
    "equity": ["StockholdersEquity"],
}
LABELS = [("revenue", "Sales"), ("gross_profit", "Gross profit"), ("sga", "Selling and admin"), ("rnd", "Research"),
          ("op_income", "Operating profit"), ("interest", "Interest paid"), ("tax", "Income tax"), ("net_income", "Net profit"),
          ("op_cash", "Cash from operations"), ("capex", "Capital spending"), ("dep_amort", "Depreciation"),
          ("acquisitions", "Spent buying companies"), ("buybacks", "Spent on buybacks"), ("dividends", "Dividends paid"),
          ("stock_pay", "Pay given in stock"), ("cash", "Cash"), ("receivables", "Owed by customers"), ("inventory", "Inventory"),
          ("goodwill", "Goodwill"), ("debt", "Long-term debt"), ("equity", "Owners' equity"), ("assets", "Total assets")]


def B(v):
    a = abs(v)
    return ("$%.1fB" % (v / 1e9)) if a >= 1e9 else ("$%.0fM" % (v / 1e6))


def build(cik):
    cf = data.get("%s/api/xbrl/companyfacts/CIK%010d.json" % (data.SEC, cik))
    if not cf:
        return None
    gaap = (cf.get("facts") or {}).get("us-gaap") or {}
    ni = data._annual(gaap, FLOWS["net_income"], "USD")
    ends = sorted(ni)[-10:]
    if len(ends) < 3:
        return {"years": [], "rows": [], "findings": {}, "note": "Too little annual-report history for a digest."}
    table = {k: data._annual(gaap, tags, "USD") for k, tags in FLOWS.items()}
    inst = {k: data._instants(gaap, tags) for k, tags in INSTANTS.items()}

    def f(key, end):
        v = table[key].get(end)
        return v[0] if v else None

    def i(key, end):
        return inst[key].get(end)

    if not any(f("gross_profit", e) is not None for e in ends):       # derive when only cost of sales is tagged
        for e in ends:
            r, c = f("revenue", e), f("cost_of_sales", e)
            if r is not None and c is not None:
                table["gross_profit"][e] = (r - c, None, False)

    years = [data.fiscal_label(e) for e in ends]
    rows = []
    for key, label in LABELS:
        vals = [(f(key, e) if key in FLOWS else i(key, e)) for e in ends]
        if any(v is not None for v in vals):
            rows.append({"label": label, "values": vals})

    def tot(key, es=ends):
        v = [f(key, e) for e in es if f(key, e) is not None]
        return sum(v) if v else None

    def ratio(a, b, es):
        x = [(f(a, e), f(b, e)) for e in es]
        x = [(p, q) for p, q in x if p is not None and q]
        return sum(p for p, _ in x) / sum(q for _, q in x) if x and sum(q for _, q in x) else None

    first, last3, n = ends[:3], ends[-3:], len(ends)
    F = {}

    def add(key, text):
        F.setdefault(key, []).append(text)

    gm0, gm1 = ratio("gross_profit", "revenue", first), ratio("gross_profit", "revenue", last3)
    om0, om1 = ratio("op_income", "revenue", first), ratio("op_income", "revenue", last3)
    if gm1 is not None:
        add("economics", "Of each $1 of sales, %.0f cents is left after the direct cost of what it sells (gross margin, last three years)." % (100 * gm1))
    sga, rnd = ratio("sga", "revenue", last3), ratio("rnd", "revenue", last3)
    if sga is not None:
        add("economics", "Selling and administration takes %.0f cents of each sales dollar." % (100 * sga))
    if rnd is not None:
        add("economics", "Research takes %.0f cents of each sales dollar." % (100 * rnd))
    if om1 is not None:
        add("economics", "Operating profit is %.0f cents per sales dollar%s." % (
            100 * om1, "" if om0 is None else ", against %.0f cents in the first three years shown" % (100 * om0)))
    if gm0 is not None and gm1 is not None:
        d = 100 * (gm1 - gm0)
        add("pricing", "Gross margin went from %.1f%% to %.1f%% over the period (%s). A company with pricing power usually holds or widens this through cost increases." % (
            100 * gm0, 100 * gm1, "widened" if d > 1 else "narrowed" if d < -1 else "held steady"))
        add("moat_trend", "Gross margin %s by %.1f points and operating margin %s over the period." % (
            "rose" if d >= 0 else "fell", abs(d),
            "is not available" if om0 is None or om1 is None else ("rose" if om1 >= om0 else "fell") + " by %.1f points" % abs(100 * (om1 - om0))))
        add("inflation", "Through the cost increases of this period its gross margin %s (%.1f%% to %.1f%%)." % (
            "improved" if d > 1 else "held" if d > -1 else "shrank", 100 * gm0, 100 * gm1))
    r0, r1 = tot("revenue", first), tot("revenue", last3)
    if r0 and r1 and r0 > 0 and n >= 6:
        g = (r1 / r0) ** (1.0 / (n - 3)) - 1
        add("ten_years", "Sales grew about %.1f%% a year over these %d years (%s to %s a year)." % (100 * g, n, B(r0 / 3), B(r1 / 3)))

    oc = tot("op_cash")
    if oc and oc > 0:
        parts = [("capital spending", tot("capex")), ("buying other companies", tot("acquisitions")),
                 ("buying back its own stock", tot("buybacks")), ("dividends", tot("dividends"))]
        used = ", ".join("%.0f%% on %s" % (100 * v / oc, k) for k, v in parts if v)
        add("capital", "Over %d years it brought in %s of cash from operations and spent %s." % (n, B(oc), used or "no recorded amounts on the usual uses"))
        acq = tot("acquisitions") or 0
        if acq > 0.4 * oc:
            add("capital", "Acquisitions took %.0f%% of its operating cash. Growth bought this way depends on the prices paid; check the goodwill line." % (100 * acq / oc))
    gw, assets = i("goodwill", ends[-1]), i("assets", ends[-1])
    if gw and assets:
        add("capital", "Goodwill (the premium paid for acquired companies) is %.0f%% of total assets." % (100 * gw / assets))
    sp, nit = tot("stock_pay", last3), tot("net_income", last3)
    if sp and nit and nit > 0:
        add("managers", "Pay given in stock equalled %.0f%% of net profit over the last three years. The proxy statement (form DEF 14A) shows who got it and how much the managers own." % (100 * sp / nit))
        if sp > 0.2 * nit:
            add("real_profit", "Stock pay is %.0f%% of profit. It is a real cost to owners even though no cash leaves; buybacks may only be offsetting it." % (100 * sp / nit))

    cash, debt = i("cash", ends[-1]), i("debt", ends[-1])
    if cash is not None and debt is not None:
        add("survive", "It holds %s of cash against %s of long-term debt." % (B(cash), B(debt)))
    oi, it = tot("op_income", last3), tot("interest", last3)
    if oi and it and it > 0:
        add("survive", "Operating profit covers its interest bill %.1f times. Under about 3 leaves little room in a bad year." % (oi / it))
    ca, cl = i("current_assets", ends[-1]), i("current_liabilities", ends[-1])
    if ca and cl:
        add("survive", "Assets it can turn to cash within a year are %.1f times the bills due within a year." % (ca / cl))
    losses = [data.fiscal_label(e) for e in ends if (f("net_income", e) or 0) <= 0]
    add("survive", "It lost money in %s." % ", ".join(map(str, losses)) if losses else "It made a profit in every one of the %d years shown." % n)

    nia = tot("net_income")
    if oc is not None and nia and nia > 0:
        add("real_profit", "Cash from operations was %.0f%% of reported profit over the period. Well under 100%% for years means profit that is not arriving as cash." % (100 * oc / nia))
    cx, da = tot("capex"), tot("dep_amort")
    if cx and da:
        add("real_profit", "Capital spending was %.1f times depreciation. Above 1 means the depreciation charge understates what it costs to stay in business." % (cx / da))
    rev = tot("revenue")
    if cx and rev:
        add("capital_light", "Capital spending takes %.1f cents of every sales dollar." % (100 * cx / rev))
    if r0 and r1 and (cx or tot("acquisitions")):
        put = (tot("capex", ends[3:]) or 0) + (tot("acquisitions", ends[3:]) or 0)
        gain = r1 / 3 - r0 / 3
        if put > 0 and gain > 0:
            add("capital_light", "It invested %s in plant and acquisitions and yearly sales rose %s: about $%.2f invested for each added dollar of sales." % (B(put), B(gain), put / gain))
    rec0, rec1 = i("receivables", ends[0]), i("receivables", ends[-1])
    s0, s1 = f("revenue", ends[0]), f("revenue", ends[-1])
    if rec0 and rec1 and s0 and s1:
        d0, d1 = 365 * rec0 / s0, 365 * rec1 / s1
        add("real_profit", "Customers take about %.0f days to pay, against %.0f days at the start. A sharp rise can mean sales booked before the cash is sure." % (d1, d0))
    tx, pt = tot("tax", last3), tot("pretax", last3)
    if tx is not None and pt and pt > 0:
        add("valuation", "It paid %.0f%% of pre-tax profit in income tax over the last three years. A rate far below 21%% may not last." % (100 * tx / pt))
    b, d = tot("buybacks", last3), tot("dividends", last3)
    if nit and nit > 0 and (b or d):
        add("valuation", "Over the last three years it returned %.0f%% of profit to owners (%s in buybacks, %s in dividends)." % (
            100 * ((b or 0) + (d or 0)) / nit, B(b or 0), B(d or 0)))
    return {"years": years, "ends": ends, "rows": rows, "findings": F,
            "note": "From the company's own annual reports (10-K). Lines a company does not report separately are left out. These are facts about the past ten years, not answers."}


def get(db, cik, max_age=7 * 86400):
    """Cached in the user's file so repeat visits are instant."""
    r = db.execute("SELECT v FROM u.settings WHERE k=?", ("digest:%d" % cik,)).fetchone()
    if r:
        d = json.loads(r[0])
        if time.time() - d.get("_ts", 0) < max_age:
            return d
    d = build(cik)
    if d:
        d["_ts"] = int(time.time())
        db.execute("INSERT OR REPLACE INTO u.settings VALUES(?,?)", ("digest:%d" % cik, json.dumps(d)))
        db.commit()
    return d


if __name__ == "__main__":
    import sys
    from . import study
    db = study.connect()
    c = db.execute("SELECT cik, name FROM company WHERE ticker=?", (sys.argv[1].upper(),)).fetchone()
    d = build(c[0])
    print(c[1], d["years"])
    for row in d["rows"]:
        print("  %-24s" % row["label"], " ".join("%8s" % ("-" if v is None else B(v)) for v in row["values"][-5:]))
    for k, fs in d["findings"].items():
        print(k)
        for t in fs:
            print("   -", t)
