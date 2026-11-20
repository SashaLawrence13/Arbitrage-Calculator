"""Phone push through ntfy. The topic name in config.json acts as the password:
anyone who knows it can read the alerts, so it is generated randomly and kept local."""
import urllib.request


def push(cfg, title, body, url=None, priority="default"):
    topic = cfg.get("ntfy_topic")
    if not topic or not cfg.get("alerts_enabled"):
        return False
    headers = {"Title": title.encode("ascii", "replace").decode(), "Priority": priority,
               "Tags": "moneybag"}
    if url:
        headers["Click"] = url
    req = urllib.request.Request(cfg.get("ntfy_server", "https://ntfy.sh") + "/" + topic,
                                 data=body.encode("utf-8"), headers=headers, method="POST")
    try:
        urllib.request.urlopen(req, timeout=15).read()
        return True
    except OSError:
        return False


def describe(v):
    lines = []
    for l in v["legs"]:
        site = "Kalshi" if l["platform"] == "kalshi" else "Polymarket"
        size = "" if not l.get("size") else " (%d available)" % l["size"]
        lines.append("%s %s at %.1fc on %s: %s%s" % ("Buy", l["side"].upper(), l["price"] * 100,
                                                   site, l["label"], size))
    days = "" if v["days"] is None else ", settles in about %.0f days" % v["days"]
    head = "%d contracts each, cost $%.2f, locked-in profit $%.2f (%.1f%%)%s" % (
        v["contracts"], v["cost"], v["profit"], v["roi"] * 100, days)
    return "\n".join([head] + lines + ([v["note"]] if v["note"] else []))


def send_opportunity(cfg, v):
    kind = {"single": "Same-market arb", "no_set": "NO-set arb", "yes_set": "YES-set (check)",
            "cross": "Cross-site arb"}[v["kind"]]
    if v["kind"] == "cross" and not v.get("confirmed", True):
        kind = "Possible cross-site arb (unconfirmed)"
    title = "%s: $%.2f on %s" % (kind, v["profit"], v["title"][:60])
    return push(cfg, title, describe(v), url=v["legs"][0]["url"],
                priority="high" if v["profit"] >= 5 * cfg["min_profit"] else "default")
