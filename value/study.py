"""The ownership gate and the portfolio.

Rule: nothing goes into the portfolio until its owner has shown, in writing, that he
understands the business. Two parts.

  Facts quiz    numbers from the company's filings, graded by the program. You answer
                them by reading the filings (or the year-by-year table). 80% to pass.
  Written case  the questions an owner should be able to answer about the business, its
                moat, its managers, its price, and how it could go wrong. The program
                cannot judge whether a written answer is right. It only refuses blanks
                and one-liners, and keeps the answers so they can be reread a year later.

A company can also be put on the "too hard" pile, which is a perfectly good outcome.
"""
import json
import os
import time

from . import benchmark, data, metrics

MIN_CHARS = 120            # shortest written answer the gate accepts
QUIZ_PASS = 0.8
STALE_DAYS = 365

# Your answers, holdings and journal live in their own file, apart from the downloaded
# company data, so a data refresh can never lock or overwrite them. Back this file up.
USER_DB = os.path.expanduser("~/arb-scanner/data/portfolio.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS u.thesis(cik INTEGER PRIMARY KEY, status TEXT, answers TEXT, quiz TEXT,
  quiz_score REAL, my_value REAL, buy_below REAL, read_report INTEGER, updated INTEGER, snapshot TEXT);
CREATE TABLE IF NOT EXISTS u.journal(id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, cik INTEGER,
  ticker TEXT, action TEXT, reason_kind TEXT, reason TEXT, price REAL, verdict TEXT);
CREATE TABLE IF NOT EXISTS u.holding(id INTEGER PRIMARY KEY AUTOINCREMENT, cik INTEGER, shares REAL,
  cost REAL, bought TEXT, note TEXT, sold_date TEXT, sold_price REAL);
