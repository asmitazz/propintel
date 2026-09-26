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
import re
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


def _parse_vic_yoy(content: bytes) -> dict[str, dict]:
    """{SUBURB_UPPER: {yoy, asof, prior}} — VG's published median for the latest quarter vs the
    SAME quarter a year earlier, both from the one multi-quarter file. VG's medians are already
    vetted (no per-suburb sample count is published, so we trust them as-is)."""
    import re
    import xlrd
    wb = xlrd.open_workbook(file_contents=content)
    sh = wb.sheet_by_index(0)
    q_re = re.compile(r"(Jan-Mar|Apr-Jun|Jul-Sep|Oct-Dec)")
    y_re = re.compile(r"(20\d{2})")
    col_period = {}
    for c in range(sh.ncols):
        qlabel = year = None
        for r in range(min(6, sh.nrows)):
            v = sh.cell_value(r, c)
            s = v if isinstance(v, str) else (str(int(v)) if isinstance(v, (int, float)) and v else "")
            if q_re.search(s):
                qlabel = q_re.search(s).group(1)
            if y_re.search(s):
                year = int(y_re.search(s).group(1))
        if qlabel and year:
            col_period[c] = (year, _QUARTER_ORDER[qlabel])
    if not col_period:
        return {}
    # The VG report carries ~5 quarters (some periods repeat across sub-columns), so a
    # 4-quarter average isn't available — use the latest quarter vs the SAME quarter a year
    # earlier. First de-dupe to one column per period (keep the right-most = the data column).
    period_col = {}
    for c in sorted(col_period):
        period_col.setdefault(col_period[c], c)   # leftmost col of each period = the median
    latest_p = max(period_col)
    yr, qo = latest_p
    prior_c = period_col.get((yr - 1, qo))
    if prior_c is None:
        return {}
    latest_c = period_col[latest_p]
    qn = {v: k for k, v in _QUARTER_ORDER.items()}
    asof, prior = f"{qn[qo]} {yr}", f"{qn[qo]} {yr-1}"

    def _num(raw):
        if isinstance(raw, (int, float)) and raw > 10000:
            return int(raw)
        if isinstance(raw, str):
            s = raw.replace(",", "").replace("$", "").strip()
            return int(s) if s.isdigit() and int(s) > 10000 else None
        return None

    out = {}
    for r in range(sh.nrows):
        name = sh.cell_value(r, 0)
        if not isinstance(name, str) or not name.strip():
            continue
        key = name.strip().upper()
        if key in ("LOCALITY", "TOTAL", "GRAND TOTAL") or key in _QUARTER_ORDER:
            continue
        cur, prev = _num(sh.cell_value(r, latest_c)), _num(sh.cell_value(r, prior_c))
        if cur and prev:
            out[key] = {"yoy": round((cur / prev - 1) * 100, 1), "asof": asof, "prior": prior}
    return out


def _nsw_year_raw(year: str) -> dict[str, dict]:
    r = cf.get(f"https://www.valuergeneral.nsw.gov.au/__psi/yearly/{year}.zip",
               impersonate="chrome", timeout=180)
    if r.status_code != 200 or not r.content:
        return {}
    house, attached = defaultdict(list), defaultdict(list)
    _nsw_zip(r.content, house, attached)
    return {loc: {"h": house.get(loc, []), "a": attached.get(loc, [])} for loc in set(house) | set(attached)}


# ---- NSW rolling-12 (current to within a week) ----
_NSW_EMBED = "https://valuation.property.nsw.gov.au/embed/propertySalesInformation"
_NSW_BASE = "https://www.valuergeneral.nsw.gov.au/__psi"
_NSW_HDR = {"Referer": _NSW_EMBED}   # weekly files 403 without a referer (hotlink protection)


