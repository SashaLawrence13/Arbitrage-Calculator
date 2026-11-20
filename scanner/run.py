"""Run the scanner:   python3 -m scanner.run          (loops forever)
                     python3 -m scanner.run --once   (one sweep, then exit)
"""
import json
import os
import pickle
import secrets
import subprocess
import sys
import time
import traceback

from . import alerts, books, feeds, match, model, scan, store, web

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "config.json")
DEFAULTS = {
    "data_dir": "~/arb-scanner/data",   # internal disk, outside iCloud, so auto-start can reach it
    "poll_seconds": 60,                 # pause between sweeps (a sweep itself takes ~2 min)
    "pm_max_events": 100000,            # Polymarket events per sweep, busiest first
    "bankroll_per_arb": 200,            # most dollars to put into one opportunity
    "min_profit": 1.00,                 # ignore anything locking in less than this
    "min_roi": 0.01,                    # ...or returning less than 1% on the money tied up
    "max_roi": 0.15,                    # ...or looking too good to be true (bad match or stale data)
    "max_days": 120,                    # ...or tying money up longer than this
    "max_legs": 12,
    "keep_days": 7,                     # days of price history and settled markets to keep
    "verify_per_sweep": 25,
    "alerts_enabled": True,
    "ntfy_server": "https://ntfy.sh",
    "ntfy_topic": "",
    "odds_api_key": "",                 # from the-odds-api.com; blank = sportsbook plays off
    "odds_hours": 12,                   # hours between sportsbook pulls (free plan: 500 a month)
    "odds_sports": books.DEFAULT_SPORTS,
    "port": 8787,
}


def load_config():
    cfg = dict(DEFAULTS)
    if os.path.exists(CONFIG):
        with open(CONFIG) as f:
            cfg.update(json.load(f))
    if not cfg["ntfy_topic"]:
        cfg["ntfy_topic"] = "arb-" + secrets.token_urlsafe(12)
    with open(CONFIG, "w") as f:
        json.dump(cfg, f, indent=2)
    cfg["data_dir"] = os.path.expanduser(cfg["data_dir"])
    return cfg


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def resolve(db, now):
    """Look up results for markets that have left the open feeds."""
    rows = db.execute(
        """SELECT id FROM markets WHERE result IS NULL AND last_seen < ?
           ORDER BY COALESCE(checked_ts, 0), close_ts LIMIT 4000""", (now - 3 * 3600,)).fetchall()
    db.executemany("UPDATE markets SET checked_ts=? WHERE id=?", [(now, r["id"]) for r in rows])
    k = [r["id"][2:] for r in rows if r["id"].startswith("K:")]
    p = [r["id"][2:] for r in rows if r["id"].startswith("P:")]
    res = {}
    if k:
        res.update(feeds.k_results(k))
    if p:
        res.update(feeds.p_results(p))
    for mid, result in res.items():
        db.execute("UPDATE markets SET result=?, resolved_ts=? WHERE id=?", (result, now, mid))
    # stop re-asking about markets that vanished without a result (voided, delisted)
    db.execute("""UPDATE markets SET result='unknown', resolved_ts=? WHERE result IS NULL
                  AND last_seen < ?""", (now, now - 14 * 86400))
    db.commit()
    return len(res)


PROPS = set("""yards assists rebounds ladder halftime goals receptions touchdowns strikeouts hits
shots saves threes passing rushing receiving kills aces sacks steals blocks turnovers runs
scorer anytime double triple props player mvp""".split())


