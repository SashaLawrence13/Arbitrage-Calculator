"""Fee and stake math, ported from computePred() / minWholeContracts() in the calculator.

Both Kalshi and Polymarket charge a taker fee of  rate x contracts x P x (1 - P).
Kalshi rounds each order's fee up to the next cent; Polymarket does not.
"""
import math


def fee_per_contract(price, rate):
    return rate * price * (1 - price)


def order_fee(price, rate, contracts, platform):
    fee = rate * contracts * price * (1 - price)
    if platform == "kalshi" and rate > 0:
        return math.ceil(fee * 100 - 1e-9) / 100
    return fee


def unit_cost(legs):
    """Cost of one contract on every leg, fees included (before rounding)."""
    return sum(l["price"] + fee_per_contract(l["price"], l["rate"]) for l in legs)


def size_basket(legs, payout_units, bankroll, pm_min_order=5):
    """Size a basket where `payout_units` dollars per contract are guaranteed.

    legs: dicts with price, rate, platform and size (contracts available at that
    price, or None when unknown). Returns None when there is no profit.
    """
    uc = unit_cost(legs)
    if uc <= 0 or uc >= payout_units:
        return None
    contracts = math.floor(bankroll / uc)
    known = [l["size"] for l in legs if l.get("size")]
    if known:
        contracts = min(contracts, math.floor(min(known)))
    if contracts < 1:
        return None
    if contracts < pm_min_order and any(l["platform"] == "polymarket" for l in legs):
        return None
    cost = sum(contracts * l["price"] + order_fee(l["price"], l["rate"], contracts, l["platform"])
               for l in legs)
    payout = contracts * payout_units
    profit = payout - cost
    if profit <= 0:
        return None
    return {"contracts": contracts, "cost": round(cost, 4), "payout": round(payout, 4),
            "profit": round(profit, 4), "roi": profit / cost, "unit_cost": uc,
            "size_known": len(known) == len(legs)}
