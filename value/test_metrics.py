"""Hand-computed checks of the screen's arithmetic.   python3 -m value.test_metrics"""
import sqlite3

from . import benchmark, data, metrics, study


def company(db, cik, years, ni, rev, dep, capex, equity, debt, div=0, bb=0, shares=1e9, price=50.0, sic=2000):
    db.execute("INSERT INTO company(cik,name,ticker,sic,sic_desc,price,price_ts) VALUES(?,?,?,?,?,?,0)",
               (cik, "Test %d" % cik, "T%d" % cik, sic, "test", price))
    for i, y in enumerate(years):
        end = "%d-12-31" % y
        for item, v in (("net_income", ni[i]), ("revenue", rev[i]), ("dep_amort", dep), ("capex", capex),
                        ("dividends", div), ("buybacks", bb), ("shares", shares)):
            db.execute("INSERT INTO flow VALUES(?,?,?,?,?,0)", (cik, item, y, end, v))
        db.execute("INSERT INTO inst VALUES(?,?,?,?,0)", (cik, "equity", end, equity))
        if debt is not None:
            db.execute("INSERT INTO inst VALUES(?,?,?,?,0)", (cik, "debt", end, debt))


def main():
    db = sqlite3.connect(":memory:")
    db.execute("ATTACH ':memory:' AS u")
    db.executescript(data.SCHEMA + data.DISPUTE_SCHEMA + study.SCHEMA)
    years = list(range(2016, 2026))
    # Company 1: flat $100M profit on $1B sales, $20M depreciation, $20M capital spending,
    # $500M equity, $300M debt, 1B shares at $50... market value $50B is silly on purpose
    # for a clean P/E of 500 -> valuation must be refused.
    company(db, 1, years, [100e6] * 10, [1e9] * 10, 20e6, 20e6, 500e6, 300e6)
    a = metrics.analyze(db, 1)
    c = {x["name"]: x for x in a["checks"]}
    assert c["Steady earnings"]["pass"] is True
    assert abs(a["years"][0]["roc_pct"] - 12.5) < 1e-9, a["years"][0]          # 100 / (500 + 300)
    assert c["High return on capital"]["pass"] is True
    assert c["Little debt"]["pass"] is True and "3.0 years" in c["Little debt"]["value"]
    assert abs(a["years"][0]["net_margin_pct"] - 10.0) < 1e-9
    assert a["years"][0]["owner_earnings"] == 100e6                           # 100 + 20 - 20
    assert c["Growing profit"]["pass"] is False                               # flat
    assert a["value"] is None and any("do not fit" in f for f in a["flags"])  # P/E 500 refused

    # Company 2: same business priced at $1 a share -> market value $1B, P/E 10.
    company(db, 2, years, [100e6] * 10, [1e9] * 10, 20e6, 20e6, 500e6, 300e6, price=1.0)
    v = metrics.analyze(db, 2)["value"]
    assert v["pe"] == 10.0 and v["owner_earnings_yield_pct"] == 10.0
    # no-growth value by hand: 100M a year for 10 years at 10%, plus terminal 100M*1.025/(0.10-0.025)
    ann = sum(100e6 / 1.1 ** t for t in range(1, 11))
    term = 100e6 * 1.025 / 0.075 / 1.1 ** 10
    want = (ann + term) / 1e9
    got = v["scenarios"]["no growth"]["value_per_share"]
    assert abs(got - round(want, 2)) < 0.011, (got, want)
    assert v["scenarios"]["no growth"]["price_to_value"] == round(1e9 / (ann + term), 2)

    # Company 3: one loss year, debt 8 years of profit, profit doubling -> specific fails/passes.
    ni = [50e6, 55e6, -10e6, 60e6, 70e6, 80e6, 90e6, 100e6, 110e6, 120e6]
    company(db, 3, years, ni, [1e9] * 10, 10e6, 60e6, 400e6, 880e6, price=2.0)
    a = metrics.analyze(db, 3)
    c = {x["name"]: x for x in a["checks"]}
    assert c["Steady earnings"]["pass"] is False and "9 of 10" in c["Steady earnings"]["value"]
    assert c["Little debt"]["pass"] is False and "8.0 years" in c["Little debt"]["value"]   # 880 / avg(100,110,120)
    assert c["Profit turns into cash for owners"]["pass"] is False             # capex 60 vs dep 10
    # growth: avg first 3 = 31.67, avg last 3 = 110, over 9 - 2 = 7 years
    g = (110 / (95 / 3)) ** (1 / 7) - 1
    assert ("%.1f%%" % (100 * g)) in c["Growing profit"]["value"], (c["Growing profit"]["value"], g)

    # Company 4: debt never reported -> counted as none, not as unknown.
    company(db, 4, years, [100e6] * 10, [1e9] * 10, 20e6, 20e6, 500e6, None, price=1.0)
    c = {x["name"]: x for x in metrics.analyze(db, 4)["checks"]}
    assert c["Little debt"]["pass"] is True and abs(metrics.analyze(db, 4)["years"][0]["roc_pct"] - 20.0) < 1e-9

    # Quiz grading tolerances.
    q = {"kind": "num", "answer": 47.94, "tol": 0.15}
    assert study.grade(q, "48") and study.grade(q, "$41") and not study.grade(q, "40") and not study.grade(q, "")
    assert study.grade({"kind": "exact", "answer": 0, "tol": 0}, "0") and not study.grade({"kind": "exact", "answer": 0, "tol": 0}, "1")
    assert study.grade({"kind": "pts", "answer": 20.6, "tol": 3}, "18%") and not study.grade({"kind": "pts", "answer": 20.6, "tol": 3}, "25")
    assert study.grade({"kind": "choice", "answer": "treasury", "tol": 0}, "Treasury") and not study.grade({"kind": "choice", "answer": "treasury", "tol": 0}, "stock")

    # The gate: no holding without a passed case.
    assert "error" in study.add_holding(db, {"cik": 2, "shares": 10, "cost": 1})
    r = study.save(db, 2, {"answers": {}, "quiz": {}})
    assert r["status"] == "studying" and len(r["missing"]) == 4
    a2 = metrics.analyze(db, 2)
    full = {"answers": {k: "x" * study.MIN_CHARS for k in study.WRITTEN_KEYS},
            "quiz": {q["key"]: q["answer"] for q in study.quiz_for(a2)}, "my_value": 2, "buy_below": 1.2, "read_report": True}
    assert study.save(db, 2, dict(full, buy_below=3))["status"] == "studying"   # no margin of safety
    assert study.save(db, 2, full)["status"] == "understood"
    assert study.add_holding(db, {"cik": 2, "shares": 1000, "cost": 0.9}) == {"ok": True}
    p = study.portfolio(db)
    assert p["invested"] == 1000.0 and p["positions"][0]["weight_pct"] == 100.0
    # look-through: 1000 shares of 1B = one millionth of $100M owner earnings = $100
    assert abs(p["lookthrough_owner_earnings"] - 100.0) < 1e-6 and p["lookthrough_yield_pct"] == 10.0
    # ---- hold and sell review ----
    base = {"failing": [], "latest_profit": 100e6, "latest_owner_earnings": 100e6, "latest_roc_pct": 12.5, "roc_pct": 12.5,
            "latest_margin_pct": 10.0, "net_margin_pct": 10.0, "debt_years": 3.0, "latest_end": "2025-12-31"}
    pos = {"price": 1.0, "my_value": 2.0, "buy_below": 1.2, "paid": 900.0, "shares": 1000.0}
    r = study.review({"summary": dict(base)}, dict(base), pos, {"sell": "S", "drop": "D"})
    assert r["kind"] == "hold" and r["your_sell_rule"] == "S" and any("buy-below" in n for n in r["notes"])
    # price halves, business unchanged -> still hold, and it says a lower price is not a reason
    r = study.review({"summary": dict(base)}, dict(base), dict(pos, price=0.45, buy_below=0.4), {})
    assert r["kind"] == "hold" and r["change_pct"] == -50.0 and any("not a reason to sell" in n for n in r["notes"])
    # price doubles, business unchanged, still under 1.5x value -> hold a winner
    r = study.review({"summary": dict(base)}, dict(base), dict(pos, price=1.8), {})
    assert r["kind"] == "hold" and any("up 100%" in n for n in r["notes"])
    # price at 1.5x own value -> the one price-based review
    assert study.review({"summary": dict(base)}, dict(base), dict(pos, price=3.0), {})["kind"] == "pricey"
    # business weakens: a loss year and a check newly failing -> re-examine, whatever the price did
    worse = dict(base, latest_profit=-5e6, failing=["Steady earnings"])
    for price in (0.5, 3.0):
        r = study.review({"summary": worse}, dict(base), dict(pos, price=price), {})
        assert r["kind"] == "weak" and len(r["weakened"]) == 2
    # a check that was already failing when the case was written is not "newly" failing
    r = study.review({"summary": dict(base, failing=["Growing profit"])}, dict(base, failing=["Growing profit"]), pos, {})
    assert r["kind"] == "hold"
    # new annual report, nothing weaker -> reread
    assert study.review({"summary": dict(base, latest_end="2026-12-31")}, dict(base), pos, {})["kind"] == "reread"
    # journal: needs a reason, flags price-only reasons, selling removes the holding
    assert "error" in study.log_decision(db, {"cik": 2, "action": "hold", "reason_kind": "holding", "reason": "short"})
    w = study.log_decision(db, {"cik": 2, "action": "sold", "reason_kind": "price_fell", "reason": "x" * 60})
    assert w["ok"] and "not one of the reasons" in w["warning"]
    assert db.execute("SELECT shares, sold_price FROM u.holding WHERE sold_date IS NOT NULL").fetchall() == [(1000.0, 1.0)]
    # partial sale splits a lot and never loses shares
    study.add_holding(db, {"cik": 2, "shares": 100, "cost": 1.0, "bought": "2026-02-01"})
    assert "error" in study.log_decision(db, {"cik": 2, "action": "trimmed", "reason_kind": "needed_cash", "reason": "x" * 60, "shares_sold": 500})
    assert study.log_decision(db, {"cik": 2, "action": "trimmed", "reason_kind": "needed_cash", "reason": "x" * 60, "shares_sold": 40, "sale_price": 1.5})["ok"]
    assert db.execute("SELECT SUM(shares) FROM u.holding WHERE cik=2 AND sold_date IS NULL").fetchone()[0] == 60.0
    assert db.execute("SELECT shares, sold_price FROM u.holding WHERE sold_price=1.5").fetchall() == [(40.0, 1.5)]
    # index comparison arithmetic on made-up prices: stock 10 -> 12 plus $0.50 dividend; index 100 -> 105 plus $1
    D = 86400; t0 = benchmark.ts_of("2026-01-05")
    h = {"t": [t0, t0 + 30 * D], "c": [10.0, 12.0], "div": [(t0 + 10 * D, 0.5)]}
    ix = {"t": [t0, t0 + 30 * D], "c": [100.0, 105.0], "div": [(t0 + 10 * D, 1.0)]}
    assert benchmark.close_on(h, t0 + 5 * D) == 10.0 and benchmark.divs_between(h, t0, t0 + 30 * D) == 0.5
    paid = 50 * 10.0; units = paid / benchmark.close_on(ix, t0)
    yours = 50 * 12.0 + 50 * benchmark.divs_between(h, t0, t0 + 30 * D)
    index = units * 105.0 + units * benchmark.divs_between(ix, t0, t0 + 30 * D)
    assert (yours, index) == (625.0, 530.0)
    db.execute("DELETE FROM u.holding")
    assert study.portfolio(db)["positions"] == [] and len(study.portfolio(db)["journal"]) == 2
    print("all value-screen checks passed")


if __name__ == "__main__":
    main()