CREATE TABLE IF NOT EXISTS u.settings(k TEXT PRIMARY KEY, v TEXT);
"""

WRITTEN = [
    ("Circle of competence", [
        ("business", "In plain words: what does this company sell, who buys it, and why do they pay for it?"),
        ("economics", "How does a dollar of sales turn into profit here? Name the two or three biggest costs and what drives them."),
        ("ten_years", "What will this business most likely look like in ten years? What has to stay true for that to happen?"),
        ("whole_business", "If you could buy the entire company at today's market value and were never allowed to sell it, would you? Why?"),
    ]),
    ("The moat", [
        ("moat", "What stops a rich, determined competitor from taking its customers? Be specific: brand, lowest cost, switching costs, network, scale, regulation."),
        ("pricing", "Could it raise prices 10% next year without losing much business? What is your evidence?"),
        ("moat_trend", "Is that protection getting stronger or weaker? What changed in the last five years?"),
    ]),
    ("Management", [
        ("managers", "Who runs it, for how long, and how much of the stock do they own themselves?"),
        ("capital", "What did they do with the cash over the last ten years (reinvest, buy companies, buy back stock, pay dividends), and was each use sensible at the price paid?"),
        ("candor", "Find one place in the latest annual report or letter where management admits a mistake or a real problem. Quote or describe it. If you cannot find one, what does that tell you?"),
    ]),
    ("Financial strength", [
        ("survive", "Could it get through two bad years in a row without help? Use the debt, cash and margin numbers."),
        ("real_profit", "Is the reported profit real cash? Explain any gap between profit and owner earnings."),
        ("capital_light", "To grow sales 10%, how much new money does it have to put into plants, stores, inventory or acquisitions? Does growth produce cash or eat it?"),
        ("inflation", "Costs rise 5% a year for five years. What happens to this business and its profit?"),
    ]),
    ("Price", [
        ("valuation", "What is the whole company worth, roughly, and how did you get there? State the growth you assumed and why it is conservative."),
        ("vs_alternatives", "Why is this a better use of the money than a 10-year Treasury, or than adding to something you already own?"),
    ]),
    ("How it goes wrong", [
        ("risks", "The three most likely ways this investment loses money for good (not just a price drop)."),
        ("sell", "What facts would make you sell? Name them now, before you own it."),
        ("drop", "The price falls 50% next year and nothing in the business has changed. What do you do, and why?"),
        ("other_side", "Someone is selling you these shares. What might they know or believe that you do not?"),
    ]),
]
WRITTEN_KEYS = [k for _, qs in WRITTEN for k, _ in qs]


def connect(user_db=None):
    db = data.connect()
    db.execute("ATTACH ? AS u", (user_db or USER_DB,))
    db.executescript(SCHEMA)
    if "sold_date" not in [r[1] for r in db.execute("PRAGMA u.table_info(holding)")]:
        db.execute("ALTER TABLE u.holding ADD COLUMN sold_date TEXT")
        db.execute("ALTER TABLE u.holding ADD COLUMN sold_price REAL")
    return db


# --------------------------------------------------------------------------------------
# When to hold and when to sell.
#
# Berkshire's letters give few reasons to sell, and none of them is "the price moved":
#   1. the business got worse (its economics or its moat),
#   2. the original judgement turns out to have been wrong,
#   3. the price went far above any sensible value,
#   4. the money has a clearly better use.
# A falling price with an intact business is not a reason to sell; a rising price with an
# intact business is not a reason to sell either. So each holding is reviewed against the
# business facts recorded when its case was written, and against the owner's own value.
# The program raises points to review and shows the sell rules the owner wrote in advance.
# It does not decide.
# --------------------------------------------------------------------------------------
FAR_ABOVE = 1.5            # price at 1.5x your own value counts as "far above"
REASONS = {
    "business_worse": ("The business got worse", True),
    "was_wrong": ("I was wrong about it", True),
    "far_above_value": ("Price went far above my value", True),
    "better_use": ("Found a clearly better use for the money", True),
    "needed_cash": ("I needed the cash", True),
    "price_fell": ("The price fell", False),
    "price_rose": ("The price rose and I wanted to lock it in", False),
    "holding": ("Holding: nothing has changed", True),
    "adding": ("Adding: price is at or below my buy price and the case stands", True),
}


def review(a, snap, pos, answers):
    """Points to review for one holding. a = today's analysis, snap = the summary saved with the case."""
    cur, weak, notes = a.get("summary") or {}, [], []
    snap = snap or {}
    newly = [n for n in cur.get("failing", []) if n not in snap.get("failing", [])]
    if newly and snap:
        weak.append("Now failing, and was passing when you wrote your case: " + ", ".join(newly) + ".")
    if cur.get("latest_profit") is not None and cur["latest_profit"] <= 0:
        weak.append("It lost money in its latest fiscal year.")
    if cur.get("latest_owner_earnings") is not None and cur["latest_owner_earnings"] <= 0:
        weak.append("Owner earnings were negative in its latest fiscal year.")
    if cur.get("latest_roc_pct") is not None and cur.get("roc_pct") and cur["latest_roc_pct"] < 0.67 * cur["roc_pct"]:
        weak.append("Return on capital last year (%.1f%%) was well under its ten-year average (%.1f%%)." % (cur["latest_roc_pct"], cur["roc_pct"]))
    if cur.get("latest_margin_pct") is not None and cur.get("net_margin_pct") and cur["latest_margin_pct"] < 0.7 * cur["net_margin_pct"]:
        weak.append("Net margin last year (%.1f%%) was well under its average (%.1f%%)." % (cur["latest_margin_pct"], cur["net_margin_pct"]))
    if snap.get("roc_pct") and cur.get("roc_pct") is not None and cur["roc_pct"] < snap["roc_pct"] - 3:
        weak.append("Average return on capital has dropped from %.1f%% to %.1f%% since you wrote your case." % (snap["roc_pct"], cur["roc_pct"]))
    if cur.get("debt_years") is not None and cur["debt_years"] > 5 and (snap.get("debt_years") is None or snap["debt_years"] <= 5):
        weak.append("Debt is now %.1f years of profit." % cur["debt_years"])
    new_report = bool(snap.get("latest_end") and cur.get("latest_end") and cur["latest_end"] > snap["latest_end"])
    price, value, buy = pos.get("price"), pos.get("my_value"), pos.get("buy_below")
    avg = pos["paid"] / pos["shares"] if pos.get("shares") else None
    change = (price / avg - 1) if price and avg else None

    if weak:
        verdict, kind = "Re-examine: the business has weakened", "weak"
        notes.append("This is the main reason Berkshire gives for selling. It applies the same whether you are up or down on the stock. Decide whether the damage is temporary or permanent, and write down why.")
    elif price and value and price >= FAR_ABOVE * value:
        verdict, kind = "Review: price is far above your own value", "pricey"
        notes.append("At $%.2f it trades at %.1f times the $%.2f you said it is worth. Either raise your value, with reasons, or accept that the price has left the business behind. Selling a winner also means paying tax on the gain." % (price, price / value, value))
    elif new_report:
        verdict, kind = "Reread: a new annual report is out since you wrote your case", "reread"
        notes.append("Nothing in the numbers has weakened. Read the new report against your written case and save it again.")
    else:
        verdict, kind = "Hold: the business is intact by these measures", "hold"
        if change is not None and change <= -0.25:
            notes.append("The price is %.0f%% below what you paid, but nothing in the filings has weakened. A lower price on the same business is not a reason to sell." % (-100 * change))
        elif change is not None and change >= 0.5:
            notes.append("It is up %.0f%% and the business is intact. No rule here says sell; Berkshire's stated preference is to keep holding a good business." % (100 * change))
        if price and buy and price <= buy:
            notes.append("The price is at or below your own buy-below price of $%.2f." % buy)
    return {"verdict": verdict, "kind": kind, "weakened": weak, "notes": notes,
            "your_sell_rule": (answers or {}).get("sell") or "", "your_drop_rule": (answers or {}).get("drop") or "",
            "change_pct": None if change is None else round(100 * change, 1)}


