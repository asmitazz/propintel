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


# QLD RTA bond-lodgement median rents (updated in place at this path each quarter).
_QLD_URL = "https://www.rta.qld.gov.au/sites/default/files/2023-04/rta-bond-statistics.xlsx"


def _last_nonnull(row, lo: int, hi: int):
    for v in reversed(row[lo:hi + 1]):
        if isinstance(v, (int, float)) and v > 0:
            return int(round(v))
    return None


def pull_qld_rents() -> dict[str, dict]:
    """QLD current market rent by named suburb from the RTA bond data: house = 3-bed house,
    unit/townhouse = 2-bed townhouse (fallback 3-bed townhouse, then 2-bed flat). Weekly $."""
    content = cf.get(_QLD_URL, impersonate="chrome", timeout=90).content
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    ws = wb["4 sub-rents"]
    rows = list(ws.iter_rows(min_row=1, values_only=True))
    months, years = rows[5], rows[6]
    last = max(i for i in range(len(years))
               if isinstance(years[i], (int, float)) or (isinstance(years[i], str) and str(years[i]).strip().isdigit()))
    asof = f"{months[last]} {years[last]}"
    house, th, flat = {}, {}, {}
    for row in rows[7:]:
        if len(row) <= 3 or not row[2] or not row[3]:
            continue
        sub = str(row[2]).strip().upper()
        dw = str(row[3]).strip()
        val = _last_nonnull(row, 4, last)
        if val is None:
            continue
        if dw == "House 3":
            house[sub] = val
        elif dw == "Townhouse 2":
            th[sub] = val
        elif dw == "Townhouse 3":
            th.setdefault(sub, val)
        elif dw == "Flat 2":
            flat[sub] = val
    out: dict[str, dict] = {}
    for sub in set(house) | set(th) | set(flat):
        rec = {"asof": asof}
        if sub in house:
            rec["h"] = house[sub]
        u = th.get(sub) or flat.get(sub)
        if u:
            rec["u"] = u
        if "h" in rec or "u" in rec:
            out[f"QLD|{sub}"] = rec
    return out


_SA_PKG = "private-rent-report"


def _num(v):
    if isinstance(v, (int, float)):
        return int(round(v)) if v > 0 else None
    if isinstance(v, str):
        s = v.strip().replace(",", "")
        try:
            return int(round(float(s))) if float(s) > 0 else None
        except ValueError:
            return None
    return None


def pull_sa_rents() -> dict[str, dict]:
    """SA current market rent by named suburb from the Office of Consumer & Business Services
    Private Rental Report (Data SA CKAN): house = all-houses median (col 20), unit/townhouse =
    all-flats/units median (col 10). Weekly $. The per-quarter file changes, so resolve the
    newest by the YYYY-MM in the resource name."""
    import re as _re
    pkg = cf.get(f"https://data.sa.gov.au/data/api/3/action/package_show?id={_SA_PKG}",
                 impersonate="chrome", timeout=30).json()["result"]
    res = [x for x in pkg["resources"]
           if x.get("format") == "XLSX" and _re.search(r"20\d\d-\d\d", x.get("name", ""))]
    if not res:
        return {}
    res.sort(key=lambda x: _re.search(r"(20\d\d-\d\d)", x["name"]).group(1))
    latest = res[-1]
    ym = _re.search(r"(20\d\d)-(\d\d)", latest["name"])
    mon = {"03": "Mar", "06": "Jun", "09": "Sep", "12": "Dec"}.get(ym.group(2), ym.group(2))
    asof = f"{mon} {ym.group(1)}"
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(cf.get(latest["url"], impersonate="chrome", timeout=60).content),
                                read_only=True, data_only=True)
    ws = wb["Suburb"]
    _SKIP = {"METRO", "COUNTRY", "TOTAL", "SOUTH AUSTRALIA", "ROW LABELS", "GRAND TOTAL"}
    out: dict[str, dict] = {}
    for row in ws.iter_rows(min_row=18, values_only=True):
        name = row[0] if row else None
        if not name or not isinstance(name, str):
            continue
        sub = name.strip().upper()
        if sub in _SKIP:
            continue
        h = _num(row[20]) if len(row) > 20 else None
        u = _num(row[10]) if len(row) > 10 else None
        if h or u:
            rec = {"asof": asof}
            if h:
                rec["h"] = h
            if u:
                rec["u"] = u
            out[f"SA|{sub}"] = rec
    return out


