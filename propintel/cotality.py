"""Cotality (CoreLogic) Home Value Index — free capital-city market performance.

Cotality publishes the daily/monthly Home Value Index as an open, no-auth JSON feed
(the one that powers their public indices page and the ASX-listed daily index). It gives
per-capital-city GROWTH RATES — monthly and yearly, split house vs unit — plus a daily
index time-series. These are percentages, not dollars, so they sit naturally alongside
the site's growth-fundamentals view.

DISPLAY-ONLY, like the Valuer-General overlay: this is macro capital-city context, not a
per-SA2 signal, so it is never fed into the composite score and cannot move the daily
change-signature. Degrades to {} on any failure — never shows stale/partial numbers.

Source: https://www.cotality.com/au/our-data/indices  (feed: au-indices.cotality.com/asx.json)
"""
from __future__ import annotations

from curl_cffi import requests as cf

FEED = "https://au-indices.cotality.com/asx.json"
SOURCE = "Cotality (CoreLogic) Home Value Index"
SOURCE_URL = "https://www.cotality.com/au/our-data/indices"

# The 8 capital cities in a sensible display order. The feed also carries a
# "Brisbane (inc Gold Coast)" variant and a "5 capital city aggregate"; we take the
# standalone capital rows for the per-city table and surface the aggregate separately.
_CAPITAL_ORDER = ["Sydney", "Melbourne", "Brisbane", "Adelaide", "Perth",
                  "Hobart", "Darwin", "Canberra"]
_AGGREGATE = "5 capital city aggregate"


def _num(v):
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return None


def _city(row: dict) -> dict:
    return {
        "name": row.get("location"),
        "all_yr": _num(row.get("allPercentChangeYear")),
        "all_mo": _num(row.get("allPercentChangeMonth")),
        "house_yr": _num(row.get("housePercentChangeYear")),
        "house_mo": _num(row.get("housePercentChangeMonth")),
        "unit_yr": _num(row.get("unitPercentChangeYear")),
        "unit_mo": _num(row.get("unitPercentChangeMonth")),
    }


def pull_hvi() -> dict:
    """Fetch the Cotality HVI. Returns {} on any failure (feed/format change, network)."""
    try:
        d = cf.get(FEED, impersonate="chrome", timeout=25).json()
    except Exception:
        return {}
    monthly = d.get("monthly") or []
    if not monthly:
        return {}
    by_name = {r.get("location"): r for r in monthly}

    cities = [_city(by_name[c]) for c in _CAPITAL_ORDER if c in by_name]
    if not cities:
        return {}
    agg = _city(by_name[_AGGREGATE]) if _AGGREGATE in by_name else None

    return {
        "asof": d.get("monthName"),          # e.g. "31 August 2026"
        "source": SOURCE,
        "url": SOURCE_URL,
        "cities": cities,
        "aggregate": agg,
    }


if __name__ == "__main__":
    hvi = pull_hvi()
    if not hvi:
        print("Cotality HVI: no data")
    else:
        print(f"Cotality HVI as of {hvi['asof']}")
        print(f"  {'city':12} {'all yr%':>8} {'house yr%':>10} {'unit yr%':>9} {'all mo%':>8}")
        for c in hvi["cities"]:
            print(f"  {c['name']:12} {c['all_yr']:>8} {c['house_yr']:>10} {c['unit_yr']:>9} {c['all_mo']:>8}")
        if hvi.get("aggregate"):
            a = hvi["aggregate"]
            print(f"  {'AGGREGATE':12} {a['all_yr']:>8} {a['house_yr']:>10} {a['unit_yr']:>9} {a['all_mo']:>8}")