def log_decision(db, body):
    kind = str(body.get("reason_kind") or "")
    reason = str(body.get("reason") or "").strip()
    if kind not in REASONS:
        return {"error": "pick a reason"}
    if len(reason) < 60:
        return {"error": "Write at least 60 characters on why. This is the note you will reread later."}
    cik = int(body["cik"])
    c = db.execute("SELECT ticker, price FROM company WHERE cik=?", (cik,)).fetchone() or (None, None)
    db.execute("INSERT INTO u.journal(ts,cik,ticker,action,reason_kind,reason,price,verdict) VALUES(?,?,?,?,?,?,?,?)",
               (int(time.time()), cik, c[0], str(body.get("action") or "hold")[:12], kind, reason[:3000], c[1],
                str(body.get("verdict") or "")[:120]))
    action = body.get("action")
    if action in ("sold", "trimmed"):
        try:
            sale = float(body.get("sale_price") or c[1])
        except (TypeError, ValueError):
            db.rollback()                           # nothing is journaled for a rejected sale
            return {"error": "enter the price you sold at"}
        open_lots = db.execute("SELECT id, shares, cost, bought, note FROM u.holding WHERE cik=? AND sold_date IS NULL ORDER BY bought, id",
                               (cik,)).fetchall()
        left = sum(l[1] for l in open_lots)
        if action == "trimmed":
            try:
                left = float(body["shares_sold"])
            except (KeyError, TypeError, ValueError):
                db.rollback()                           # nothing is journaled for a rejected sale
                return {"error": "enter how many shares you sold"}
            if left <= 0 or left > sum(l[1] for l in open_lots) + 1e-9:
                db.rollback()                           # nothing is journaled for a rejected sale
                return {"error": "that is more shares than the record shows you own"}
        day = str(body.get("sold_date") or time.strftime("%Y-%m-%d"))[:10]
        for lid, shares, cost, bought, note in open_lots:
            if left <= 1e-9:
                break
            take = min(shares, left)
            if take < shares - 1e-9:                    # split the lot: part sold, part kept
                db.execute("UPDATE u.holding SET shares=? WHERE id=?", (shares - take, lid))
                db.execute("INSERT INTO u.holding(cik,shares,cost,bought,note,sold_date,sold_price) VALUES(?,?,?,?,?,?,?)",
                           (cik, take, cost, bought, note, day, sale))
            else:
                db.execute("UPDATE u.holding SET sold_date=?, sold_price=? WHERE id=?", (day, sale, lid))
            left -= take
    db.commit()
    return {"ok": True, "warning": None if REASONS[kind][1] else
            "Recorded. Note that a price move on its own is not one of the reasons Berkshire gives for selling."}


