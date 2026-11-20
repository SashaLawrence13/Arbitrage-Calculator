"""Public market data from Kalshi and Polymarket. Read-only, no account, no keys."""
import calendar
import gzip
import json
import time
import urllib.error
import urllib.parse
import urllib.request

K = "https://api.elections.kalshi.com/trade-api/v2"
G = "https://gamma-api.polymarket.com"
C = "https://clob.polymarket.com"
KALSHI_BASE_RATE = 0.07


def get(url, params=None, tries=4):
    if params:
        url += "?" + urllib.parse.urlencode(params, doseq=True)
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "arb-scanner/0.1",
                                                       "Accept": "application/json",
                                                       "Accept-Encoding": "gzip"})
            with urllib.request.urlopen(req, timeout=30) as r:
                body = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    body = gzip.decompress(body)
                return json.loads(body)
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and i < tries - 1:
                time.sleep(2.0 * (i + 1))
                continue
            raise
        except (OSError, ValueError):
            if i < tries - 1:
                time.sleep(2.0 * (i + 1))
                continue
            raise


def px(v):
    """A tradable price strictly between 0 and 1, else None."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if 0 < f < 1 else None


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def iso(s):
    if not s:
        return None
    try:
        return calendar.timegm(time.strptime(s[:19], "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        try:
            return calendar.timegm(time.strptime(s[:10], "%Y-%m-%d"))
        except ValueError:
            return None


# ---------------------------------------------------------------- Kalshi

def k_market(ev, m):
    series = ev.get("series_ticker") or ""
    return {
        "id": "K:" + m["ticker"], "platform": "kalshi",
        "event_id": "K:" + ev["event_ticker"], "event_title": ev.get("title") or "",
        "title": m.get("title") or "", "outcome": m.get("yes_sub_title") or "",
        "rules": m.get("rules_primary") or "",
        "close_ts": iso(m.get("expected_expiration_time") or m.get("close_time")),
        "yes_bid": px(m.get("yes_bid_dollars")), "yes_ask": px(m.get("yes_ask_dollars")),
        "no_bid": px(m.get("no_bid_dollars")), "no_ask": px(m.get("no_ask_dollars")),
        "yes_ask_size": num(m.get("yes_ask_size_fp")),
        "no_ask_size": num(m.get("no_ask_size_fp") or m.get("yes_bid_size_fp")),
        "fee_rate": KALSHI_BASE_RATE, "exclusive": bool(ev.get("mutually_exclusive")),
        "vol24": num(m.get("volume_24h_fp")) or 0.0,
        "category": ev.get("category") or "",
        "url": "https://kalshi.com/markets/" + series.lower(),
        "event_n": len(ev.get("markets") or []),
        "open_ts": iso(m.get("open_time")),
        "extra": {"series": series, "ticker": m["ticker"]},
    }


def kalshi_markets(max_pages=150):
    out, cursor = [], None
    for _ in range(max_pages):
        params = {"limit": 200, "status": "open", "with_nested_markets": "true"}
        if cursor:
            params["cursor"] = cursor
        d = get(K + "/events", params)
        for ev in d.get("events") or []:
            for m in ev.get("markets") or []:
                if m.get("status") == "active" and m.get("market_type", "binary") == "binary":
                    out.append(k_market(ev, m))
        cursor = d.get("cursor")
        if not cursor or not d.get("events"):
            break
        time.sleep(0.08)
    return out


def k_fresh(ticker):
    m = get(K + "/markets/" + ticker)["market"]
    return {"yes": (px(m.get("yes_ask_dollars")), num(m.get("yes_ask_size_fp"))),
            "no": (px(m.get("no_ask_dollars")),
                   num(m.get("no_ask_size_fp") or m.get("yes_bid_size_fp")))}


def k_series_rate(series):
    s = get(K + "/series/" + series)["series"]
    mult = num(s.get("fee_multiplier"))
    return KALSHI_BASE_RATE * (1.0 if mult is None else mult), s.get("fee_type") or ""


def k_results(tickers):
    res = {}
    for i in range(0, len(tickers), 40):
        d = get(K + "/markets", {"tickers": ",".join(tickers[i:i + 40]), "limit": 1000})
        for m in d.get("markets") or []:
            if m.get("result") in ("yes", "no"):
                res["K:" + m["ticker"]] = m["result"]
        time.sleep(0.1)
    return res


# ------------------------------------------------------------ Polymarket

def p_market(ev, m):
    if m.get("closed") or not m.get("active") or not m.get("acceptingOrders") \
            or not m.get("enableOrderBook"):
        return None
    try:
        outcomes = json.loads(m.get("outcomes") or "[]")
        tokens = json.loads(m.get("clobTokenIds") or "[]")
    except ValueError:
        return None
    if len(outcomes) != 2 or len(tokens) != 2:
        return None
    yes_bid, yes_ask = px(m.get("bestBid")), px(m.get("bestAsk"))
    rate = 0.0
    if m.get("feesEnabled"):
        rate = num((m.get("feeSchedule") or {}).get("rate")) or 0.0
    label = m.get("groupItemTitle") or ("" if outcomes[0].lower() == "yes" else outcomes[0])
    return {
        "id": "P:" + str(m["id"]), "platform": "polymarket",
        "event_id": "P:" + str(ev["id"]), "event_title": ev.get("title") or "",
        "title": m.get("question") or "", "outcome": label,
        "rules": m.get("description") or "",
        "close_ts": iso(m.get("endDate") or ev.get("endDate")),
        "yes_bid": yes_bid, "yes_ask": yes_ask,
        "no_bid": (round(1 - yes_ask, 4) if yes_ask else None),
        "no_ask": (round(1 - yes_bid, 4) if yes_bid else None),
        "yes_ask_size": None, "no_ask_size": None,
        "fee_rate": rate, "exclusive": bool(m.get("negRisk") and ev.get("negRisk")),
        "vol24": num(m.get("volume24hr")) or 0.0,
        "category": m.get("feeType") or "",
        "url": "https://polymarket.com/event/" + (ev.get("slug") or ""),
        "event_n": len(ev.get("markets") or []),
        "open_ts": iso(m.get("startDate")),
        "extra": {"tokens": tokens, "outcomes": outcomes,
                  "min_order": num(m.get("orderMinSize")) or 5},
    }


def polymarket_markets(max_events=3000):
    out, seen, cursor = [], 0, None
    while seen < max_events:
        params = {"limit": 100, "closed": "false", "active": "true",
                  "order": "volume24hr", "ascending": "false"}
        if cursor:
            params["after_cursor"] = cursor
        d = get(G + "/events/keyset", params)
        events = d.get("events") or []
        for ev in events:
            for m in ev.get("markets") or []:
                n = p_market(ev, m)
                if n:
                    out.append(n)
        seen += len(events)
        cursor = d.get("next_cursor")
        if not cursor or len(events) < 100:
            break
        time.sleep(0.08)
    return out


def p_book(token):
    """Best ask (price, size) for one outcome token from the live order book."""
    b = get(C + "/book", {"token_id": token})
    asks = [(float(a["price"]), float(a["size"])) for a in b.get("asks") or []]
    if not asks:
        return None, None
    best = min(p for p, _ in asks)
    return (best if 0 < best < 1 else None), sum(s for p, s in asks if abs(p - best) < 1e-9)


def p_results(ids):
    res = {}
    for i in range(0, len(ids), 40):
        d = get(G + "/markets", [("id", x) for x in ids[i:i + 40]] + [("closed", "true"),
                                                                     ("limit", 100)])
        for m in d or []:
            try:
                prices = [float(x) for x in json.loads(m.get("outcomePrices") or "[]")]
            except ValueError:
                continue
            if m.get("closed") and len(prices) == 2 and max(prices) >= 0.99:
                res["P:" + str(m["id"])] = "yes" if prices[0] > prices[1] else "no"
        time.sleep(0.1)
    return res
