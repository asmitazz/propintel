"""National macro / leading indicators — the cycle backdrop, from primary sources.

The point (per the user): read the macro tape from free primary data and state, in
transparent published rules, what each condition has *historically* been associated with —
not a forecast, not advice, not someone's hand-drawn trendline. Every state below is
computed from the series and shown next to the rule that produced it, so it can be checked.

Sources (all free, machine-readable):
  Cash rate target ...... RBA F1.1  series FIRMMCRT   (monthly, %)
  Mortgage rate ......... RBA F6    series FLRHOOTA    (owner-occ, all loans, all inst, %)
  Household debt-to-income RBA E2   series BHFDDIT     (quarterly, ratio — NOT debt-to-GDP)
  Unemployment rate ..... ABS LF    M13.3.1599.20.AUS.M(monthly, seasonally adj, %)
  Personal insolvencies . AFSA monthly time series CSV (household stress → forced sales)

DISPLAY-ONLY: national indicators move every suburb equally, so they are never fed into
the per-suburb composite score. Each pull degrades to {} on failure. Small files — fine to
fetch in the daily run.
"""
from __future__ import annotations

import calendar
import csv
import io
import re
from datetime import datetime

from curl_cffi import requests as cf

RBA = "https://www.rba.gov.au/statistics/tables/csv/{}-data.csv"
ABS_LF = ("https://data.api.abs.gov.au/rest/data/LF/M13.3.1599.20.AUS.M"
          "?startPeriod=2015-01&dimensionAtObservation=AllDimensions")
AFSA_PKG = ("https://data.gov.au/data/api/3/action/package_show?id="
            "4174f850-3d50-4b07-ae1d-4eb38e628bb4")
ASIC_STATS = ("https://asic.gov.au/regulatory-resources/find-a-document/statistics/"
              "insolvency-statistics/")
_MON = {m.lower(): i for i, m in enumerate(
    ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])}

_DATE_FMTS = ("%d-%b-%Y", "%b-%Y", "%b-%y", "%d/%m/%Y", "%Y-%m", "%Y-%m-%d")


def _pdate(s: str):
    s = (s or "").strip()
    for f in _DATE_FMTS:
        try:
            return datetime.strptime(s, f)
        except ValueError:
            continue
    return None


def _parse_rba(text: str, series_id: str) -> list[tuple[datetime, float]]:
    """Parse an RBA CSV by Series ID (never column position — headers shift)."""
    rows = list(csv.reader(io.StringIO(text)))
    id_row = next((r for r in rows if r and r[0].strip() == "Series ID"), None)
    if not id_row or series_id not in id_row:
        return []
    col = id_row.index(series_id)
    out = []
    for r in rows:
        if not r or col >= len(r):
            continue
        d = _pdate(r[0])
        if d is None:
            continue
        v = r[col].strip()
        if not v:
            continue
        try:
            out.append((d, float(v)))
        except ValueError:
            continue
    out.sort(key=lambda x: x[0])
    return out


def _series(pairs, n=132):
    """Downsample to a compact monthly-ish tail for charting: [[YYYY-MM, value], …]."""
    tail = pairs[-n:]
    return [[d.strftime("%Y-%m"), round(v, 2)] for d, v in tail]


def _get(url, timeout=25, headers=None):
    """Hard-capped fetch — a plain curl timeout was observed not to fire on a 0-byte hang
    (this runs in the daily pipeline), so cap it with a future that always returns."""
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(cf.get, url, impersonate="chrome", timeout=timeout, headers=headers)
        return fut.result(timeout=timeout + 10)


# ---- individual pulls (each returns [(date, value)] or []) ----
def _rba_series(table, sid):
    try:
        return _parse_rba(_get(RBA.format(table)).text, sid)
    except Exception:
        return []