def sports_snapshot(cfg, markets, now):
    """Sports markets settling within 10 days, as hedge options for sportsbook bonus bets."""
    rows = []
    for m in markets.values():
        cat = (m["category"] or "").lower()
        if not cat.startswith("sports") or not m["close_ts"] or m["close_ts"] - now > 10 * 86400:
            continue
        text = m["event_title"] + " " + m["title"] + " " + m["outcome"]
        if match.kind(text) or PROPS & set(match.WORD.findall(text.lower())):
            continue                                   # win markets only: no spreads, totals, props
        base = {"platform": m["platform"], "event": m["event_title"], "title": m["title"],
                "rate": m["fee_rate"], "close_ts": m["close_ts"], "url": m["url"]}
        names = m["extra"].get("outcomes")
        if names and names[0].lower() != "yes":        # Polymarket team-vs-team market
            if m["no_ask"]:
                rows.append(dict(base, on=names[0], hedge="Buy " + names[1], price=m["no_ask"], size=None))
            if m["yes_ask"]:
                rows.append(dict(base, on=names[1], hedge="Buy " + names[0], price=m["yes_ask"], size=None))
        elif m["no_ask"]:
            rows.append(dict(base, on=m["outcome"] or m["title"], hedge="Buy NO", price=m["no_ask"],
                             size=m["no_ask_size"]))
    tmp = os.path.join(cfg["data_dir"], "sports.json.tmp")
    with open(tmp, "w") as f:
        json.dump({"ts": now, "rows": rows}, f)
    os.replace(tmp, os.path.join(cfg["data_dir"], "sports.json"))
    return rows


def seed_quotes(cfg, mem):
    """After a restart, remember the last saved quotes so unchanged prices are not re-saved."""
    try:
        with open(os.path.join(cfg["data_dir"], "last_quotes.pkl"), "rb") as f:
            mem["quotes"] = pickle.load(f)
    except (OSError, pickle.PickleError, EOFError):
        pass


def save_quotes(cfg, mem):
    tmp = os.path.join(cfg["data_dir"], "last_quotes.pkl.tmp")
    with open(tmp, "wb") as f:
        pickle.dump(mem["quotes"], f, protocol=4)
    os.replace(tmp, os.path.join(cfg["data_dir"], "last_quotes.pkl"))


def trim(db, cfg, now):
    """Keep the database small: drop price history and settled markets older than keep_days.
    Markets with a paper bet or a reviewed pair are kept."""
    cut = now - cfg["keep_days"] * 86400
    q = db.execute("DELETE FROM quotes WHERE ts < ?", (cut,)).rowcount
    m = db.execute("""DELETE FROM markets WHERE result IS NOT NULL AND resolved_ts < ?
                      AND id NOT IN (SELECT market_id FROM paper)
                      AND id NOT IN (SELECT a_id FROM pairs WHERE status != 'candidate')
                      AND id NOT IN (SELECT b_id FROM pairs WHERE status != 'candidate')""",
                   (cut,)).rowcount
    db.execute("DELETE FROM opps WHERE status != 'open' AND last_ts < ?", (now - 30 * 86400,))
    db.commit()
    log("trim: removed %d old quotes and %d old settled markets" % (q, m))


