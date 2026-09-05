"""'.id' (informed decisions) council population forecasts — free, reliable, pulled in.

.id publishes free small-area population & dwelling forecasts per council (LGA) at
forecast.id.com.au. Those pages aren't a SPA and each carries a clean, consistently
formatted meta-description summary, e.g.:

    "The City of Ballarat population forecast for 2026 is 127,066, and is forecast to
     grow to 164,365 by 2046."

That's the reliable datum we pull. Only councils that subscribe to .id have a public
forecast (~120 of ~550 LGAs), so this is display-only, partial-coverage context — never
fed into the composite score — and where a suburb's council isn't covered we fall back to
the per-suburb .id link already in the report.

Two caches (geography is static; forecasts change ~annually — neither belongs in the
daily hot path):
  data/id_forecast.json  — {lga_key: {council, base_year, base_pop, fc_year, fc_pop, growth_pct}}
  data/sa2_lga.json      — {sa2_code: {lga, state}} via ABS ArcGIS point-in-polygon

Rebuild with:  python3 -m propintel.id_forecast
analyze.py then reads the caches and joins (no network in the daily run).
"""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor

from curl_cffi import requests as cf

from .config import ROOT

FORECAST_HOME = "https://forecast.id.com.au"
LGA_PIP = "https://geo.abs.gov.au/arcgis/rest/services/ASGS2021/LGA/MapServer/0/query"
ID_FILE = ROOT / "data" / "id_forecast.json"
SA2LGA_FILE = ROOT / "data" / "sa2_lga.json"
CENTROIDS = ROOT / "data" / "sa2_centroids.json"

_DESC = re.compile(r'<meta name="Description" content="([^"]+)"', re.I)
_SUMMARY = re.compile(
    r'The (.+?) population forecast for (\d{4}) is ([\d,]+), '
    r'and is forecast to grow to ([\d,]+) by (\d{4})')
_STATE_SUFFIX = {"nsw": "New South Wales", "vic": "Victoria", "qld": "Queensland",
                 "sa": "South Australia", "wa": "Western Australia", "tas": "Tasmania",
                 "nt": "Northern Territory", "act": "Australian Capital Territory"}


def _norm_lga(name: str) -> str:
    """Normalise a council / LGA name to a bare core for matching .id ↔ ABS."""
    n = (name or "").lower()
    n = re.sub(r"\(.*?\)", " ", n)                     # drop (C) (NSW) (S) (A) (RC) …
    words = ["city of", "shire of", "rural city of", "municipality of", "municipal",
             "district council of", "the council of", "regional council", "shire council",
             "city council", "town of", "borough of", "the corporation of", "corporation",
             "regional", "council", "shire", "borough", "city", "town", "district",
             "municipality", "nsw", "vic", "qld", "sa", "wa", "tas", "nt", "act"]
    for w in words:
        n = re.sub(r"\b" + re.escape(w) + r"\b", " ", n)
    n = re.sub(r"[^a-z ]", " ", n)
    return re.sub(r"\s+", " ", n).strip()


def _council_slugs(home_html: str) -> list[str]:
    slugs = set(re.findall(r"forecast\.id\.com\.au/([a-z0-9][a-z0-9\-]+)", home_html))
    bad = {"assets", "dist", "content", "images", "about", "home", "help", "au", "australia"}
    return sorted(s for s in slugs if s not in bad and not s.startswith("assets"))


def _fetch_council(slug: str) -> tuple[str, dict | None]:
    try:
        h = cf.get(f"{FORECAST_HOME}/{slug}", impersonate="chrome", timeout=20).text
    except Exception:
        return slug, None
    m = _DESC.search(h)
    if not m:
        return slug, None
    mm = _SUMMARY.search(m.group(1))
    if not mm:
        return slug, None
    council, base_y, base_pop, fc_pop, fc_y = mm.groups()
    base, fc = int(base_pop.replace(",", "")), int(fc_pop.replace(",", ""))
    if base <= 0:
        return slug, None
    # state hint from a slug suffix like "central-coast-nsw"
    state = next((full for suf, full in _STATE_SUFFIX.items() if slug.endswith("-" + suf)), None)
    return slug, {
        "council": council, "slug": slug, "state": state,
        "base_year": int(base_y), "base_pop": base,
        "fc_year": int(fc_y), "fc_pop": fc,
        "growth_pct": round((fc / base - 1) * 100, 1),
        "key": _norm_lga(council),
    }


def pull_forecasts() -> dict:
    """{lga_key: record} of every council with a public .id forecast."""
    home = cf.get(FORECAST_HOME, impersonate="chrome", timeout=25).text
    slugs = _council_slugs(home)
    with ThreadPoolExecutor(max_workers=16) as ex:
        results = list(ex.map(lambda s: _fetch_council(s), slugs))
    out = {}
    for _slug, rec in results:
        if rec:
            out[rec["key"]] = rec           # keyed by normalised council name
    return out


def _pip_lga(lonlat: tuple[float, float]) -> dict | None:
    """ABS server-side point-in-polygon: centroid → containing LGA (name, state)."""
    try:
        r = cf.get(LGA_PIP, params={
            "geometry": f"{lonlat[0]},{lonlat[1]}", "geometryType": "esriGeometryPoint",
            "inSR": "4326", "spatialRel": "esriSpatialRelIntersects",
            "outFields": "lga_name_2021,state_name_2021", "returnGeometry": "false", "f": "json",
        }, impersonate="chrome", timeout=20)
        feats = r.json().get("features") or []
        if feats:
            a = feats[0]["attributes"]
            return {"lga": a.get("lga_name_2021"), "state": a.get("state_name_2021")}
    except Exception:
        return None
    return None


def build_sa2_lga() -> dict:
    """{sa2_code: {lga, state}} for every cached SA2 centroid, via ABS PIP (threaded)."""
    cents = json.loads(CENTROIDS.read_text())
    codes = list(cents.keys())

    def one(code):
        return code, _pip_lga(cents[code])

    out = {}
    with ThreadPoolExecutor(max_workers=16) as ex:
        for code, lga in ex.map(one, codes):
            if lga and lga.get("lga"):
                out[code] = lga
    return out


def load_join() -> tuple[dict, dict]:
    """Read both caches (used by analyze). Returns ({sa2: {lga,state}}, {lga_key: forecast})."""
    sa2 = json.loads(SA2LGA_FILE.read_text()) if SA2LGA_FILE.exists() else {}
    fc = json.loads(ID_FILE.read_text()) if ID_FILE.exists() else {}
    return sa2, fc


def forecast_for(sa2_code: str, sa2_lga: dict, forecasts: dict) -> dict | None:
    """The .id council forecast for a suburb, matched by its LGA (state-aware). None if uncovered."""
    lga = sa2_lga.get(sa2_code)
    if not lga:
        return None
    key = _norm_lga(lga.get("lga", ""))
    rec = forecasts.get(key)
    if not rec:
        return None
    # guard against cross-state name collisions (e.g. Central Coast NSW vs TAS)
    if rec.get("state") and lga.get("state") and rec["state"] != lga["state"]:
        return None
    return rec


if __name__ == "__main__":
    print("Pulling .id council forecasts …")
    fc = pull_forecasts()
    ID_FILE.write_text(json.dumps(fc, indent=1))
    print(f"  {len(fc)} councils → {ID_FILE.name}")
    print("Building SA2 → LGA (ABS point-in-polygon, ~2.5k points, threaded) …")
    sa2 = build_sa2_lga()
    SA2LGA_FILE.write_text(json.dumps(sa2, separators=(",", ":")))
    print(f"  {len(sa2)} SA2s mapped → {SA2LGA_FILE.name}")
    covered = sum(1 for c in sa2 if _norm_lga(sa2[c]["lga"]) in fc)
    print(f"  SA2s whose council has an .id forecast: {covered} ({covered*100//max(1,len(sa2))}%)")