def _unemployment():
    try:
        d = _get(ABS_LF, headers={"Accept": "application/vnd.sdmx.data+json"}).json()
        struct = d["data"]["structures"][0]
        dims = struct["dimensions"]["observation"]
        time_dim = next(x for x in dims if x["id"] == "TIME_PERIOD")
        periods = [v["id"] for v in time_dim["values"]]
        ti = dims.index(time_dim)
        obs = d["data"]["dataSets"][0]["observations"]
        out = []
        for key, val in obs.items():
            idx = int(key.split(":")[ti])
            dt = _pdate(periods[idx])
            if dt is not None and val and val[0] is not None:
                out.append((dt, round(float(val[0]), 2)))
        out.sort(key=lambda x: x[0])
        return out
    except Exception:
        return []


def _insolvencies():
    """AFSA monthly total personal insolvencies, national.

    The file is fully disaggregated (month × state × type × business × industry) AND carries
    aggregate rows in every dimension, so summing naively over-counts ~20×. We instead pick
    the single fully-aggregated cell per month: State=Australia · Type=Total personal
    insolvencies · Business involvement=Total · Industry=Total. Matched by column name."""
    try:
        pkg = _get(AFSA_PKG).json()["result"]
        url = next(r["url"] for r in pkg["resources"]
                   if (r.get("format") or "").upper() == "CSV" and "time" in r["url"].lower())
        rows = list(csv.reader(io.StringIO(_get(url, timeout=90).text)))   # ~30MB file
        head = [h.strip().lower() for h in rows[0]]

        def col(*subs):
            return next((i for i, h in enumerate(head) if all(s in h for s in subs)), None)

        def first(*cands):
            return next((c for c in cands if c is not None), None)

        di = first(col("month"), col("date"), col("period"), 0)
        si, ti = col("state"), col("type")
        bi, ii = col("business"), col("industry")
        ci = first(col("number", "insolvenc"), col("number"), col("count"), len(head) - 1)
        monthly = {}
        for r in rows[1:]:
            if len(r) <= ci:
                continue
            if si is not None and r[si].strip() != "Australia":
                continue
            if ti is not None and r[ti].strip() != "Total personal insolvencies":
                continue
            if bi is not None and r[bi].strip() != "Total":
                continue
            if ii is not None and r[ii].strip() != "Total":
                continue
            d = _pdate(r[di])
            try:
                v = float((r[ci] or "").replace(",", ""))
            except ValueError:
                continue
            if d is not None:
                monthly[d.strftime("%Y-%m")] = v      # one aggregated cell per month
        out = [(_pdate(k), v) for k, v in monthly.items() if _pdate(k)]
        out.sort(key=lambda x: x[0])
        return out
    except Exception:
        return []


def _asic_insolvencies():
    """ASIC Series 1 — companies entering external administration for the first time,
    monthly (the widely-quoted 'company insolvencies/bankruptcies' series). The xlsx URL
    rotates each release, so resolve the latest from the statistics page; drop the partial
    current month using the file's own 'Data to' date so YoY isn't distorted."""
    try:
        page = _get(ASIC_STATS, timeout=30).text
        m = re.search(r'https://download\.asic\.gov\.au/media/[^"]*series-1[^"]*\.xlsx', page, re.I)
        if not m:
            return []
        import openpyxl
        raw = _get(m.group(0), timeout=90).content     # ~17MB — needs the longer cap
        wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
        # 'Data to' cutoff (Contents sheet) → the current month may be incomplete
        dto = None
        for r in list(wb["Contents"].iter_rows(values_only=True))[:14]:
            for j, c in enumerate(r[:-1]):
                if isinstance(c, str) and c.strip().lower().startswith("data to"):
                    dto = r[j + 1]
        rows = list(wb["1.1"].iter_rows(values_only=True))
        hdr = rows[11]
        tc = next((j for j, h in enumerate(hdr) if isinstance(h, str) and h.strip().lower() == "total"), None)
        if tc is None:
            return []
        yc, mc = 2, 4      # Period(year), Period(month) columns in Table 1.1
        out = []
        for r in rows[12:]:
            if len(r) <= tc or not r[mc] or r[yc] is None:
                continue
            mi = _MON.get(str(r[mc]).strip()[:3].lower())
            if not mi:
                continue
            try:
                y = int(r[yc])
            except (ValueError, TypeError):
                continue
            if isinstance(r[tc], (int, float)):
                out.append((datetime(y, mi, 1), float(r[tc])))
        out.sort(key=lambda x: x[0])
        # drop the trailing partial month (Data-to day before the month's end)
        if dto and out and out[-1][0].year == dto.year and out[-1][0].month == dto.month \
                and dto.day < calendar.monthrange(dto.year, dto.month)[1]:
            out = out[:-1]
        return out
    except Exception:
        return []