def quiz_for(a):
    """Facts questions with graded answers, built from the company's own numbers."""
    yrs = a["years"]
    last = yrs[-1]
    qs = []

    def add(key, text, answer, kind, tol):
        if answer is not None:
            qs.append({"key": key, "text": text, "answer": answer, "kind": kind, "tol": tol})

    b = lambda v: None if v is None else round(v / 1e9, 2)
    fy = "the fiscal year that ended %s" % last["end"] if last.get("end") else "fiscal %d" % last["year"]
    add("sales", "Sales in %s, in billions of dollars" % fy, b(last["revenue"]), "num", 0.15)
    add("profit", "Net profit in %s, in billions of dollars" % fy, b(last["net_income"]), "num", 0.15)
    nm = [y["net_margin_pct"] for y in yrs if y["net_margin_pct"] is not None]
    add("margin", "Average net profit margin over these %d years, in percent" % len(yrs),
        round(sum(nm) / len(nm), 1) if nm else None, "pts", 3)
    add("losses", "In how many of the last %d fiscal years did it lose money?" % len(yrs),
        sum(1 for y in yrs if y["net_income"] <= 0), "exact", 0)
    debts = [y for y in yrs if y["debt"] is not None]
    if debts:
        add("debt", "Long-term debt on %s, in billions of dollars" % (debts[-1].get("end") or debts[-1]["year"]),
            b(debts[-1]["debt"]), "num", 0.2)
    roc = [y["roc_pct"] for y in yrs if y["roc_pct"] is not None]
    add("roc", "Average yearly profit as a percent of equity plus long-term debt",
        round(sum(roc) / len(roc), 1) if len(roc) >= 5 else None, "pts", 4)
    oe = [(y["owner_earnings"], y["net_income"]) for y in yrs if y["owner_earnings"] is not None]
    if len(oe) >= 5 and sum(n for _, n in oe) > 0:
        add("owner", "Of each $1 of reported profit over the period, how many cents were owner earnings (profit plus depreciation minus capital spending)?",
            round(100 * sum(o for o, _ in oe) / sum(n for _, n in oe)), "pts", 15)
    v = a.get("value") or {}
    add("pe", "Price-to-earnings ratio today (market value divided by average profit of the last three years)",
        v.get("pe"), "num", 0.15)
    if v.get("owner_earnings_yield_pct") is not None and v.get("treasury_pct"):
        add("vs_treasury", "Which pays more per dollar today: this company's owner earnings, or a 10-year Treasury? (answer 'stock' or 'treasury')",
            "stock" if v["owner_earnings_yield_pct"] > v["treasury_pct"] else "treasury", "choice", 0)
    return qs


def grade(q, given):
    if given is None or str(given).strip() == "":
        return False
    if q["kind"] == "choice":
        return str(given).strip().lower().startswith(q["answer"][:3])
    try:
        g = float(str(given).replace(",", "").replace("$", "").replace("%", ""))
    except ValueError:
        return False
    if q["kind"] == "exact":
        return g == q["answer"]
    if q["kind"] == "pts":
        return abs(g - q["answer"]) <= q["tol"]
    return abs(g - q["answer"]) <= q["tol"] * max(abs(q["answer"]), 0.01)


