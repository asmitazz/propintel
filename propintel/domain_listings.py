"""Live current prices from the free Domain listings search (Agents & Listings).

Computes a current **median asking price** per suburb from for-sale listings —
the accurate-current-price signal the ABS 2024 medians can't give. Requires the
free "Agents & Listings" package on your Domain project.

Note: asking (list) prices run slightly above eventual sold prices, and many
listings show a range or "contact agent" (no guide) — we median the listings
that carry a numeric guide, and report the sample count so thin suburbs are
visible.
"""
from __future__ import annotations

import json
import time
from statistics import median

from .config import ROOT, settings
from .domain_client import DomainClient, PackageNotEnabled
from .ingest import parse_price

LIVE_PRICES = ROOT / "data" / "live_prices.json"


def _listing_price(item: dict) -> float | None:
    listing = item.get("listing", item)
    if listing.get("type") == "Project":
        return None
    pd = listing.get("priceDetails", {}) or {}
    return pd.get("price") or parse_price(pd.get("displayPrice"))


def suburb_listing_stats(
    client: DomainClient,
    suburb: str,
    state: str,
    property_types: list[str],
    max_pages: int = 2,
    page_size: int = 100,
) -> dict:
    """Return {count, priced, median_asking, min, max} for a suburb's for-sale houses."""
    prices: list[float] = []
    total = 0
    for page in range(1, max_pages + 1):
        results = client.search_listings(
            [{"state": state, "suburb": suburb, "includeSurroundingSuburbs": False}],
            max_price=10_000_000, property_types=property_types,
            page=page, page_size=page_size,
        )
        if not isinstance(results, list) or not results:
            break
        total += len(results)
        for item in results:
            p = _listing_price(item)
            if p and p > 50_000:
                prices.append(p)
        if len(results) < page_size:
            break
    prices.sort()
    return {
        "count": total,
        "priced": len(prices),
        "median_asking": round(median(prices)) if prices else None,
        "min": round(prices[0]) if prices else None,
        "max": round(prices[-1]) if prices else None,
    }


def refresh_shortlist(top_n: int = 25, property_types: list[str] | None = None) -> dict:
    """Pull live medians for the top-N in-budget suburbs and cache them.

    Keyed by SA2 code so the report can overlay live prices onto the ABS estimate.
    """
    analysis = json.loads((ROOT / "data" / "suburb_analysis.json").read_text())
    subs = analysis["suburbs"]
    property_types = property_types or settings.criteria["property_types"]

    # candidates: best-scoring suburbs with a house market in-budget
    cand = [s for s in subs if s.get("house") and s["house"]["price_now"] <= 700_000]
    cand.sort(key=lambda s: s["house"]["score"], reverse=True)
    cand = cand[:top_n]

    client = DomainClient()
    out = {}
    delay = float(settings.domain.get("request_delay_seconds", 1.0))
    for s in cand:
        if client.api_calls >= settings.domain.get("daily_call_budget", 450):
            print("  reached daily call budget; stopping.")
            break
        try:
            stats = suburb_listing_stats(client, s["name"], s["state"], ["House"])
            out[s["code"]] = {"name": s["name"], "state": s["state"], **stats}
            print(f"  {s['name']} ({s['state']}): live median ${stats['median_asking']:,}"
                  if stats["median_asking"] else f"  {s['name']}: no priced listings", flush=True)
        except PackageNotEnabled as e:
            raise SystemExit(str(e))
        time.sleep(delay)

    LIVE_PRICES.write_text(json.dumps({"generated": _now(), "api_calls": client.api_calls,
                                       "suburbs": out}, indent=1))
    return out


def _now() -> str:
    from .db import now_iso
    return now_iso()