def _nsw_parse_dated(text: str, sales: dict) -> None:
    """Accumulate (contract_date 'YYYYMMDD', price, kind) per locality — dates kept for rolling."""
    for line in text.splitlines():
        if not line.startswith("B;"):
            continue
        f = line.split(";")
        if len(f) < 19 or f[18].strip().upper() != "RESIDENCE":
            continue
        loc = f[9].strip().upper()
        cdate = f[13].strip()
        if not loc or len(cdate) != 8 or not cdate.isdigit():
            continue
        try:
            price = int(f[15])
        except (ValueError, IndexError):
            continue
        if price < 200000:
            continue
        sales[loc].append((cdate, price, "a" if f[6].strip() else "h"))   # unit-no ⇒ attached


def _nsw_zip_dated(raw: bytes, sales: dict) -> None:
    z = zipfile.ZipFile(io.BytesIO(raw))
    for name in z.namelist():
        if name.lower().endswith(".zip"):
            _nsw_zip_dated(z.read(name), sales)
        elif name.upper().endswith(".DAT"):
            _nsw_parse_dated(z.read(name).decode("latin-1", "ignore"), sales)


def _nsw_weekly_urls() -> list[str]:
    """Current-year weekly PSI zip URLs. Primary: the public NSW VG listing (independently
    found), retried because it's intermittently empty. Fallback: probe recent weekly dates
    directly, so a flaky listing can't silently drop the current-year data."""
    import datetime
    for _ in range(4):
        try:
            html = cf.get(_NSW_EMBED, impersonate="chrome", timeout=40).text
            dates = sorted(set(re.findall(r"/__psi/weekly/(\d{8})\.zip", html)))
            if dates:
                return [f"{_NSW_BASE}/weekly/{d}.zip" for d in dates]
        except Exception:
            pass
    # fallback: probe the last ~40 weeks of dated URLs directly (with the referer)
    hits, today = [], datetime.date.today()
    for i in range(0, 300, 7):
        d = (today - datetime.timedelta(days=i)).strftime("%Y%m%d")
        try:
            if cf.head(f"{_NSW_BASE}/weekly/{d}.zip", impersonate="chrome", timeout=15,
                       headers=_NSW_HDR).status_code == 200:
                hits.append(f"{_NSW_BASE}/weekly/{d}.zip")
        except Exception:
            pass
    return hits


def _nsw_rolling_yoy() -> dict[str, dict]:
    """Per-locality house/attached median change: rolling last-12-months vs the prior 12,
    bucketed by contract date (current to the latest weekly file). ≥20 sales in EACH window."""
    import datetime
    import statistics
    sales: dict[str, list] = defaultdict(list)
    weeks = _nsw_weekly_urls()                         # get the listing FIRST — the heavy archive
    got = 0                                            # downloads below throttle later requests
    for yr in ("2024", "2025"):                       # cover the prior-12 window's tail
        try:
            r = cf.get(f"{_NSW_BASE}/yearly/{yr}.zip", impersonate="chrome", timeout=180, headers=_NSW_HDR)
            if r.status_code == 200:
                _nsw_zip_dated(r.content, sales)
        except Exception:
            pass
    for u in weeks:                                    # current-year weeklies → to within a week
        try:
            r = cf.get(u, impersonate="chrome", timeout=60, headers=_NSW_HDR)
            if r.status_code == 200 and r.content[:2] == b"PK":
                _nsw_zip_dated(r.content, sales)
                got += 1
        except Exception:
            pass
    print(f"  [NSW] weekly URLs: {len(weeks)}, downloaded: {got}")
    if not sales:
        return {}
    import datetime
    today = datetime.date.today().strftime("%Y%m%d")
    latest = max((d for rows in sales.values() for d, _, _ in rows if d <= today), default=None)
    if latest is None:
        return {}
    ld = datetime.datetime.strptime(latest, "%Y%m%d").date()
    b1 = (ld - datetime.timedelta(days=365)).strftime("%Y%m%d")   # last-12 start
    b2 = (ld - datetime.timedelta(days=730)).strftime("%Y%m%d")   # prior-12 start
    asof = f"rolling 12mo to {ld.strftime('%d %b %Y')}"
    out: dict[str, dict] = {}
    for loc, rows in sales.items():
        cur_n = sum(1 for d, _, _ in rows if b1 < d <= latest)
        prev_n = sum(1 for d, _, _ in rows if b2 < d <= b1)
        if cur_n < 20:                     # too little current activity to say anything
            continue
        # Buyer activity ("where people are buying"): total sales this year, and vs last year.
        rec = {"state": "NSW", "asof": asof, "prior": "prior 12mo", "vol": cur_n}
        if prev_n >= 20:
            rec["vol_chg"] = round((cur_n / prev_n - 1) * 100)
        for kind in ("h", "a"):            # price change, split house vs attached
            cur = [p for d, p, k in rows if k == kind and b1 < d <= latest]
            prev = [p for d, p, k in rows if k == kind and b2 < d <= b1]
            if len(cur) >= 20 and len(prev) >= 20:
                c, p = statistics.median(cur), statistics.median(prev)
                if p > 0:
                    rec[f"{kind}_yoy"] = round((c / p - 1) * 100, 1)
                    rec[f"{kind}_n"] = len(cur)
        out[f"NSW|{loc}"] = rec
    return out