def report_link(db, cik):
    """Link to the latest annual report on the SEC site (looked up once, then remembered)."""
    r = db.execute("SELECT v FROM u.settings WHERE k=?", ("report:%d" % cik,)).fetchone()
    if r:
        return r[0]
    url = None
    try:
        d = (data.get("%s/submissions/CIK%010d.json" % (data.SEC, cik)) or {}).get("filings", {}).get("recent", {})
        for i, form in enumerate(d.get("form", [])):
            if form in ("10-K", "20-F", "40-F"):
                acc = d["accessionNumber"][i].replace("-", "")
                url = "https://www.sec.gov/Archives/edgar/data/%d/%s/%s" % (cik, acc, d["primaryDocument"][i])
                break
    except Exception:
        pass
    if url:
        db.execute("INSERT OR REPLACE INTO u.settings VALUES(?,?)", ("report:%d" % cik, url))
        db.commit()
    return url


def treasury(db):
    t = db.execute("SELECT v FROM meta WHERE k='treasury10'").fetchone()
    return json.loads(t[0]) if t else None


def get(db, cik):
    a = metrics.analyze(db, cik, treasury(db))
    if not a:
        return None
    row = db.execute("SELECT status, answers, quiz, quiz_score, my_value, buy_below, read_report, updated FROM u.thesis WHERE cik=?",
                     (cik,)).fetchone()
    saved = {"status": "not started", "answers": {}, "quiz": {}, "quiz_score": None, "my_value": None,
             "buy_below": None, "read_report": 0, "updated": None}
    if row:
        saved = {"status": row[0], "answers": json.loads(row[1] or "{}"), "quiz": json.loads(row[2] or "{}"),
                 "quiz_score": row[3], "my_value": row[4], "buy_below": row[5], "read_report": row[6], "updated": row[7]}
    return {"cik": cik, "name": a["name"], "ticker": a["ticker"], "price": a["price"],
            "report_url": report_link(db, cik), "min_chars": MIN_CHARS,
            "written": [{"section": s, "questions": [{"key": k, "text": t} for k, t in qs]} for s, qs in WRITTEN],
            "quiz": [{"key": q["key"], "text": q["text"]} for q in quiz_for(a)], "saved": saved}


