"""SA2 centroids + supply-catchment analysis.

Building approvals are a *committed* supply influx (distinct from developable
land, which is uncertain/long-term). A large influx within a short radius drowns
capital growth, so we compute the dwelling-approval influx as a % of dwelling
stock across a 5km catchment and rule out suburbs above a threshold.

Centroids come from the ABS ASGS 2021 SA2 boundaries (generalised geometry via
the ABS ArcGIS service), cached to data/sa2_centroids.json.
"""
from __future__ import annotations

import json
import math

from curl_cffi import requests as cf

from .config import ROOT

CENTROID_CACHE = ROOT / "data" / "sa2_centroids.json"
ARCGIS = "https://geo.abs.gov.au/arcgis/rest/services/ASGS2021/SA2/MapServer/0/query"


def _ring_centroid(geom: dict) -> tuple[float, float] | None:
    if not geom or not geom.get("rings"):
        return None
    pts = [pt for ring in geom["rings"] for pt in ring]
    if not pts:
        return None
    return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))


def fetch_sa2_centroids(refresh: bool = False) -> dict[str, tuple[float, float]]:
    """{sa2_code: (lon, lat)} — cached. Generalised geometry keeps payload small."""
    if CENTROID_CACHE.exists() and not refresh:
        raw = json.loads(CENTROID_CACHE.read_text())
        return {k: tuple(v) for k, v in raw.items()}
    cents: dict[str, tuple[float, float]] = {}
    offset = 0
    while True:
        params = {"where": "1=1", "outFields": "SA2_CODE_2021", "returnGeometry": "true",
                  "maxAllowableOffset": "0.02", "outSR": "4326", "f": "json",
                  "resultOffset": str(offset), "resultRecordCount": "1000"}
        j = cf.get(ARCGIS, params=params, impersonate="chrome", timeout=90).json()
        feats = j.get("features", [])
        for f in feats:
            c = _ring_centroid(f.get("geometry"))
            if c:
                cents[f["attributes"]["sa2_code_2021"]] = c
        if not j.get("exceededTransferLimit") or not feats:
            break
        offset += len(feats)
    CENTROID_CACHE.write_text(json.dumps({k: list(v) for k, v in cents.items()}))
    return cents


def _haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lon1, lat1 = a
    lon2, lat2 = b
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def compute_ripple(
    info: dict[str, dict], centroids: dict[str, tuple[float, float]], radius_km: float = 10.0
) -> dict[str, float]:
    """Ripple / arbitrage signal (suburb-selection Layer 3).

    A suburb priced BELOW nearby suburbs of *similar or higher income* tends to
    'catch up' as the dearer suburbs become unaffordable and demand ripples out.

    For each suburb, look at neighbours within radius_km whose median income is
    at least ~85% of its own (i.e. not poorer), and return the % by which the
    suburb sits below their median house price. Higher = more ripple upside.

    info: {code: {"price": float, "income": float}}
    """
    codes = [c for c in info if c in centroids and info[c].get("price") and info[c].get("income")]
    dlat = radius_km / 111.0
    out: dict[str, float] = {}
    for c in codes:
        lon0, lat0 = centroids[c]
        inc0 = info[c]["income"]
        price0 = info[c]["price"]
        dlon = radius_km / (111.0 * max(0.2, math.cos(math.radians(lat0))))
        nbr_prices = []
        for o in codes:
            if o == c:
                continue
            lon1, lat1 = centroids[o]
            if abs(lat1 - lat0) > dlat or abs(lon1 - lon0) > dlon:
                continue
            if info[o]["income"] < 0.85 * inc0:      # skip poorer neighbours
                continue
            if _haversine_km((lon0, lat0), (lon1, lat1)) <= radius_km:
                nbr_prices.append(info[o]["price"])
        if len(nbr_prices) < 3:                       # need a real neighbourhood
            out[c] = None
            continue
        nbr_prices.sort()
        nbr_median = nbr_prices[len(nbr_prices) // 2]
        out[c] = round((nbr_median - price0) / nbr_median * 100, 1)  # % below peers
    return out


def catchment_influx(
    supply: dict[str, dict], centroids: dict[str, tuple[float, float]], radius_km: float = 5.0
) -> dict[str, float]:
    """For each suburb, dwelling-approval influx as % of dwelling stock, summed
    across all suburbs whose centroid is within radius_km (incl. itself).

    supply: {code: {"approvals": float, "stock": float}}
    """
    codes = [c for c in supply if c in centroids]
    # ~degree box prefilter (1 deg lat ~111km) to avoid full O(n^2) haversine
    dlat = radius_km / 111.0
    result: dict[str, float] = {}
    for c in codes:
        lon0, lat0 = centroids[c]
        dlon = radius_km / (111.0 * max(0.2, math.cos(math.radians(lat0))))
        appr = stock = 0.0
        for o in codes:
            lon1, lat1 = centroids[o]
            if abs(lat1 - lat0) > dlat or abs(lon1 - lon0) > dlon:
                continue
            if _haversine_km((lon0, lat0), (lon1, lat1)) <= radius_km:
                appr += supply[o]["approvals"] or 0
                stock += supply[o]["stock"] or 0
        result[c] = (appr / stock * 100) if stock > 0 else None
    return result
