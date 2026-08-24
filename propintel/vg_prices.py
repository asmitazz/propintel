"""State Valuer-General median prices — real named-suburb sold medians, free & open.

The ABS "Data by Region" medians we score on are whole-SA2, annual and ~1-2yr lagged.
State valuers-general publish sharper, more current *named-suburb* medians for free.
This module ingests them as a DISPLAY-ONLY overlay — surfaced beside the ABS estimate
in the lookup and Compare tab. It is deliberately NOT fed into the composite score:
swapping the price basis would move every score and make the daily change-detection
report phantom "what moved" diffs. Ranking stays ABS-based and reproducible.

Currently implemented:
  VIC — Valuer-General Victoria "Victorian Property Sales Report", median house &
        unit by named suburb, quarterly, CC-BY. Resolved via the DataVic CKAN API
        (the per-quarter file URL changes each release), parsed by column header so a
        new quarter or footnote can't shift a hard-coded position. Degrades to {} on
        any failure — never writes wrong/partial numbers.

NSW comparable-sales (individual PSI records) is a separate, later feature.
"""
from __future__ import annotations

import io
import json
import statistics
import zipfile
from collections import defaultdict

from curl_cffi import requests as cf

# DataVic CKAN packages (median by named suburb, quarterly XLS).
_VIC_HOUSE_PKG = "victorian-property-sales-report-median-house-by-suburb"
_VIC_UNIT_PKG = "victorian-property-sales-report-median-unit-by-suburb"

_QUARTER_ORDER = {"Jan-Mar": 1, "Apr-Jun": 2, "Jul-Sep": 3, "Oct-Dec": 4}


def _latest_xls_url(pkg_id: str) -> str | None:
    """Newest live XLS resource URL for a DataVic package (skips web.archive copies)."""
    url = f"https://discover.data.vic.gov.au/api/3/action/package_show?id={pkg_id}"
    data = cf.get(url, impersonate="chrome", timeout=30).json()
    xls = [r for r in data["result"]["resources"]
           if r.get("format", "").upper() == "XLS" and "web.archive.org" not in r.get("url", "")]
    return xls[-1]["url"] if xls else None


def _parse_vic_xls(content: bytes) -> tuple[dict[str, int], str]:
    """Parse a VGV median-by-suburb .xls -> ({SUBURB_UPPER: latest_median}, asof_label).

    Header-driven: rows near the top carry the quarter label ('Jul-Sep') and the year;
    we locate the column of the most recent (year, quarter) and read that column, so a
    new quarter appended on the right just works. Returns ({}, "") if the shape is off.
    """
    import re
    import xlrd
    wb = xlrd.open_workbook(file_contents=content)
    sh = wb.sheet_by_index(0)

    # Locate each data column's (year, quarter). Robust to both layouts seen: the house
    # file splits quarter (one row) and year (next row) across cells; the unit file packs
    # them into one cell ('Oct-Dec\n2023'). So scan the first rows and pull a quarter token
    # and a 4-digit year from the cell text of each column, however they're arranged.
    q_re = re.compile(r"(Jan-Mar|Apr-Jun|Jul-Sep|Oct-Dec)")
    y_re = re.compile(r"(20\d{2})")
    col_period: dict[int, tuple[int, int]] = {}
    for c in range(sh.ncols):
        qlabel = year = None
        for r in range(min(6, sh.nrows)):
            v = sh.cell_value(r, c)
            s = v if isinstance(v, str) else (str(int(v)) if isinstance(v, (int, float)) and v else "")
            mq, my = q_re.search(s), y_re.search(s)
            if mq:
                qlabel = mq.group(1)
            if my:
                year = int(my.group(1))
        if qlabel and year:
            col_period[c] = (year, _QUARTER_ORDER[qlabel])
    if not col_period:
        return {}, ""
    latest_col = max(col_period, key=lambda c: col_period[c])
    yr, qo = col_period[latest_col]
    qname = next(k for k, v in _QUARTER_ORDER.items() if v == qo)
    asof = f"{qname} {yr}"

    out: dict[str, int] = {}
    for r in range(sh.nrows):
        name = sh.cell_value(r, 0)
        if not isinstance(name, str) or not name.strip():
            continue
        key = name.strip().upper()
        if key in ("LOCALITY", "TOTAL", "GRAND TOTAL") or key in _QUARTER_ORDER:
            continue
        raw = sh.cell_value(r, latest_col)
        val = None
        if isinstance(raw, (int, float)) and raw > 0:
            val = int(raw)
        elif isinstance(raw, str):
            s = raw.replace(",", "").replace("$", "").strip()
            if s.isdigit():
                val = int(s)
        if val and val > 10000:                 # guard against stray small numbers
            out[key] = val
    return out, asof