def save(db, cik, body):
    """Store answers, grade the quiz, and decide the status. Returns what is still missing."""
    a = metrics.analyze(db, cik, treasury(db))
    if not a:
        return {"error": "unknown company"}
    answers = {k: str((body.get("answers") or {}).get(k, ""))[:6000] for k in WRITTEN_KEYS}
    given = body.get("quiz") or {}
    qs = quiz_for(a)
    marks = {q["key"]: grade(q, given.get(q["key"])) for q in qs}
    answered = sum(1 for q in qs if str(given.get(q["key"], "")).strip() != "")
    score = sum(marks.values()) / len(qs) if qs else 0.0

    def num(x):
        try:
            return float(x) if x not in (None, "") else None
        except (TypeError, ValueError):
            return None
    my_value, buy_below, read = num(body.get("my_value")), num(body.get("buy_below")), 1 if body.get("read_report") else 0

    missing = []
    short = [k for k in WRITTEN_KEYS if len(answers[k].strip()) < MIN_CHARS]
    if short:
        missing.append("%d written answers are blank or under %d characters" % (len(short), MIN_CHARS))
    if score < QUIZ_PASS:
        missing.append("facts quiz: %d of %d right, %d needed" % (sum(marks.values()), len(qs),
                                                                  -(-len(qs) * 8 // 10)))
    if not read:
        missing.append("the latest annual report is not ticked as read")
    if not my_value or not buy_below:
        missing.append("your own value per share and your buy-below price are not filled in")
    elif buy_below > my_value:
        missing.append("your buy-below price is above your own value, which leaves no margin of safety")

    if body.get("too_hard"):
        status = "too hard"
    elif not missing:
        status = "understood"
    else:
        status = "studying"
    db.execute("""INSERT INTO u.thesis(cik,status,answers,quiz,quiz_score,my_value,buy_below,read_report,updated,snapshot)
                  VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(cik) DO UPDATE SET status=excluded.status,
                  answers=excluded.answers, quiz=excluded.quiz, quiz_score=excluded.quiz_score,
                  my_value=excluded.my_value, buy_below=excluded.buy_below, read_report=excluded.read_report,
                  updated=excluded.updated, snapshot=excluded.snapshot""",
               (cik, status, json.dumps(answers), json.dumps({k: str(v)[:40] for k, v in given.items()}), score,
                my_value, buy_below, read, int(time.time()), json.dumps(a["summary"])))
    db.commit()
    # Wrong quiz answers are marked wrong without revealing the number: look it up in the filing.
    return {"status": status, "missing": missing, "short": short,
            "quiz_marks": {k: v for k, v in marks.items() if str(given.get(k, "")).strip() != ""},
            "quiz_answered": answered, "quiz_total": len(qs)}


def add_holding(db, body):
    try:
        cik, shares, cost = int(body["cik"]), float(body["shares"]), float(body["cost"])
    except (KeyError, TypeError, ValueError):
        return {"error": "need a company, a share count and a cost per share"}
    if shares <= 0 or cost < 0:
        return {"error": "shares must be above zero"}
    t = db.execute("SELECT status FROM u.thesis WHERE cik=?", (cik,)).fetchone()
    if not t or t[0] != "understood":
        return {"error": "This company has not passed the ownership questions yet. Finish them first."}
    db.execute("INSERT INTO u.holding(cik, shares, cost, bought, note) VALUES(?,?,?,?,?)",
               (cik, shares, cost, str(body.get("bought") or time.strftime("%Y-%m-%d"))[:10], str(body.get("note") or "")[:300]))
    db.commit()
    return {"ok": True}


def portfolio(db):
    cash = json.loads((db.execute("SELECT v FROM u.settings WHERE k='cash'").fetchone() or ["0"])[0])
    tre = treasury(db)
    positions, by_cik = [], {}
    for hid, cik, shares, cost, bought, note in db.execute(
            "SELECT id, cik, shares, cost, bought, note FROM u.holding WHERE sold_date IS NULL ORDER BY id"):
        p = by_cik.setdefault(cik, {"cik": cik, "lots": [], "shares": 0.0, "paid": 0.0})
        p["lots"].append({"id": hid, "shares": shares, "cost": cost, "bought": bought, "note": note})
        p["shares"] += shares
        p["paid"] += shares * cost
    now = time.time()
    for cik, p in by_cik.items():
        a = metrics.analyze(db, cik, tre) or {}
        th = db.execute("SELECT status, my_value, buy_below, updated, snapshot, answers FROM u.thesis WHERE cik=?", (cik,)).fetchone()
        price = a.get("price")
        p.update(name=a.get("name"), ticker=a.get("ticker"), price=price, score=a.get("score"), of=a.get("of"),
                 value=p["shares"] * price if price else None,
                 my_value=th[1] if th else None, buy_below=th[2] if th else None,
                 thesis_age_days=int((now - th[3]) / 86400) if th and th[3] else None)
        # look-through: this holding's share of the company's profit and owner earnings
        yrs = a.get("years") or []
        cap = a.get("market_cap")
        own = p["value"] / cap if p["value"] and cap else None
        last3 = yrs[-3:]
        ni = sum(y["net_income"] for y in last3) / len(last3) if last3 else None
        oes = [y["owner_earnings"] for y in last3 if y["owner_earnings"] is not None]
        p["lookthrough_profit"] = own * ni if own is not None and ni is not None else None
        p["lookthrough_owner_earnings"] = own * sum(oes) / len(oes) if own is not None and oes else None
        p["oe_yield_pct"] = (a.get("value") or {}).get("owner_earnings_yield_pct")
        p["review"] = review(a, json.loads(th[4]) if th and th[4] else None, p,
                             json.loads(th[5]) if th and th[5] else {}) if a else None
        positions.append(p)
    invested = sum(p["value"] or 0 for p in positions)
    total = invested + cash
    for p in positions:
        p["weight_pct"] = round(100 * (p["value"] or 0) / total, 1) if total else None
    positions.sort(key=lambda p: -(p["value"] or 0))
    lt = [p["lookthrough_owner_earnings"] for p in positions if p["lookthrough_owner_earnings"] is not None]
    notes = []
    if positions:
        top = positions[0]
        if (top["weight_pct"] or 0) > 40:
            notes.append("%s is %.0f%% of the portfolio. Berkshire has held positions that large, but only in businesses it knew cold." % (top["ticker"], top["weight_pct"]))
        if len(positions) > 20:
            notes.append("%d positions. Berkshire's equity portfolio keeps most of its money in a handful of names; past about 20, each one gets less of your attention." % len(positions))
        for p in positions:
            if p["thesis_age_days"] is not None and p["thesis_age_days"] > STALE_DAYS:
                notes.append("%s: your written case is %d days old. Reread it against the latest annual report." % (p["ticker"], p["thesis_age_days"]))
            if p["my_value"] and p["price"] and p["price"] > p["my_value"]:
                notes.append("%s trades at $%.2f, above your own value of $%.2f. Either your value has gone up or the price has run ahead of it; decide which." % (p["ticker"], p["price"], p["my_value"]))
    watch = []
    for cik, status, my_value, buy_below in db.execute("SELECT cik, status, my_value, buy_below FROM u.thesis WHERE status='understood'"):
        c = db.execute("SELECT name, ticker, price FROM company WHERE cik=?", (cik,)).fetchone()
        if c:
            watch.append({"cik": cik, "name": c[0], "ticker": c[1], "price": c[2], "my_value": my_value,
                          "buy_below": buy_below, "owned": cik in by_cik,
                          "at_or_below": bool(c[2] and buy_below and c[2] <= buy_below)})
    # the fourth reason: a clearly better use for the money, among companies already cleared
    for w in watch:
        if w["owned"] or not w["at_or_below"]:
            continue
        wa = metrics.analyze(db, w["cik"], tre) or {}
        wy = (wa.get("value") or {}).get("owner_earnings_yield_pct")
        w["oe_yield_pct"] = wy
        for p in positions:
            if wy and p.get("oe_yield_pct") and wy >= 1.5 * p["oe_yield_pct"] and p.get("review"):
                p["review"]["notes"].append("%s, which you have cleared and which is at your buy price, earns %.1f%% on its price against %.1f%% here. A clearly better use of the money is one of the few reasons to switch." % (w["ticker"], wy, p["oe_yield_pct"]))
    journal = [dict(zip(("ts", "ticker", "action", "reason_kind", "reason", "price", "verdict"), r)) for r in
               db.execute("SELECT ts, ticker, action, reason_kind, reason, price, verdict FROM u.journal ORDER BY ts DESC LIMIT 100")]
    for j in journal:
        j["reason_label"], j["sound"] = REASONS.get(j["reason_kind"], (j["reason_kind"], True))
    piles = {s: n for s, n in db.execute("SELECT status, COUNT(*) FROM u.thesis GROUP BY status")}
    return {"cash": cash, "invested": invested, "total": total, "positions": positions,
            "lookthrough_owner_earnings": sum(lt) if lt else None,
            "lookthrough_yield_pct": round(100 * sum(lt) / invested, 2) if lt and invested else None,
            "vs_index": benchmark.compare(db),
            "treasury": tre, "notes": notes, "watch": watch, "piles": piles, "journal": journal,
            "reasons": [{"key": k, "label": v[0]} for k, v in REASONS.items()]}