# ---- rule-based state per indicator ----
def _dir_state(pairs, months=12, up_is="headwind", eps=0.05):
    """Direction over N months; falling rates = tailwind, rising = headwind (configurable)."""
    if len(pairs) < 2:
        return None, None
    now = pairs[-1][1]
    past = pairs[max(0, len(pairs) - 1 - months)][1]
    chg = round(now - past, 2)
    if abs(chg) < eps:
        return "neutral", chg
    rising = chg > 0
    return (up_is if rising else ("tailwind" if up_is == "headwind" else "headwind")), chg


def _sahm(pairs):
    """Sahm rule: current 3-month average unemployment minus the LOW of the 3-month
    averages over the prior 12 months. ≥0.5pp is the published recession trigger."""
    if len(pairs) < 16:
        return None, None
    vals = [v for _, v in pairs]
    ma = [sum(vals[i - 2:i + 1]) / 3 for i in range(2, len(vals))]   # 3-mo moving averages
    gap = round(ma[-1] - min(ma[-13:-1]), 2)                          # vs low of prior 12 MAs
    if gap >= 0.5:
        return "headwind", gap        # Sahm recession trigger met
    if gap >= 0.2:
        return "neutral", gap         # Sersi's own near-trigger tier (not part of Sahm)
    return "tailwind", gap


def _yoy_state(pairs, hi=10, lo=-10):
    if len(pairs) < 13:
        return None, None
    now, yr = pairs[-1][1], pairs[-13][1]
    if yr <= 0:
        return None, None
    pct = round((now / yr - 1) * 100, 1)
    if pct > hi:
        return "headwind", pct
    if pct < lo:
        return "tailwind", pct
    return "neutral", pct