def _pull_vic_one(pkg_id: str) -> tuple[dict[str, int], str]:
    url = _latest_xls_url(pkg_id)
    if not url:
        return {}, ""
    r = cf.get(url, impersonate="chrome", timeout=60)
    if r.status_code != 200 or not r.content:
        return {}, ""
    return _parse_vic_xls(r.content)


def pull_vic_medians() -> dict[str, dict]:
    """{SUBURB_UPPER: {"h": house_median, "h_asof": "..", "u": unit_median, "u_asof": ".."}}.

    Display-only overlay. Any failure degrades to {} (never wrong/partial data)."""
    try:
        houses, h_asof = _pull_vic_one(_VIC_HOUSE_PKG)
    except Exception:
        houses, h_asof = {}, ""
    try:
        units, u_asof = _pull_vic_one(_VIC_UNIT_PKG)
    except Exception:
        units, u_asof = {}, ""
    out: dict[str, dict] = {}
    for sub, med in houses.items():
        out.setdefault(sub, {})["h"] = med
        out[sub]["h_asof"] = h_asof
    for sub, med in units.items():
        out.setdefault(sub, {})["u"] = med
        out[sub]["u_asof"] = u_asof
    return out


# --- NSW: real named-suburb medians from the free CC Property Sales Information -------------
_NSW_YEAR = "2025"   # latest full-year archive (a zip of weekly zips of per-district .DAT)


def _nsw_parse(text: str, house: dict, attached: dict) -> None:
    for line in text.splitlines():
        if not line.startswith("B;"):
            continue
        f = line.split(";")
        if len(f) < 19 or f[18].strip().upper() != "RESIDENCE":
            continue
        loc = f[9].strip().upper()
        try:
            price = int(f[15])
        except (ValueError, IndexError):
            continue
        if price < 200000 or not loc:            # floor out non-arm's-length transfers
            continue
        (attached if f[6].strip() else house)[loc].append(price)   # unit no. => strata/attached


def _nsw_zip(raw: bytes, house: dict, attached: dict) -> None:
    z = zipfile.ZipFile(io.BytesIO(raw))
    for name in z.namelist():
        if name.lower().endswith(".zip"):
            _nsw_zip(z.read(name), house, attached)
        elif name.upper().endswith(".DAT"):
            _nsw_parse(z.read(name).decode("latin-1", "ignore"), house, attached)


def pull_nsw_medians() -> dict[str, dict]:
    """{SUBURB_UPPER: {"h": house_median, "a": attached_median, "asof": "2025", "n": count}}.

    Real NSW sold medians for EVERY locality, split house vs attached (strata-titled
    unit/townhouse/villa) via the unit-number field. Display-only overlay like VIC;
    degrades to {} on any failure. Median-only (no individual addresses) for the public site."""
    try:
        r = cf.get(f"https://www.valuergeneral.nsw.gov.au/__psi/yearly/{_NSW_YEAR}.zip",
                   impersonate="chrome", timeout=180)
        if r.status_code != 200 or not r.content:
            return {}
        house: dict[str, list] = defaultdict(list)
        attached: dict[str, list] = defaultdict(list)
        _nsw_zip(r.content, house, attached)
        out: dict[str, dict] = {}
        for loc in set(house) | set(attached):
            h, a = house.get(loc, []), attached.get(loc, [])
            if len(h) + len(a) < 8:              # skip thin suburbs — unreliable median
                continue
            out[loc] = {
                "h": int(statistics.median(h)) if h else None,
                "a": int(statistics.median(a)) if a else None,
                "asof": _NSW_YEAR, "n": len(h) + len(a),
            }
        return out
    except Exception:
        return {}


if __name__ == "__main__":
    m = pull_vic_medians()
    print(f"VIC VG medians: {len(m)} suburbs")
    for s in ("GEELONG", "MELTON SOUTH", "SPEARWOOD", "CORIO", "LARA", "BROADMEADOWS"):
        if s in m:
            print(" ", s, m[s])
