"""Write source data into the database (snapshot semantics)."""
from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from .db import now_iso, state_from_sa2


# --- geography + macro ----------------------------------------------------
def seed_geography(
    conn: sqlite3.Connection, population: dict[str, dict], region_type: str = "SA2"
) -> int:
    ts = now_iso()
    n = 0
    for code, d in population.items():
        # SA4 code = first 3 digits of any SA2/SA3/SA4 code (parent city/region)
        sa4_code = code[:3]
        conn.execute(
            """INSERT INTO geography(region_code, region_type, name, state, sa4_code, updated_at)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(region_code) DO UPDATE SET
                 name=excluded.name, state=excluded.state,
                 region_type=excluded.region_type, sa4_code=excluded.sa4_code,
                 updated_at=excluded.updated_at""",
            (code, region_type, d["name"], state_from_sa2(code), sa4_code, ts),
        )
        n += 1
    conn.commit()
    return n


def write_population(conn: sqlite3.Connection, population: dict[str, dict]) -> int:
    ts = now_iso()
    rows = 0
    for sa2_code, d in population.items():
        period = d["latest_period"]
        conn.execute(
            """INSERT OR REPLACE INTO macro(sa2_code, metric, period, value, source, observed_at)
               VALUES(?,?,?,?,?,?)""",
            (sa2_code, "population", period, d["population"], "ABS:ERP_ASGS2021", ts),
        )
        rows += 1
        if d.get("pop_growth_pct") is not None:
            conn.execute(
                """INSERT OR REPLACE INTO macro(sa2_code, metric, period, value, source, observed_at)
                   VALUES(?,?,?,?,?,?)""",
                (sa2_code, "pop_growth_pct", period, d["pop_growth_pct"], "ABS:ERP_ASGS2021", ts),
            )
            rows += 1
    conn.commit()
    return rows


def write_indicator(
    conn: sqlite3.Connection, data: dict[str, dict], metric: str, source: str
) -> int:
    ts = now_iso()
    rows = 0
    for sa2_code, d in data.items():
        conn.execute(
            """INSERT OR REPLACE INTO macro(sa2_code, metric, period, value, source, observed_at)
               VALUES(?,?,?,?,?,?)""",
            (sa2_code, metric, d.get("latest_period", ""), d.get("value"), source, ts),
        )
        rows += 1
    conn.commit()
    return rows


# --- listings -------------------------------------------------------------
_PRICE_RE = re.compile(r"\$?\s*([\d,]+(?:\.\d+)?)\s*([kKmM]?)")


def parse_price(display: str | None) -> float | None:
    """Best-effort guide price from a free-text Domain display string.

    Handles '$549,000', 'Offers over $520k', '$1.2M', 'Mid $500,000s'.
    Returns None for 'Contact agent' / auction with no guide.
    """
    if not display:
        return None
    best: float | None = None
    for m in _PRICE_RE.finditer(display.replace(",", "")):
        num = float(m.group(1))
        suffix = m.group(2).lower()
        if suffix == "k":
            num *= 1_000
        elif suffix == "m":
            num *= 1_000_000
        # ignore stray small numbers (e.g. bedroom counts) unless clearly a price
        if num < 10_000:
            continue
        best = num if best is None else min(best, num)
    return best


def write_listings(
    conn: sqlite3.Connection, raw_results: list[dict], sa2_code: str | None = None
) -> int:
    """Insert a fresh snapshot row per listing; append price history on change."""
    ts = now_iso()
    rows = 0
    for item in raw_results:
        listing = item.get("listing", item)
        if listing.get("type") == "Project":  # skip project/development groupings
            continue
        pd = listing.get("propertyDetails", {}) or {}
        price_display = (listing.get("priceDetails", {}) or {}).get("displayPrice")
        price_numeric = (listing.get("priceDetails", {}) or {}).get("price") or parse_price(price_display)
        lid = str(listing.get("id"))
        conn.execute(
            """INSERT INTO listings(listing_id, sa2_code, address, suburb, state, postcode,
                 property_type, price_display, price_numeric, bedrooms, bathrooms, parking,
                 land_area, agency, listed_date, listing_url, observed_at, raw_json)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                lid, sa2_code, pd.get("displayableAddress"), pd.get("suburb"),
                pd.get("state"), pd.get("postcode"), pd.get("propertyType"),
                price_display, price_numeric, pd.get("bedrooms"), pd.get("bathrooms"),
                pd.get("carspaces"), pd.get("landArea"),
                (listing.get("advertiser", {}) or {}).get("name"),
                listing.get("dateListed"),
                f"https://www.domain.com.au/{listing.get('listingSlug','')}" if listing.get("listingSlug") else None,
                ts, json.dumps(item),
            ),
        )
        rows += 1
        # price history: append only when latest known price differs
        prev = conn.execute(
            "SELECT price_numeric FROM listing_price_history WHERE listing_id=? ORDER BY observed_at DESC LIMIT 1",
            (lid,),
        ).fetchone()
        if price_numeric is not None and (prev is None or prev["price_numeric"] != price_numeric):
            conn.execute(
                "INSERT OR IGNORE INTO listing_price_history(listing_id, price_numeric, observed_at) VALUES(?,?,?)",
                (lid, price_numeric, ts),
            )
    conn.commit()
    return rows


# --- suburb stats + demographics -----------------------------------------
def write_suburb_stats(
    conn: sqlite3.Connection,
    sa2_code: str | None,
    suburb: str,
    state: str,
    postcode: str,
    property_category: str,
    bedrooms: int,
    payload: dict[str, Any],
) -> int:
    """Store the latest period from a Domain suburbPerformanceStatistics payload."""
    ts = now_iso()
    series = payload.get("series", {}) or {}
    seriesinfo = series.get("seriesInfo", []) or []
    if not seriesinfo:
        return 0
    latest = seriesinfo[-1]
    vals = latest.get("values", {}) or {}
    median = vals.get("medianSoldPrice")
    num_sold = vals.get("numberSold")
    dom = vals.get("daysOnMarket")
    # growth vs first available period
    growth = None
    first_med = (seriesinfo[0].get("values", {}) or {}).get("medianSoldPrice")
    if first_med and median and first_med > 0:
        yrs = max(1, len(seriesinfo) - 1)
        growth = ((median / first_med) ** (1 / yrs) - 1) * 100
    conn.execute(
        """INSERT INTO suburb_stats(sa2_code, suburb, state, postcode, property_category,
             bedrooms, period, median_price, num_sold, days_on_market, growth_pct, observed_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        (sa2_code, suburb, state, postcode, property_category, bedrooms,
         latest.get("year") or latest.get("month"), median, num_sold, dom, growth, ts),
    )
    conn.commit()
    return 1