def build_macro() -> dict:
    """Assemble the macro dashboard: per-indicator series + state + rule, plus a composite.

    Only THREE indicators vote (rates via the cash rate, unemployment, insolvencies); the
    mortgage rate is shown as context and household debt as an amplifier, neither votes.
    Stale indicators (>~3 months behind the freshest) are badged and drop out of the vote."""
    cash = _rba_series("f1.1", "FIRMMCRT")
    mort = _rba_series("f6", "FLRHOOTA")
    dti = _rba_series("e2", "BHFDDIT")
    unemp = _unemployment()
    insol = _insolvencies()
    asic = _asic_insolvencies()

    inds = []

    def add(key, label, unit, source, pairs, state, metric, rule, votes, series_n=132):
        if not pairs:
            return
        inds.append({
            "key": key, "label": label, "unit": unit, "source": source,
            "series": _series(pairs, series_n), "current": round(pairs[-1][1], 2),
            "last_dt": pairs[-1][0], "asof": pairs[-1][0].strftime("%b %Y"),
            "state": state, "metric": metric, "rule": rule, "vote": votes,
        })

    if cash:
        st, chg = _dir_state(cash, 12, up_is="headwind")
        add("cash", "RBA cash rate (monthly avg)", "%", "Reserve Bank of Australia (F1.1)", cash, st,
            f"{chg:+.2f}pp / 12mo" if chg is not None else "—",
            "The policy lever: rate cuts lift borrowing capacity and prices tend to follow with a lag; "
            "hikes do the reverse. This is the voting 'interest rates' signal.", votes=True)
    if mort:
        st, chg = _dir_state(mort, 12, up_is="headwind")
        add("mortgage", "Mortgage rate (owner-occ)", "%", "Reserve Bank of Australia (F6)", mort, st,
            f"{chg:+.2f}pp / 12mo" if chg is not None else "—",
            "Context for the cash rate — the actual cost of servicing a loan. Moves with the cash rate, "
            "so it isn't counted a second time in the composite.", votes=False)
    if unemp:
        st, gap = _sahm(unemp)
        add("unemployment", "Unemployment rate", "%", "ABS Labour Force (seasonally adj.)", unemp, st,
            f"+{gap:.2f}pp vs 12mo low" if gap is not None else "—",
            "Sahm rule: when the 3-month average rises ≥0.5pp above the low of its prior-12-month 3-month "
            "averages, it has historically coincided with recessions. (0.2–0.5pp = Sersi's near-trigger tier.)",
            votes=True)
    if insol:
        st, pct = _yoy_state(insol)
        add("insolvencies", "Personal insolvencies", "/mo", "AFSA (monthly, personal)", insol, st,
            f"{pct:+.1f}% YoY" if pct is not None else "—",
            "Household financial stress that can precede forced sales — the voting insolvency signal. "
            "State = year-on-year change (>+10% headwind, <-10% tailwind).", votes=True)
    if asic:
        st, pct = _yoy_state(asic)
        add("asic", "Company insolvencies (ASIC)", "/mo", "ASIC Series 1 (companies entering ext. admin.)",
            asic, st, f"{pct:+.1f}% YoY" if pct is not None else "—",
            "Context — the widely-quoted 'company insolvencies' series (Series 1: companies entering "
            "external administration). Business failures flow through to jobs and confidence, but it isn't "
            "counted a second time in the composite; the personal-insolvency signal above carries the vote.",
            votes=False)
    if dti:
        cur = dti[-1][1]
        hist_max = max(v for _, v in dti)
        band = "near record" if cur >= hist_max * 0.97 else ("elevated" if cur >= hist_max * 0.85 else "moderate")
        add("dti", "Household debt-to-income", "% of income", "Reserve Bank of Australia (E2)", dti,
            "amplifier", band,
            "Not a buy/sell signal on its own — high household leverage amplifies how sharply the other "
            "indicators feed through to prices (more forced sellers when rates or unemployment rise).",
            votes=False, series_n=120)

    # Staleness: any indicator trailing the freshest by >~3 months is badged and drops its vote.
    if inds:
        freshest = max(i["last_dt"] for i in inds)
        for i in inds:
            i["stale"] = (freshest - i["last_dt"]).days > 95
            if i["stale"]:
                i["vote"] = False
            i.pop("last_dt", None)

    votes = [i for i in inds if i["vote"]]
    head = sum(1 for i in votes if i["state"] == "headwind")
    tail = sum(1 for i in votes if i["state"] == "tailwind")
    n = len(votes)
    if n and head > n / 2:
        read = (f"{head} of {n} voting signals are headwinds — historically associated with a softer, "
                "later-cycle housing backdrop.")
    elif n and tail > n / 2:
        read = (f"{tail} of {n} voting signals are tailwinds — historically associated with improving "
                "borrowing capacity and firmer demand (earlier-cycle conditions).")
    elif n:
        read = (f"Signals are mixed ({head} headwind / {tail} tailwind of {n}) — historically a "
                "transitional backdrop with no clear cycle lean.")
    else:
        read = ""

    return {
        "indicators": inds,
        "composite": {"headwinds": head, "tailwinds": tail, "n": n, "read": read},
        "asof": max((i["asof"] for i in inds), key=lambda s: datetime.strptime(s, "%b %Y"), default=""),
    }


if __name__ == "__main__":
    m = build_macro()
    print(f"Macro as of {m['asof']} — {m['composite']['read']}\n")
    for i in m["indicators"]:
        print(f"  {i['label']:28} {i['current']:>7}{i['unit']:<5} {i['state']:>9}  ({i['metric']})")