def build_yoy(nsw_cur: str = "2025", nsw_prev: str = "2024") -> dict[str, dict]:
    """{"STATE|SUBURB_UPPER": {state, h_yoy, h_n, a_yoy, a_n, asof, prior}} — current per-suburb
    SOLD-price change from the state Valuer-General (far fresher than the 2024 ABS medians the rank
    uses). Keyed by STATE|name to avoid cross-state collisions (e.g. Richmond NSW vs VIC). NSW is
    computed from raw sales (≥20 in each year); VIC from VG's published quarterly medians."""
    out: dict[str, dict] = {}
    try:
        out.update(_nsw_rolling_yoy())          # NSW: rolling-12 price + buyer-activity, to the week
    except Exception:
        pass
    def _fetch_xls(url):                          # land.vic.gov.au WAF serves HTML intermittently
        for _ in range(4):
            c = cf.get(url, impersonate="chrome", timeout=60).content
            if c[:5] != b"<!DOC":
                return c
        return None
    try:
        for pkg, field in [("victorian-property-sales-report-median-house-by-suburb", "h_yoy"),
                           ("victorian-property-sales-report-median-unit-by-suburb", "a_yoy")]:
            url = _latest_xls_url(pkg)
            if not url:
                continue
            content = _fetch_xls(url)
            if not content:
                continue
            vic = _parse_vic_yoy(content)
            for sub, v in vic.items():
                rec = out.setdefault(f"VIC|{sub}", {"state": "VIC", "asof": v["asof"], "prior": v["prior"]})
                rec[field] = v["yoy"]
    except Exception:
        pass
    return out


if __name__ == "__main__":
    import json
    import sys
    from .config import ROOT
    if "--yoy" in sys.argv:
        yoy = build_yoy()
        nsw = sum(1 for k in yoy if k.startswith("NSW|"))
        vic = sum(1 for k in yoy if k.startswith("VIC|"))
        # Guard: a WAF challenge or a failed archive silently yields an empty slice. Refuse to
        # overwrite a good cache with a broken one — keep the previous file instead.
        if vic < 500 or nsw < 1000:
            print(f"REFUSING to write vg_yoy.json — thin result (NSW {nsw}, VIC {vic}); kept previous.")
        else:
            (ROOT / "data" / "vg_yoy.json").write_text(json.dumps(yoy, separators=(",", ":")))
            print(f"Wrote vg_yoy.json — {len(yoy)} suburbs (NSW {nsw}, VIC {vic})")
    else:
        m = pull_vic_medians()
        print(f"VIC VG medians: {len(m)} suburbs")