_NSW_LIST = ("https://dcj.nsw.gov.au/about-us/families-and-communities-statistics/"
             "housing-rent-and-sales/previous-rent-and-sales-reports.html")
_NSW_DAM = "https://dcj.nsw.gov.au"
_LGA_SUFFIX = re.compile(r"\b(regional|council|city|shire|municipal(?:ity)?|area|the council of)\b", re.I)


def norm_lga(name: str) -> str:
    """Normalise an LGA name so DCJ's names match the sa2_lga names (drop '(NSW)', a trailing
    'Regional'/'Council'/'City'/'Shire', punctuation) — shared by rents.py and report.py."""
    s = re.sub(r"\([^)]*\)", "", name or "")
    s = _LGA_SUFFIX.sub("", s)
    s = re.sub(r"[^a-z0-9 ]", "", s.lower())
    return re.sub(r"\s+", " ", s).strip().upper()


def _nsw_latest_url() -> str | None:
    """Newest LGA rent-tables .xlsx URL from the DCJ previous-reports listing."""
    html = cf.get(_NSW_LIST, impersonate="chrome", timeout=40).text
    hrefs = re.findall(r'href="([^"]*rent-tables-[a-z]+-20\d\d-quarter\.xlsx)"', html, re.I)
    if not hrefs:
        return None
    q = {"march": 1, "june": 2, "september": 3, "december": 4}

    def key(u):
        m = re.search(r"rent-tables-([a-z]+)-(20\d\d)-quarter", u, re.I)
        return (int(m.group(2)), q.get(m.group(1).lower(), 0)) if m else (0, 0)
    best = max(hrefs, key=key)
    return best if best.startswith("http") else _NSW_DAM + best


def pull_nsw_rents() -> dict[str, dict]:
    """NSW current market rent by LGA (DCJ Rent & Sales, bond lodgements). LGA-level only —
    coarser than the suburb feeds — keyed 'NSW_LGA|<normalised LGA>'; report.py maps each NSW
    suburb to its LGA via sa2_lga.json. house = House/Total median, unit = Townhouse/Total
    (fallback Flat/Unit/Total). Weekly $."""
    url = _nsw_latest_url()
    if not url:
        return {}
    import openpyxl
    content = cf.get(url, impersonate="chrome", timeout=90).content
    wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    ws = wb["LGA"]
    m = re.search(r"rent-tables-([a-z]+)-(20\d\d)", url, re.I)
    asof = f"{m.group(1).title()} {m.group(2)}" if m else "latest"
    house, town, flat = {}, {}, {}
    for row in ws.iter_rows(min_row=9, values_only=True):
        if len(row) <= 7 or not row[3]:
            continue
        lga = norm_lga(str(row[3]))
        dwell, beds, med = str(row[4] or ""), str(row[5] or ""), _num(row[7])
        if med is None or beds != "Total":
            continue
        if dwell == "House":
            house[lga] = med
        elif dwell == "Townhouse":
            town[lga] = med
        elif dwell == "Flat/Unit":
            flat[lga] = med
    out: dict[str, dict] = {}
    for lga in set(house) | set(town) | set(flat):
        rec = {"asof": asof}
        if lga in house:
            rec["h"] = house[lga]
        u = town.get(lga) or flat.get(lga)
        if u:
            rec["u"] = u
        if "h" in rec or "u" in rec:
            out[f"NSW_LGA|{lga}"] = rec
    return out


def build_rents() -> dict[str, dict]:
    """Merge every implemented state into one cache. VIC/QLD/SA by suburb; NSW by LGA."""
    out: dict[str, dict] = {}
    for name, fn in (("VIC", pull_vic_rents), ("QLD", pull_qld_rents),
                     ("SA", pull_sa_rents), ("NSW", pull_nsw_rents)):
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
