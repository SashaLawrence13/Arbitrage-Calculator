"""The self-learning part: does anything predict outcomes better than the market price?

Everything here is fixed in advance so the record cannot be tuned after the fact:
  Sample    one row per resolved market: its quote 24 hours before the scheduled close.
  Target    did it resolve YES.
  Features  logit of the mid price, bid/ask spread, log 24h volume, site, site x logit.
  Split     by time: oldest 70% of resolved markets train, newest 30% test. No shuffling.
  Metric    log loss on the test set, compared with using the market mid price as-is.
  Gate      the model only makes paper picks when the test set has at least MIN_TEST
            markets and its log loss beats the market's. Otherwise it stays off.
  Picks     paper only. A market 20 to 28 hours from close where the model's probability
            beats the ask price plus fee by at least EDGE. One pick per event.
A model that never passes the gate is a real result: it means the price is already right.
"""
import json
import math
import time

from . import feeds

HORIZON = 24 * 3600
MIN_TEST = 300
EDGE = 0.03
PAPER_CONTRACTS = 10
FEATURES = ["intercept", "logit_mid", "spread", "log_vol", "is_poly", "poly_x_logit"]


def logit(p):
    p = min(max(p, 0.005), 0.995)
    return math.log(p / (1 - p))


def sigmoid(z):
    if z < -30:
        return 1e-13
    if z > 30:
        return 1 - 1e-13
    return 1 / (1 + math.exp(-z))


def features(yes_bid, yes_ask, vol24, platform):
    mid = (yes_bid + yes_ask) / 2
    lg = logit(mid)
    poly = 1.0 if platform == "polymarket" else 0.0
    return [1.0, lg, yes_ask - yes_bid, math.log1p(max(vol24 or 0, 0)), poly, poly * lg], mid


def predict(coef, x):
    return sigmoid(sum(c * v for c, v in zip(coef, x)))


def solve(a, b):
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for i in range(n):
        piv = max(range(i, n), key=lambda r: abs(m[r][i]))
        m[i], m[piv] = m[piv], m[i]
        if abs(m[i][i]) < 1e-12:
            return None
        for r in range(n):
            if r != i:
                f = m[r][i] / m[i][i]
                for c in range(i, n + 1):
                    m[r][c] -= f * m[i][c]
    return [m[i][n] / m[i][i] for i in range(n)]


def fit(xs, ys, ridge=1.0, iters=25):
    """Logistic regression by Newton's method with a small ridge penalty."""
    k = len(xs[0])
    w = [0.0, 1.0] + [0.0] * (k - 2)          # start at "the market is right"
    for _ in range(iters):
        g = [0.0] * k
        h = [[0.0] * k for _ in range(k)]
        for x, y in zip(xs, ys):
            p = predict(w, x)
            d, s = p - y, p * (1 - p)
            for i in range(k):
                g[i] += d * x[i]
                for j in range(i, k):
                    h[i][j] += s * x[i] * x[j]
        for i in range(k):
            for j in range(i):
                h[i][j] = h[j][i]
            pen = ridge * (w[i] - (1.0 if i == 1 else 0.0))   # shrink toward the market
            g[i] += pen
            h[i][i] += ridge
        step = solve(h, g)
        if step is None:
            break
        w = [wi - si for wi, si in zip(w, step)]
        if max(abs(s) for s in step) < 1e-7:
            break
    return w


def logloss(ps, ys):
    return -sum(math.log(p if y else 1 - p) for p, y in zip(ps, ys)) / len(ys)


def brier(ps, ys):
    return sum((p - y) ** 2 for p, y in zip(ps, ys)) / len(ys)


def training_rows(db):
    rows = db.execute(
        """SELECT id, platform, close_ts, resolved_ts, first_seen, result FROM markets
           WHERE result IN ('yes','no') AND close_ts IS NOT NULL
             AND first_seen <= close_ts - ? AND resolved_ts > close_ts - ?
           ORDER BY resolved_ts""", (HORIZON, HORIZON)).fetchall()
    out = []
    for r in rows:
        q = db.execute(
            """SELECT yes_bid, yes_ask, vol24 FROM quotes WHERE market_id=? AND ts<=?
               ORDER BY ts DESC LIMIT 1""", (r["id"], r["close_ts"] - HORIZON)).fetchone()
        if not q or q["yes_bid"] is None or q["yes_ask"] is None:
            continue
        x, mid = features(q["yes_bid"], q["yes_ask"], q["vol24"], r["platform"])
        out.append((x, 1 if r["result"] == "yes" else 0, mid))
    return out


