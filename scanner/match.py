"""Find Kalshi and Polymarket markets that look like the same question.

A match here is only a candidate. The two sites can settle "the same" event under
different rules, so a person confirms each pair on the dashboard before it is trusted.
"""
import math
import re
import time
from collections import defaultdict

STOP = set("""a an the of in on at to for by will be is are was who what which when or and
vs v versus win wins winner won market markets before after than this that with from as do
does did yes no not any other it its their his her have has had game match""".split())
WORD = re.compile(r"\d+(?:\.\d+)?|[a-z]+")
ORDINAL = re.compile(r"\b(\d+)(st|nd|rd|th)\b")
HALF = re.compile(r"\b([12])h\b")


def norm(s):
    s = (s or "").lower()
    return HALF.sub(r"\1 half", ORDINAL.sub(r"\1", s))


ALIAS = {"st": "state", "u": "university", "univ": "university"}


def toks(s):
    return [ALIAS.get(t, t) for t in WORD.findall(norm(s))
            if t not in STOP and (len(t) > 1 or t.isdigit())]


TAGS = {"spread": "spread", "handicap": "spread", "total": "total", "totals": "total",
        "over": "total", "under": "total", "half": "half", "1h": "half", "2h": "half",
        "quarter": "quarter", "1q": "quarter", "inning": "inning", "innings": "inning",
        "period": "period", "first": "first", "score": "score", "td": "score",
        "touchdown": "score", "map": "map", "set": "set", "round": "round",
        "points": "points", "margin": "spread", "corners": "corners", "cards": "cards",
        "w": "women", "women": "women", "womens": "women", "wnba": "women", "ladies": "women",
        "nominations": "nominated", "nomination": "nominated", "nominated": "nominated",
        "nominee": "nominated", "nominees": "nominated",
        "draw": "draw", "tie": "draw", "both": "both", "exact": "exact", "method": "method"}


def kind(text):
    """What sort of bet a market is (spread, total, first half...). Pairs must agree."""
    tags = {TAGS[t] for t in WORD.findall(norm(text)) if t in TAGS}
    if "spread" in tags:
        tags -= {"total", "points"}
    return frozenset(tags)


def nums(tokens):
    return frozenset(t for t in tokens if t[0].isdigit())


def alpha(tokens):
    return {t for t in tokens if not t[0].isdigit()}


def group(markets):
    ev = defaultdict(list)
    for m in markets:
        if m["yes_ask"] and m["no_ask"]:
            ev[m["event_id"]].append(m)
    return ev


def jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def find_pairs(kalshi, poly, known, event_thr=0.5, market_thr=0.5, max_days=45):
    """Return new candidate pairs as (kalshi_market, polymarket_market, score)."""
    kev, pev = group(kalshi), group(poly)
    ktok = {e: set(toks(ms[0]["event_title"])) for e, ms in kev.items()}
    ptok = {e: set(toks(ms[0]["event_title"])) for e, ms in pev.items()}
    df = defaultdict(int)
    for d in (ktok, ptok):
        for ts in d.values():
            for t in ts:
                df[t] += 1
    n = len(ktok) + len(ptok) + 1
    idf = {t: math.log(n / c) for t, c in df.items()}
    index = defaultdict(list)
    for e, ts in ptok.items():
        for t in ts:
            if df[t] <= 400:
                index[t].append(e)
    pweight = {e: sum(idf[t] for t in ts) for e, ts in ptok.items()}

    out = []
    for ke, kts in ktok.items():
        if not kts:
            continue
        acc = defaultdict(float)
        for t in kts:
            for pe in index.get(t, ()):
                acc[pe] += idf[t]
        if not acc:
            continue
        kw = sum(idf[t] for t in kts)
        best, best_s = None, 0.0
        for pe, shared in acc.items():
            s = shared / (kw + pweight[pe] - shared)
            if s > best_s:
                best, best_s = pe, s
        if best is None or best_s < event_thr:
            continue
        kms, pms = kev[ke], pev[best]
        kc, pc = kms[0]["close_ts"], pms[0]["close_ts"]
        if kc and pc and abs(kc - pc) > max_days * 86400:
            continue
        ptexts = []
        for pm in pms:
            names = pm["extra"]["outcomes"]
            teams = None
            if names[0].lower() != "yes":          # e.g. ["Duke", "Clemson"]: YES means Duke
                teams = (set(toks(names[0])), set(toks(names[1])))
            ptexts.append((pm, set(toks(pm["title"] + " " + pm["outcome"])), teams,
                           kind(pm["title"] + " " + pm["outcome"])))
        for km in kms:
            kt = set(toks(km["title"] + " " + km["outcome"]))
            ko = set(toks(km["outcome"]))
            kn = nums(kt)
            kk = kind(km["event_title"] + " " + km["title"] + " " + km["outcome"])
            top, top_s, top_inv = None, 0.0, 0
            for pm, pt, teams, pk in ptexts:
                if pk != kk:
                    continue
                if teams:
                    if nums(pt) != kn:
                        continue
                    s0, s1 = jaccard(ko, teams[0]), jaccard(ko, teams[1])
                    if s0 == s1 or max(s0, s1) < 0.3:
                        continue
                    s, inv = max(s0, s1), int(s1 > s0)
                else:
                    if nums(pt) != kn:
                        continue
                    po = alpha(set(toks(pm["outcome"])))
                    if alpha(ko) and po and jaccard(alpha(ko), po) < 0.5:
                        continue                      # different named outcome (person, team)
                    s, inv = (1.0 if (len(kms) == 1 and len(pms) == 1) else jaccard(kt, pt)), 0
                if s > top_s:
                    top, top_s, top_inv = pm, s, inv
            if top is not None and top_s >= market_thr and (km["id"], top["id"]) not in known:
                out.append((km, top, round(min(best_s, top_s), 3), top_inv))
    return out


def save_pairs(db, pairs):
    now = int(time.time())
    for km, pm, score, inverted in pairs:
        db.execute(
            """INSERT OR IGNORE INTO pairs(a_id,b_id,score,inverted,created_ts,a_text,b_text,
                 a_rules,b_rules,a_url,b_url) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (km["id"], pm["id"], score, inverted, now,
             " | ".join(x for x in (km["event_title"], km["title"], km["outcome"]) if x),
             " | ".join(x for x in (pm["event_title"], pm["title"], pm["outcome"]) if x),
             km["rules"][:4000], pm["rules"][:4000], km["url"], pm["url"]))
    db.commit()
