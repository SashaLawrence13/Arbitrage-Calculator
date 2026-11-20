"""Sportsbook lines (The Odds API) and what to do with them.

Needs "odds_api_key" in config.json. The free plan is 500 requests a month, so lines are
pulled every "odds_hours" hours per sport, not every sweep. Lines can be hours old: every
play listed here must be re-checked on the sportsbook before acting.

Three kinds of play:
  bonus   best use of a stake-not-returned bonus bet at each book, hedged on Kalshi,
          Polymarket or another book. Shown as the share of the bonus turned into cash.
  arb     both sides covered for a locked-in profit (book vs book, or book vs exchange).
  value   a book's price beats the no-vig consensus of all books by 2% or more. This one
          is not guaranteed; it only pays off on average over many bets.
"""
import json
import os
import statistics
import time
import urllib.parse
import urllib.request

from . import feeds, match

API = "https://api.the-odds-api.com/v4/sports/%s/odds"
DEFAULT_SPORTS = ["americanfootball_nfl", "americanfootball_ncaaf", "basketball_nba",
                  "basketball_ncaab", "icehockey_nhl"]


def fetch(cfg, now=None):
    """Pull h2h lines if they are due. Returns the stored data (possibly old) or None."""
    path = os.path.join(cfg["data_dir"], "books.json")
    data = None
    if os.path.exists(path):
        try:
            with open(path) as f:
                data = json.load(f)
        except ValueError:
            data = None
    key = cfg.get("odds_api_key")
    now = now or int(time.time())
    if not key or (data and now - data["ts"] < cfg.get("odds_hours", 12) * 3600):
        return data
    events, remaining = [], None
    for sport in cfg.get("odds_sports", DEFAULT_SPORTS):
        url = API % sport + "?" + urllib.parse.urlencode(
            {"regions": "us", "markets": "h2h", "oddsFormat": "decimal", "apiKey": key})
        try:
            with urllib.request.urlopen(urllib.request.Request(
                    url, headers={"User-Agent": "arb-scanner/0.1"}), timeout=30) as r:
                remaining = r.headers.get("x-requests-remaining")
                events += json.loads(r.read())
        except OSError:
            continue
    if not events:
        return data
    data = {"ts": now, "remaining": remaining, "events": events}
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)
    return data


def lines(event):
    """{team: [(book, decimal_odds), ...]} for two-way games only (no draw line)."""
    out = {}
    for b in event.get("bookmakers") or []:
        for m in b.get("markets") or []:
            if m.get("key") != "h2h" or len(m.get("outcomes") or []) != 2:
                continue
            for o in m["outcomes"]:
                if o.get("price") and o["price"] > 1:
                    out.setdefault(o["name"], []).append((b.get("title") or b.get("key"), float(o["price"])))
    return out if len(out) == 2 else {}


def exchange_hedges(event, teams, sports_rows):
    """Exchange rows that hedge a bet on each team: {team: [(site, unit_cost, row), ...]}."""
    start = feeds.iso(event.get("commence_time"))
    tt = {t: set(match.toks(t)) for t in teams}
    out = {t: [] for t in teams}
    a, b = teams
    for r in sports_rows:
        if start and abs(r["close_ts"] - start) > 2 * 86400:
            continue
        text = set(match.toks(r["event"] + " " + r["title"]))
        if not (text & tt[a]) or not (text & tt[b]):
            continue
        if match.kind(r["event"] + " " + r["title"] + " " + r["on"]):
            continue                                   # spreads, totals, halves: not a win bet
        on = set(match.toks(r["on"]))
        sa, sb = match.jaccard(on, tt[a]), match.jaccard(on, tt[b])
        if sa == sb or max(sa, sb) < 0.2:
            continue
        team = a if sa > sb else b
        cost = r["price"] + r["rate"] * r["price"] * (1 - r["price"])
        out[team].append(("Kalshi" if r["platform"] == "kalshi" else "Polymarket", cost, r))
    return out


def analyze(data, sports_rows, top=25):
    plays = {"bonus": [], "arb": [], "value": []}
    if not data:
        return plays
    for ev in data["events"]:
        ln = lines(ev)
        if not ln:
            continue
        teams = list(ln)
        ex = exchange_hedges(ev, teams, sports_rows)
        game = "%s at %s" % (ev.get("away_team"), ev.get("home_team"))
        fair = {}
        for t in teams:                                # no-vig consensus across books
            other = teams[1] if t == teams[0] else teams[0]
            probs = []
            for book, d in ln[t]:
                d2 = dict(ln[other]).get(book)
                if d2:
                    probs.append((1 / d) / (1 / d + 1 / d2))
            fair[t] = statistics.median(probs) if len(probs) >= 3 else None
        for t in teams:
            other = teams[1] if t == teams[0] else teams[0]
            # ways to cover "t loses": back the other team at a book, or buy the opposite side
            covers = [(book, 1 / d, "bet %s at %s" % (other, round(d, 2))) for book, d in ln[other]]
            covers += [(site, cost, "%s at %.1fc (%s)" % (r["hedge"], r["price"] * 100, r["on"]))
                       for site, cost, r in ex[t]]
            for book, d in ln[t]:
                best = min((c for c in covers if c[0] != book), key=lambda c: c[1], default=None)
                if best:
                    base = {"game": game, "start": ev.get("commence_time"), "book": book, "team": t,
                            "odds": d, "hedge_site": best[0], "hedge": best[2]}
                    plays["bonus"].append(dict(base, conversion=(d - 1) * (1 - best[1])))
                    if 1 / d + best[1] < 0.995:
                        plays["arb"].append(dict(base, roi=1 / (1 / d + best[1]) - 1))
                if fair[t] and fair[t] * d - 1 >= 0.02:
                    plays["value"].append({"game": game, "start": ev.get("commence_time"), "book": book,
                                           "team": t, "odds": d, "fair": fair[t], "edge": fair[t] * d - 1})
    best_by_book = {}
    for p in sorted(plays["bonus"], key=lambda p: -p["conversion"]):
        best_by_book.setdefault(p["book"], [])
        if len(best_by_book[p["book"]]) < 3:
            best_by_book[p["book"]].append(p)
    plays["bonus"] = [p for ps in best_by_book.values() for p in ps]
    plays["bonus"].sort(key=lambda p: -p["conversion"])
    plays["arb"] = sorted(plays["arb"], key=lambda p: -p["roi"])[:top]
    plays["value"] = sorted(plays["value"], key=lambda p: -p["edge"])[:top]
    return plays