def sweep(db, cfg, mem):
    t0 = time.time()
    kalshi = feeds.kalshi_markets()
    t1 = time.time()
    poly = feeds.polymarket_markets(cfg["pm_max_events"])
    t2 = time.time()
    now = int(t2)
    written = store.save_snapshot(db, kalshi + poly, mem["quotes"], now, mem["seen"])
    save_quotes(cfg, mem)
    markets = {m["id"]: m for m in kalshi + poly}
    sports_rows = sports_snapshot(cfg, markets, now)
    try:
        book_data = books.fetch(cfg, now)
        plays = books.analyze(book_data, sports_rows)
        plays["lines_ts"] = book_data["ts"] if book_data else None
        plays["remaining"] = book_data.get("remaining") if book_data else None
        plays["has_key"] = bool(cfg.get("odds_api_key"))
        with open(os.path.join(cfg["data_dir"], "plays.json"), "w") as f:
            json.dump(plays, f)
    except Exception:
        log("sportsbook step failed:\n" + traceback.format_exc())

    if now - mem["matched"] > 1800:
        known = {(r["a_id"], r["b_id"]) for r in db.execute("SELECT a_id, b_id FROM pairs")}
        new = match.find_pairs(kalshi, poly, known)
        match.save_pairs(db, new)
        mem["matched"] = now
        log("matching: %d new candidate pairs" % len(new))

    pairs = db.execute("SELECT id, a_id, b_id, inverted, status FROM pairs").fetchall()
    cands = scan.find(markets, pairs, cfg)
    verified, tried = [], 0
    for c in cands:
        if tried >= cfg["verify_per_sweep"]:
            break
        if mem["cooldown"].get(c["key"], 0) > now:
            continue
        tried += 1
        try:
            v = scan.verify(db, c, markets, cfg)
        except Exception as e:
            v = None
            log("verify failed:", c["key"][:80], repr(e)[:120])
        if v:
            verified.append(v)
        else:
            mem["cooldown"][c["key"]] = now + 600
    fresh = scan.record(db, verified, now)
    # Phone alerts only for opportunities that do not hinge on an unreviewed match.
    # YES sets stay on the dashboard too: they need a person to check the outcome list.
    sent = sum(1 for v in fresh if v.get("confirmed", True) and v["kind"] != "yes_set"
               and alerts.send_opportunity(cfg, v))

    resolved = 0
    if now - mem["resolved"] > 3600:
        resolved = resolve(db, now)
        mem["resolved"] = now
        model.settle_paper(db)
    if now - mem["trimmed"] > 86400:
        trim(db, cfg, now)
        mem["trimmed"] = now
    if now - mem["stocks"] > 86400:
        mem["stocks"] = now
        mem["stock_runs"] += 1
        args = [] if mem["stock_runs"] % 7 == 0 else ["prices"]      # full filings refresh weekly
        try:
            with open(os.path.join(cfg["data_dir"], "value.log"), "a") as lf:
                subprocess.Popen([sys.executable, "-u", "-m", "value.data"] + args, stdout=lf, stderr=lf,
                                 cwd=os.path.dirname(HERE))
        except OSError as e:
            log("stock refresh not started:", repr(e)[:80])
    if now - mem["trained"] > 86400:
        model.train(db)
        mem["trained"] = now
    picks = model.paper_picks(db, markets, now)

    status = {"ts": now, "kalshi_markets": len(kalshi), "poly_markets": len(poly),
              "kalshi_seconds": round(t1 - t0, 1), "poly_seconds": round(t2 - t1, 1),
              "quotes_written": written, "candidates": len(cands), "checked": tried,
              "verified": len(verified), "alerts_sent": sent, "resolved": resolved,
              "paper_picks": picks, "sweep_seconds": round(time.time() - t0, 1)}
    status["counts"] = {
        "markets": len(mem["seen"]),
        "quotes": db.execute("SELECT MAX(rowid) FROM quotes").fetchone()[0] or 0,
        "resolved": db.execute("SELECT COUNT(*) FROM markets WHERE result IN ('yes','no')").fetchone()[0]}
    store.set_meta(db, "status", status)
    db.commit()
    log(json.dumps(status))
    return status


def main():
    cfg = load_config()
    db = store.connect(cfg["data_dir"])
    mem = {"stocks": time.time() - 43200, "stock_runs": 0, "quotes": {}, "seen": {}, "trimmed": time.time() - 82800, "cooldown": {}, "matched": 0, "resolved": time.time(), "trained": 0}
    seed_quotes(cfg, mem)
    once = "--once" in sys.argv
    if not once:
        web.serve(cfg)
        log("dashboard: http://127.0.0.1:%d/" % cfg["port"])
    while True:
        try:
            sweep(db, cfg, mem)
        except Exception:
            log("sweep failed:\n" + traceback.format_exc())
        if once:
            break
        time.sleep(cfg["poll_seconds"])


if __name__ == "__main__":
    main()