def train(db):
    data = training_rows(db)
    now = int(time.time())
    cut = int(len(data) * 0.7)
    tr, te = data[:cut], data[cut:]
    if len(te) < MIN_TEST or len(set(y for _, y, _ in tr)) < 2:
        note = "Waiting for data: %d resolved markets in the test set, %d needed." % (len(te), MIN_TEST)
        db.execute("INSERT INTO model_runs VALUES(?,?,?,?,?,?,?,?,?,?)",
                   (now, len(tr), len(te), None, None, None, None, None, 0, note))
        db.commit()
        return None
    coef = fit([x for x, _, _ in tr], [y for _, y, _ in tr])
    ys = [y for _, y, _ in te]
    pm = [predict(coef, x) for x, _, _ in te]
    pk = [min(max(mid, 0.005), 0.995) for _, _, mid in te]
    llm, llk = logloss(pm, ys), logloss(pk, ys)
    active = int(llm < llk)
    note = ("Model beat the market price on held-out markets; paper picks are on."
            if active else "Model did not beat the market price; paper picks stay off.")
    db.execute("INSERT INTO model_runs VALUES(?,?,?,?,?,?,?,?,?,?)",
               (now, len(tr), len(te), llm, llk, brier(pm, ys), brier(pk, ys),
                json.dumps(coef), active, note))
    db.commit()
    return coef if active else None


def latest(db):
    return db.execute("SELECT * FROM model_runs ORDER BY ts DESC LIMIT 1").fetchone()


# ---- Forward test of the backtested model (see BACKTEST_RESULTS.md) ----
# Coefficients from the 2026-11-18 backtest, fitted on 207,663 settled markets and frozen.
# Order: intercept, log-odds of mid price, polymarket, polymarket x log-odds.
FROZEN = [0.029, 0.87, -0.118, -0.028]
RETIRED = True    # 2026-11-20: failed backtest version 2 (see BACKTEST_RESULTS.md). No more paper bets.
FROZEN_TAG = "frozen_v2"      # v1 bets used unverified feed prices; they do not count
MAX_CHECKS = 30
ENTRY = 24 * 3600          # bet 24 hours after the market opened, as in the backtest
WINDOW = 3600              # a sweep can be late; accept up to an hour after that
MAX_SPREAD = 0.10


def paper_picks(db, markets, now=None):
    """Paper bets with the frozen model, using only what is known at bet time.

    Unlike the backtest there is no lifetime-volume filter (that was hindsight): a market
    qualifies on its live bid/ask gap and on having traded in the last 24 hours.
    """
    if RETIRED:
        return 0
    now, made, checked = now or int(time.time()), 0, 0
    cols = [r[1] for r in db.execute("PRAGMA table_info(paper)")]
    if "tag" not in cols:
        db.execute("ALTER TABLE paper ADD COLUMN tag TEXT")
    taken = {r["event_id"] for r in db.execute("SELECT DISTINCT event_id FROM paper WHERE tag=?",
                                               (FROZEN_TAG,))}
    for m in markets.values():
        o = m.get("open_ts")
        if not o or not (ENTRY <= now - o <= ENTRY + WINDOW) or m["event_id"] in taken:
            continue
        if not (m["yes_bid"] and m["yes_ask"] and m["no_ask"]) or m["vol24"] <= 0:
            continue
        if m["yes_ask"] - m["yes_bid"] > MAX_SPREAD:
            continue
        mid = (m["yes_bid"] + m["yes_ask"]) / 2
        if mid < 0.02 or mid > 0.98:
            continue
        lg = logit(mid)
        poly = 1.0 if m["platform"] == "polymarket" else 0.0
        p = predict(FROZEN, [1.0, lg, poly, poly * lg])
        for side, price, prob in (("yes", m["yes_ask"], p), ("no", m["no_ask"], 1 - p)):
            fee = m["fee_rate"] * price * (1 - price)
            if prob - (price + fee) < EDGE:
                continue
            # The feed price can lag. Only count the bet if the live order book really
            # offers this side, for at least the paper size, at a price that still has the edge.
            if checked >= MAX_CHECKS:
                break
            checked += 1
            try:
                if m["platform"] == "kalshi":
                    price, size = feeds.k_fresh(m["extra"]["ticker"])[side]
                else:
                    price, size = feeds.p_book(m["extra"]["tokens"][0 if side == "yes" else 1])
            except Exception:
                break
            if price is None or (size or 0) < PAPER_CONTRACTS:
                break
            fee = m["fee_rate"] * price * (1 - price)
            if prob - (price + fee) < EDGE:
                break
            db.execute(
                """INSERT INTO paper(ts,market_id,event_id,side,price,fee,contracts,model_p,
                     market_p,tag) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (now, m["id"], m["event_id"], side, price, fee, PAPER_CONTRACTS, p, mid,
                 FROZEN_TAG))
            taken.add(m["event_id"])
            made += 1
            break
    db.commit()
    return made


def settle_paper(db):
    rows = db.execute(
        """SELECT p.id, p.side, p.price, p.fee, p.contracts, m.result FROM paper p
           JOIN markets m ON m.id = p.market_id
           WHERE p.result IS NULL AND m.result IN ('yes','no')""").fetchall()
    for r in rows:
        won = r["side"] == r["result"]
        pnl = r["contracts"] * ((1.0 if won else 0.0) - r["price"] - r["fee"])
        db.execute("UPDATE paper SET result=?, pnl=? WHERE id=?",
                   ("won" if won else "lost", pnl, r["id"]))
    db.commit()
    return len(rows)
