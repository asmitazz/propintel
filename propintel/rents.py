"""Current market rents by suburb — free state rental-bond data.

The yield we score on uses ABS Census 2021 median rent (with a flat market uplift),
which is ~4 years stale and reads low. State bond-lodgement data gives the *current*
market rent by suburb, quarterly. This module ingests it into a display-only cache
(`data/rents.json`) that the report's yield-now / re-rank layer prefers over the Census
rent — so only the yield *display* and the current-adjusted `#` move, never the ABS
composite score (which stays reproducible; the change-signature stays byte-identical).

IMPORTANT: bond rent is already *market* rent, so the ~1.4pp Census→market uplift is
NOT added to it (report.py only adds the uplift to Census fallback rows). Mixing the two
bases would bias the list, so every row ends up on one market basis before normalising.

Keyed "STATE|SUBURB_UPPER" → {"h": house_weekly, "u": unit/townhouse_weekly, "asof": ..}.
Built out of the daily hot path via `python -m propintel.rents --rents` (like --yoy);
degrades to {} on any failure and refuses to overwrite a good cache with a thin result.

Implemented: VIC (DFFH Rental Report, moving-annual median by named suburb, DataVic CKAN).
"""
from __future__ import annotations

import io
import json
import re
from pathlib import Path

from curl_cffi import requests as cf

from .config import ROOT

RENTS_CACHE = ROOT / "data" / "rents.json"

# DataVic CKAN package: moving-annual median rent by named suburb, per dwelling type.
_VIC_PKG = "rental-report-quarterly-moving-annual-rents-by-suburb"
_Q_ORDER = {"march": 1, "june": 2, "september": 3, "december": 4}


def _vic_latest_suburb_url() -> str | None:
    """Newest by-suburb XLSX resource URL (the per-quarter slug changes each release)."""
    url = f"https://discover.data.vic.gov.au/api/3/action/package_show?id={_VIC_PKG}"
    res = cf.get(url, impersonate="chrome", timeout=30).json()["result"]["resources"]

    def key(r):
        s = (r.get("url", "") + " " + r.get("name", "")).lower()
        yr = re.search(r"(20\d{2})", s)
        q = next((v for k, v in _Q_ORDER.items() if k in s), 0)
        return (int(yr.group(1)) if yr else 0, q)

    cand = [r for r in res
            if r.get("format") == "XLSX" and "suburb" in (r.get("url", "") + r.get("name", "")).lower()]
    if not cand:
        return None
    return max(cand, key=key).get("url")


def _last_median_col(rows: list) -> int | None:
    """Index of the last 'Median' column from the two header rows (row 1 = quarter labels,
    row 2 = Count/Median). Returns None if the layout doesn't match."""
    hdr = rows[2] if len(rows) > 2 else []
    idxs = [i for i, c in enumerate(hdr) if isinstance(c, str) and c.strip().lower() == "median"]
    return idxs[-1] if idxs else None


def _vic_sheet_medians(wb, sheet: str) -> dict[str, int]:
    """{SUBURB_UPPER: latest_weekly_median} for one dwelling-type sheet."""
    import openpyxl  # noqa: F401 (import guard — openpyxl backs read_only workbooks)
    ws = wb[sheet]
    rows = list(ws.iter_rows(min_row=1, max_row=4, values_only=True))
    col = _last_median_col(rows)
    if col is None:
        return {}
    out: dict[str, int] = {}
    for row in ws.iter_rows(min_row=4, values_only=True):
        name = row[1] if len(row) > 1 else None      # col B = suburb / town
        if not name or not isinstance(name, str):
            continue
        val = row[col] if len(row) > col else None
        if isinstance(val, (int, float)) and val > 0:
            out[name.strip().upper()] = int(round(val))
    return out


def pull_vic_rents() -> dict[str, dict]:
    """VIC current market rent by named suburb: house = 3-bedroom house median,
    unit/townhouse = 2-bedroom flat median (fallback 2-bedroom house). Weekly $."""
    url = _vic_latest_suburb_url()
    if not url:
        return {}
    content = cf.get(url, impersonate="chrome", timeout=60, allow_redirects=True).content
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    asof = wb.sheetnames and None
    house = _vic_sheet_medians(wb, "3 bedroom house") if "3 bedroom house" in wb.sheetnames else {}
    unit = _vic_sheet_medians(wb, "2 bedroom flat") if "2 bedroom flat" in wb.sheetnames else {}
    unit2 = _vic_sheet_medians(wb, "2 bedroom house") if "2 bedroom house" in wb.sheetnames else {}
    # asof from the slug (…-<quarter>-quarter-<year>-excel)
    m = re.search(r"(march|june|september|december)-quarter-(20\d{2})", url.lower())
    asof = f"{m.group(1).title()} qtr {m.group(2)}" if m else "latest"
    out: dict[str, dict] = {}
    for sub in set(house) | set(unit) | set(unit2):
        rec = {"asof": asof}
        if sub in house:
            rec["h"] = house[sub]
        u = unit.get(sub) or unit2.get(sub)
        if u:
            rec["u"] = u
        if "h" in rec or "u" in rec:
            out[f"VIC|{sub}"] = rec
    return out


def build_rents() -> dict[str, dict]:
    """Merge every implemented state into one cache. Currently: VIC."""
    out: dict[str, dict] = {}
    for name, fn in (("VIC", pull_vic_rents),):
        try:
            got = fn()
            print(f"  [{name}] rent suburbs: {len(got)}")
            out.update(got)
        except Exception as e:  # never let one state's failure sink the rest
            print(f"  [{name}] FAILED: {e}")
    return out


if __name__ == "__main__":
    import sys
    if "--rents" in sys.argv:
        data = build_rents()
        # thin-write guard: don't clobber a good cache with a broken fetch
        prev = {}
        if RENTS_CACHE.exists():
            prev = json.loads(RENTS_CACHE.read_text())
        if len(data) < 300 and len(prev) >= 300:
            print(f"REFUSING to write thin rents ({len(data)} < 300); kept previous ({len(prev)}).")
        else:
            RENTS_CACHE.write_text(json.dumps(data, separators=(",", ":"), ensure_ascii=False))
            print(f"Wrote {RENTS_CACHE} — {len(data)} suburbs.")
    else:
        print("usage: python -m propintel.rents --rents")
