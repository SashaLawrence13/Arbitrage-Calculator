"""Local dashboard: http://127.0.0.1:<port>/  (this Mac only)."""
import json
import os
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import model, store

HERE = os.path.dirname(os.path.abspath(__file__))
CALC = os.path.join(os.path.dirname(HERE), "Arbitrage Calculator.html")


def state(db):
    def rows(q, a=()):
        return [dict(r) for r in db.execute(q, a).fetchall()]
    opps = rows("SELECT * FROM opps WHERE status='open' ORDER BY profit DESC LIMIT 100")
    past = rows("SELECT * FROM opps WHERE status!='open' ORDER BY last_ts DESC LIMIT 40")
    for o in opps + past:
        o["legs"] = json.loads(o["legs"])
    pairs = rows("""SELECT * FROM pairs WHERE status='candidate' ORDER BY score DESC LIMIT 60""")
    confirmed = db.execute("SELECT COUNT(*) c FROM pairs WHERE status='confirmed'").fetchone()["c"]
    run = model.latest(db)
    paper = db.execute(
        """SELECT COUNT(*) n, SUM(result IS NOT NULL) settled, SUM(result='won') won,
                  COALESCE(SUM(pnl),0) pnl, COALESCE(SUM(contracts*(price+fee)),0) staked
           FROM paper WHERE tag=?""", (model.FROZEN_TAG,)).fetchone()
    status = store.get_meta(db, "status", {})
    counts = status.get("counts") or {"markets": 0, "quotes": 0, "resolved": 0}
    plays = {"has_key": False}
    try:
        with open(os.path.join(DATA["dir"], "plays.json")) as f:
            plays = json.load(f)
    except (OSError, ValueError):
        pass
    return {"plays": plays, "opps": opps, "past": past, "pairs": pairs, "confirmed_pairs": confirmed,
            "model": dict(run) if run else None, "paper": dict(paper), "counts": counts,
            "status": status, "features": model.FEATURES,
            "min_test": model.MIN_TEST}


STOCKS = {"ts": 0, "data": None}


def stocks_screen():
    """The value screen, recomputed at most every 10 minutes."""
    import time
    try:
        from value import data as vdata, metrics
    except ImportError:
        return {"error": "value module not installed", "rows": []}
    if STOCKS["data"] is None or time.time() - STOCKS["ts"] > 600:
        db = vdata.connect()
        try:
            out = metrics.screen(db)
            r = db.execute("SELECT v FROM meta WHERE k='refreshed'").fetchone()
            out["refreshed"] = json.loads(r[0]) if r else None
            STOCKS["data"], STOCKS["ts"] = out, time.time()
        finally:
            db.close()
    return STOCKS["data"]


def stock_detail(cik):
    from value import data as vdata, metrics
    if not str(cik).isdigit():
        return None
    db = vdata.connect()
    try:
        t = db.execute("SELECT v FROM meta WHERE k='treasury10'").fetchone()
        return metrics.analyze(db, int(cik), json.loads(t[0]) if t else None)
    finally:
        db.close()


def stock_post(path, body):
    from value import study
    db = study.connect()
    try:
        if path == "/api/thesis":
            return study.save(db, int(body["cik"]), body)
        if path == "/api/holding":
            return study.add_holding(db, body)
        if path == "/api/decision":
            return study.log_decision(db, body)
        if path == "/api/holding/delete":
            db.execute("DELETE FROM u.holding WHERE id=?", (int(body["id"]),))
            db.commit()
            return {"ok": True}
        db.execute("INSERT OR REPLACE INTO u.settings VALUES('cash', ?)", (json.dumps(max(float(body["cash"]), 0.0)),))
        db.commit()
        return {"ok": True}
    finally:
        db.close()


DATA = {"dir": ""}
SPORTS = {"mtime": 0, "data": {"ts": 0, "rows": []}}


def hedge_search(data_dir, q):
    path = os.path.join(data_dir, "sports.json")
    try:
        mt = os.path.getmtime(path)
        if mt != SPORTS["mtime"]:
            with open(path) as f:
                SPORTS["data"] = json.load(f)
            SPORTS["mtime"] = mt
    except (OSError, ValueError):
        pass
    words = [w for w in q.lower().split() if w][:6]
    out = []
    if words:
        for r in SPORTS["data"]["rows"]:
            text = (r["event"] + " " + r["title"] + " " + r["on"]).lower()
            if all(w in text for w in words):
                out.append(r)
    out.sort(key=lambda r: (r["close_ts"], r["event"], r["platform"]))
    return {"ts": SPORTS["data"]["ts"], "rows": out[:60], "total": len(out)}


def serve(cfg):
    data_dir = DATA["dir"] = cfg["data_dir"]

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def send(self, code, body, ctype="application/json"):
            if not isinstance(body, bytes):
                body = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype + "; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/":
                with open(os.path.join(HERE, "dashboard.html"), "rb") as f:
                    return self.send(200, f.read(), "text/html")
            if path == "/calculator" and os.path.exists(CALC):
                with open(CALC, "rb") as f:
                    return self.send(200, f.read(), "text/html")
            if path == "/stocks":
                with open(os.path.join(HERE, "stocks.html"), "rb") as f:
                    return self.send(200, f.read(), "text/html")
            if path == "/api/stocks":
                return self.send(200, json.dumps(stocks_screen()))
            if path == "/portfolio":
                with open(os.path.join(HERE, "portfolio.html"), "rb") as f:
                    return self.send(200, f.read(), "text/html")
            if path == "/api/digest":
                from value import study, digest
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("cik", ["0"])[0]
                sdb = study.connect()
                try:
                    return self.send(200, json.dumps(digest.get(sdb, int(q)) if q.isdigit() else None))
                finally:
                    sdb.close()
            if path in ("/api/thesis", "/api/portfolio"):
                from value import study
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("cik", ["0"])[0]
                sdb = study.connect()
                try:
                    out = study.portfolio(sdb) if path == "/api/portfolio" else (
                        study.get(sdb, int(q)) if q.isdigit() else None)
                    return self.send(200, json.dumps(out))
                finally:
                    sdb.close()
            if path == "/api/stock":
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("cik", ["0"])[0]
                return self.send(200, json.dumps(stock_detail(q)))
            if path == "/api/hedge":
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("q", [""])[0]
                return self.send(200, json.dumps(hedge_search(data_dir, q[:80])))
            if path == "/api/state":
                db = store.connect(data_dir, init=False)
                try:
                    return self.send(200, json.dumps(state(db)))
                finally:
                    db.close()
            self.send(404, "{}")

        def do_POST(self):
            if "application/json" not in (self.headers.get("Content-Type") or ""):
                return self.send(404, "{}")
            if self.path in ("/api/thesis", "/api/holding", "/api/holding/delete", "/api/cash", "/api/decision"):
                try:
                    body = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length") or 0), 400000)))
                    return self.send(200, json.dumps(stock_post(self.path, body)))
                except Exception as e:
                    return self.send(400, json.dumps({"error": repr(e)[:200]}))
            if self.path != "/api/pair":
                return self.send(404, "{}")
            try:
                d = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
                pid, status = int(d["id"]), d["status"]
                assert status in ("confirmed", "rejected", "candidate")
                inverted = 1 if d.get("inverted") else 0
            except Exception:
                return self.send(400, "{}")
            db = store.connect(data_dir)
            try:
                db.execute("UPDATE pairs SET status=?, inverted=? WHERE id=?", (status, inverted, pid))
                db.commit()
            finally:
                db.close()
            self.send(200, '{"ok":true}')

    srv = ThreadingHTTPServer(("127.0.0.1", cfg["port"]), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv
