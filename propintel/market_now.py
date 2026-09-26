"""'Market now' cross-reference — bridge the lagged fundamentals ranking to the CURRENT
capital-city price direction (Cotality), so a top-ranked suburb in a softening market shows
that tension instead of hiding it.

The shortlist ranks on ABS fundamentals that lag ~1-3 years (median prices are 2024,
nowcast forward). This module tags each SA2 with its Greater-Capital region (GCCSA, a plain
attribute on the ABS SA2 layer — no point-in-polygon needed) and maps that to the matching
Cotality Home Value Index 1-yr / 1-mo change. Rest-of-state SA2s have no free index, so they
say so rather than borrowing a capital's number.

DISPLAY-ONLY: this is context shown beside the ranking, never fed into the score (the score
must stay reproducible; national/metro direction moves every suburb and would fire phantom
digest diffs). SA2→GCCSA is static geography, cached to data/sa2_gccsa.json (committed).
Rebuild with: python3 -m propintel.market_now
"""
from __future__ import annotations

import json

from curl_cffi import requests as cf

from .config import ROOT

SA2_LAYER = "https://geo.abs.gov.au/arcgis/rest/services/ASGS2021/SA2/MapServer/0/query"
GCCSA_FILE = ROOT / "data" / "sa2_gccsa.json"

# Greater-Capital GCCSA → the Cotality capital-city row name (standalone Brisbane, not the
# "Brisbane (inc Gold Coast)" variant). Everything else ("Rest of …") is regional.
GCCSA_TO_CAPITAL = {
    "Greater Sydney": "Sydney", "Greater Melbourne": "Melbourne",
    "Greater Brisbane": "Brisbane", "Greater Adelaide": "Adelaide",
    "Greater Perth": "Perth", "Greater Hobart": "Hobart",
    "Greater Darwin": "Darwin", "Australian Capital Territory": "Canberra",
}


def build_sa2_gccsa() -> dict:
    """{sa2_code: gccsa_name} for every SA2, via a paged attribute query (no geometry)."""
    out, offset = {}, 0
    while True:
        r = cf.get(SA2_LAYER, params={
            "where": "1=1", "outFields": "sa2_code_2021,gccsa_name_2021",
            "returnGeometry": "false", "resultOffset": offset, "resultRecordCount": 2000, "f": "json",
        }, impersonate="chrome", timeout=60)
        feats = r.json().get("features") or []
        if not feats:
            break
        for f in feats:
            a = f["attributes"]
            if a.get("sa2_code_2021"):
                out[str(a["sa2_code_2021"])] = a.get("gccsa_name_2021")
        if len(feats) < 2000:
            break
        offset += 2000
    return out


def load_gccsa() -> dict:
    return json.loads(GCCSA_FILE.read_text()) if GCCSA_FILE.exists() else {}


def market_for(sa2_code: str, sa2_gccsa: dict, cotality: dict) -> dict | None:
    """Current-market tag for a suburb: {cap, yr, mo} for a capital, or {regional:True}."""
    g = sa2_gccsa.get(str(sa2_code))
    if not g:
        return None
    cap = GCCSA_TO_CAPITAL.get(g)
    if not cap:
        return {"regional": True}          # Rest-of-state — no free capital-city index
    city = next((c for c in (cotality or {}).get("cities", []) if c.get("name") == cap), None)
    if not city:
        return {"regional": True}
    return {"cap": cap, "yr": city.get("all_yr"), "mo": city.get("all_mo")}


if __name__ == "__main__":
    m = build_sa2_gccsa()
    GCCSA_FILE.write_text(json.dumps(m, separators=(",", ":")))
    import collections
    caps = collections.Counter("capital" if GCCSA_TO_CAPITAL.get(v) else "regional" for v in m.values())
    print(f"Wrote {len(m)} SA2→GCCSA → {GCCSA_FILE.name}  ({dict(caps)})")
