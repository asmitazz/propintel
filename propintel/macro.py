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
import json
import re
from datetime import datetime

from curl_cffi import requests as cf

from .config import ROOT

RBA = "https://www.rba.gov.au/statistics/tables/csv/{}-data.csv"
ABS_LF = ("https://data.api.abs.gov.au/rest/data/LF/M13.3.1599.20.AUS.M"
          "?startPeriod=2015-01&dimensionAtObservation=AllDimensions")
# Household Spending Indicator: Total · Australia · seasonally adj · through-the-year % change.
ABS_HSI = ("https://data.api.abs.gov.au/rest/data/HSI_M/9.TOT.CUR.20.AUS.M"
           "?startPeriod=2020-01&dimensionAtObservation=AllDimensions")
AFSA_PKG = ("https://data.gov.au/data/api/3/action/package_show?id="
            "4174f850-3d50-4b07-ae1d-4eb38e628bb4")
ASIC_STATS = ("https://asic.gov.au/regulatory-resources/find-a-document/statistics/"
              "insolvency-statistics/")
APRA_STATS = "https://www.apra.gov.au/quarterly-authorised-deposit-taking-institution-statistics"
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


def _month_end(pairs):
    """Collapse a DAILY series to one point per calendar month (last obs wins — pairs are
    sorted ascending), so daily RBA tables chart on the same monthly basis as the rest."""
    out = {}
    for d, v in pairs:
        out[(d.year, d.month)] = (d, v)
    return [out[k] for k in sorted(out)]


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


def _abs_sdmx(url):
    """Parse a single-series ABS Data-API SDMX-JSON response (AllDimensions) → [(date, value)]."""
    d = _get(url, headers={"Accept": "application/vnd.sdmx.data+json"}).json()
    struct = d["data"]["structures"][0]
    dims = struct["dimensions"]["observation"]
    time_dim = next(x for x in dims if x["id"] == "TIME_PERIOD")
    periods = [v["id"] for v in time_dim["values"]]
    ti = dims.index(time_dim)
    out = []
    for key, val in d["data"]["dataSets"][0]["observations"].items():
        idx = int(key.split(":")[ti])
        dt = _pdate(periods[idx])
        if dt is not None and val and val[0] is not None:
            out.append((dt, round(float(val[0]), 2)))
    out.sort(key=lambda x: x[0])
    return out


def _household_spending():
    """ABS Household Spending Indicator (HSI_M) — total, Australia, seasonally adjusted,
    through-the-year % change. The free consumer-DEMAND read (pairs with the sentiment index,
    which is how people FEEL vs. what they actually spend)."""
    try:
        return _abs_sdmx(ABS_HSI)
    except Exception:
        return []


def _consumer_sentiment():
    """Westpac–Melbourne Institute Consumer Sentiment Index, read from the manually-maintained
    data/consumer_sentiment.json. The full back-series is a paid Melbourne Institute (CASiE)
    licence, but the monthly headline is published free in the Westpac release — so this one
    indicator is updated by hand each month (see the file's `note`), not auto-fetched.
    100 = neutral (optimists balance pessimists); long-run average ~100."""
    try:
        d = json.loads((ROOT / "data" / "consumer_sentiment.json").read_text())
        out = []
        for ym, val in d.get("series", []):
            dt = _pdate(ym)
            if dt is not None and val is not None:
                out.append((dt, round(float(val), 1)))
        out.sort(key=lambda x: x[0])
        return out
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


def _apra_investor_share():
    """APRA Quarterly ADI Property Exposures (Tab 1b, residential term loans by purpose) —
    the INVESTOR share of the housing loan book = investment / (owner-occupied + investment),
    quarterly. A rising share = demand being driven more by investors (speculative, late-cycle,
    tends to unwind faster); a falling share = owner-occupier-led (buyers who live in the home,
    don't sell on a dip — a steadier base). Source flagged by DwellDelta's data page. The xlsx
    URL rotates each release, so resolve the latest 'property exposures statistics' file."""
    try:
        page = _get(APRA_STATS, timeout=30).text
        m = re.search(r'href="([^"]*property%20exposures%20statistics[^"]*\.xlsx)"', page, re.I)
        if not m:
            return []
        url = m.group(1)
        if not url.startswith("http"):
            url = "https://www.apra.gov.au" + url
        import openpyxl
        raw = _get(url, timeout=90).content
        wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
        rows = list(wb["Tab 1b"].iter_rows(values_only=True))
        dcols = next(([(j, c) for j, c in enumerate(row) if isinstance(c, datetime)]
                      for row in rows[:8]
                      if sum(isinstance(c, datetime) for c in row) >= 4), None)
        if not dcols:
            return []

        def _row(label):   # first row whose first cell is exactly this label (term-loans split)
            return next((row for row in rows
                         if row and isinstance(row[0], str) and row[0].strip().lower() == label), None)
        oo, inv = _row("owner-occupied"), _row("investment")
        if not oo or not inv:
            return []
        out = []
        for j, d in dcols:
            try:
                o, i = float(oo[j]), float(inv[j])
            except (TypeError, ValueError, IndexError):
                continue
            if o + i > 0:
                out.append((d, round(i / (o + i) * 100, 2)))
        out.sort(key=lambda x: x[0])
        return out
    except Exception:
        return []


def _apra_arrears():
    """APRA Quarterly ADI Performance (Tab 1d, asset quality) — NON-PERFORMING loans as a share
    of gross loans = non-performing ÷ gross loans & advances, quarterly. The post-2022 standard
    gauge of bank bad loans (90+ days past due or impaired). It's ALL ADI loans, not housing-
    only, but housing is ~two-thirds of the book, so it's the closest free read on mortgage
    stress/arrears. Rising = more households behind on payments → forced-sale risk. (The older
    'past due to loans' ratio was discontinued in the 2022 reporting change.)"""
    try:
        page = _get(APRA_STATS, timeout=30).text
        m = re.search(r'href="([^"]*institution%20performance[^"]*\.xlsx)"', page, re.I)
        if not m:
            return []
        url = m.group(1)
        if not url.startswith("http"):
            url = "https://www.apra.gov.au" + url
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(_get(url, timeout=90).content), read_only=True, data_only=True)
        rows = list(wb["Tab 1d"].iter_rows(values_only=True))
        _isdate = lambda c: bool(isinstance(c, str) and re.match(r"[A-Z][a-z]{2} \d{4}", c.strip()))
        drow = next((rows[ri] for ri in range(2, 6) if sum(_isdate(c) for c in rows[ri]) >= 4), None)

        def _row(label):
            return next((row for row in rows if row and isinstance(row[0], str) and row[0].strip().lower() == label), None)
        npf, gross = _row("non-performing"), _row("gross loans and advances")
        if not drow or not npf or not gross:
            return []
        out = []
        for j, c in enumerate(drow):
            if not _isdate(c):
                continue
            try:
                n, g = float(npf[j]), float(gross[j])
            except (TypeError, ValueError, IndexError):
                continue
            mi = _MON.get(c.strip()[:3].lower())
            if mi and g > 0:
                out.append((datetime(int(c.strip()[-4:]), mi, 1), round(n / g * 100, 2)))
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
    b10 = _month_end(_rba_series("f2", "FCMYGBAG10D"))   # 10yr Commonwealth Govt bond (daily→monthly)
    b2 = _month_end(_rba_series("f2", "FCMYGBAG2D"))      # 2yr bond — near-term rate-expectations read
    dti = _rba_series("e2", "BHFDDIT")
    cpi = _rba_series("g1", "GCPIAGYP")        # headline year-ended CPI inflation
    hvtinc = [(d, v / 100) for d, v in _rba_series("e2", "BHFHDI")]   # housing value ÷ income, as ×
    spending = _household_spending()           # ABS consumer spending, through-the-year %
    sentiment = _consumer_sentiment()          # Westpac–Melbourne Institute, manual monthly
    unemp = _unemployment()
    insol = _insolvencies()
    asic = _asic_insolvencies()
    apra = _apra_investor_share()
    arrears = _apra_arrears()

    inds = []

    def _dir_word(state, up="rising", down="falling", flat="steady"):
        return {"headwind": up, "tailwind": down, "neutral": flat}.get(state, "")

    def add(key, label, unit, source, pairs, state, metric, rule, plain, votes, series_n=132):
        if not pairs:
            return
        inds.append({
            "key": key, "label": label, "unit": unit, "source": source,
            "series": _series(pairs, series_n), "current": round(pairs[-1][1], 2),
            "last_dt": pairs[-1][0], "asof": pairs[-1][0].strftime("%b %Y"),
            "state": state, "metric": metric, "rule": rule, "plain": plain, "vote": votes,
        })

    if cash:
        st, chg = _dir_state(cash, 12, up_is="headwind")
        plain = ("The Reserve Bank's official interest rate — the master dial for how expensive it is to borrow in "
                 "Australia. " + ("It's been <b>rising</b>, so loans cost more, buyers can borrow less, and price growth "
                 "usually cools." if st == "headwind" else "It's been <b>falling</b>, so loans get cheaper, buyers can "
                 "borrow more, and prices usually firm up (with a lag)." if st == "tailwind" else "It's been roughly "
                 "<b>flat</b> — no fresh push either way."))
        add("cash", "RBA cash rate (monthly avg)", "%", "Reserve Bank of Australia (F1.1)", cash, st,
            f"{chg:+.2f}pp / 12mo" if chg is not None else "—",
            "The policy lever: rate cuts lift borrowing capacity and prices tend to follow with a lag; "
            "hikes do the reverse. This is the voting 'interest rates' signal.", plain, votes=True)
    if mort:
        st, chg = _dir_state(mort, 12, up_is="headwind")
        add("mortgage", "Mortgage rate (owner-occ)", "%", "Reserve Bank of Australia (F6)", mort, st,
            f"{chg:+.2f}pp / 12mo" if chg is not None else "—",
            "Context for the cash rate — the actual cost of servicing a loan. Moves with the cash rate, "
            "so it isn't counted a second time in the composite.",
            "What a typical home loan actually costs. When this is higher, monthly repayments are bigger and "
            "people can borrow less — so fewer buyers can compete and demand softens. It follows the cash rate.",
            votes=False)
    if b10:
        st, chg = _dir_state(b10, 12, up_is="headwind")
        spread = round(b10[-1][1] - cash[-1][1], 2) if cash else None
        if spread is None:
            shape = curve = ""
        elif spread < 0:
            shape, curve = f"spread {spread:+.1f}pp vs cash (inverted — caution)", "an inverted curve, historically a caution sign"
        elif spread < 0.5:
            shape, curve = f"spread {spread:+.1f}pp vs cash (flat)", "a flat curve"
        else:
            shape, curve = f"spread {spread:+.1f}pp vs cash (steep)", "a normal, upward-sloping curve"
        move = _dir_word(st, up="rising", down="falling", flat="steady")
        if st == "headwind":
            tail_txt = ("When long yields rise like this, markets aren't betting on cheap money returning soon, so "
                        "the broader cost of borrowing stays high — a headwind for prices until it turns.")
        elif st == "tailwind":
            tail_txt = ("Falling long yields mean markets are starting to price lower rates and inflation ahead, "
                        "which tends to ease the cost of borrowing and support prices with a lag.")
        else:
            tail_txt = "A steady long yield means the market sees no big change in the rate and inflation outlook."
        add("bonds", "10-year bond yield", "%", "Reserve Bank of Australia (F2, FCMYGBAG10D)", b10, st,
            f"{b10[-1][1]:.1f}% · {shape}" if shape else f"{b10[-1][1]:.1f}%",
            "The yield on 10-year Australian Government bonds — the market's own read on where interest rates and "
            "inflation sit over the long run (it moves ahead of the RBA). Fixed home loans here are mostly 1–5 "
            "year terms priced off shorter-term swap rates, so the 10yr isn't their direct anchor — it's the "
            "long-run expectations gauge that sets the tone for the whole cost of capital. Rising = markets "
            "expect higher-for-longer; falling = they're pricing relief. The term spread (10yr minus the cash "
            "rate) is the yield-curve read: a flat or inverted (negative) spread has historically been a caution "
            "sign, though it's noisier in Australia than in the US. Context, no vote.",
            f"What the bond market expects interest rates and inflation to do over the long run — the base tone "
            f"for the whole cost of borrowing. It's currently <b>{move}</b> at <b>{b10[-1][1]:.1f}%</b>" +
            (f", <b>{abs(spread):.1f}pp {'above' if spread >= 0 else 'below'}</b> the cash rate ({curve})"
             if spread is not None else "") + ". " + tail_txt,
            votes=False, series_n=120)
    if cpi:
        cur = cpi[-1][1]
        st = ("headwind" if cur >= 3.2 else "tailwind" if cur <= 2.5 else "neutral")
        band = ("<b>above</b> the RBA's 2–3% target" if cur > 3.0 else
                "<b>below</b> the 2–3% target" if cur < 2.0 else "<b>within</b> the 2–3% target")
        add("cpi", "Inflation (CPI, year-ended)", "%", "Reserve Bank of Australia / ABS (G1, GCPIAGYP)", cpi, st,
            f"{cur:.1f}% ({band.replace('<b>','').replace('</b>','')})",
            "Headline consumer-price inflation, year-ended. It decides where the cash rate goes next: above "
            "the 2–3% target keeps the RBA cautious (fewer/later cuts = a headwind); back within/below target "
            "opens room to cut (a tailwind). Context — the cash rate carries the actual vote.",
            "How fast the cost of living is rising. It's the single thing the Reserve Bank watches to set interest "
            f"rates: while it sits {band}, the RBA is less willing to cut — and rate cuts are the main fuel for "
            "borrowing power and prices. So high inflation now usually means rate relief (and a price recovery) is "
            "further off.", votes=False, series_n=60)
    if spending:
        cur = spending[-1][1]
        st = ("tailwind" if cur >= 3.0 else "headwind" if cur <= 1.0 else "neutral")
        move = _dir_word(st, up="growing solidly", down="soft", flat="moderate")
        add("spending", "Consumer spending (ABS)", "% YoY", "ABS Household Spending Indicator (total, SA)",
            spending, st, f"{cur:+.1f}% YoY",
            "Household spending, through-the-year — what people actually do with their money, the companion to "
            "the sentiment index (how they feel). Solid spending = confident households and firmer housing "
            "demand; a pull-back is an early warning. Context, no vote.",
            f"How much households are actually spending versus a year ago — a real-money read on confidence. "
            f"When spending holds up, buyers are comfortable and housing demand stays firmer; when it stalls, "
            f"people pull their horns in and demand softens. It's currently <b>{move}</b> at <b>{cur:+.1f}%</b>.",
            votes=False, series_n=48)
    if sentiment:
        cur = sentiment[-1][1]
        st = ("tailwind" if cur >= 100 else "headwind" if cur < 90 else "neutral")
        mood = ("<b>optimistic</b> (above the neutral 100 line)" if cur >= 100 else
                "<b>deeply pessimistic</b>" if cur < 85 else
                "<b>pessimistic</b>" if cur < 95 else "<b>cautious</b> (near the neutral 100 line)")
        # note the feel-vs-spend gap when both are present
        gap = ""
        if spending and spending[-1][1] >= 3.0 and cur < 95:
            gap = (f" Worth noting: households say they feel gloomy, yet actual spending is still "
                   f"growing (<b>{spending[-1][1]:+.1f}%</b>) — what people do is holding up better than how "
                   "they say they feel.")
        add("sentiment", "Consumer sentiment (Westpac–MI)", "index", "Westpac–Melbourne Institute (headline, manual monthly)",
            sentiment, st, f"{cur:.1f} ({'optimists' if cur >= 100 else 'pessimists'} ahead; 100 = neutral)",
            "The Westpac–Melbourne Institute Consumer Sentiment Index. 100 = optimists and pessimists balance; "
            "below 100 means pessimists outnumber optimists. It leads actual spending and big-ticket decisions "
            "(including buying a home) by a few months, so it's an early read on housing demand. The monthly "
            "headline is free (the full back-series is a paid licence), so this one is updated by hand each month. "
            "Context, no vote.",
            f"A monthly survey of how confident households feel about their finances and the economy, where 100 is "
            f"the dividing line between optimists and pessimists. At <b>{cur:.1f}</b> consumers are {mood}. Confidence "
            "tends to move a few months ahead of what people actually do — when it's this low, buyers are more "
            "hesitant to commit to a purchase as big as a home, which weighs on demand until it recovers." + gap,
            votes=False, series_n=48)
    if hvtinc:
        cur = hvtinc[-1][1]
        prior = next((v for d, v in reversed(hvtinc) if (hvtinc[-1][0] - d).days >= 330), None)
        chg = (cur - prior) if prior is not None else None
        st = ("headwind" if chg is not None and chg >= 0.08 else "tailwind" if chg is not None and chg <= -0.08 else "neutral")
        move = _dir_word(st, up="still stretching", down="easing back", flat="broadly flat")
        add("hv_income", "Housing value vs income", "× income", "Reserve Bank of Australia (E2, housing assets ÷ income)",
            hvtinc, st, f"{cur:.1f}× income ({chg:+.1f}×/yr)" if chg is not None else f"{cur:.1f}× income",
            "Total value of Australia's homes ÷ household disposable income (RBA E2). Near record highs. This "
            "is the 'is land/housing sustainable?' gauge — when housing is this stretched vs income, prices lean "
            "on cheap credit and sentiment, with less room to rise and more to fall on a shock. Context, no vote.",
            f"How dear housing is compared with what households earn — about <b>{cur:.1f} times</b> a year's "
            "income nationally, near the highest on record. The higher this sits, the more prices depend on cheap "
            "borrowing to stay up, and the less headroom there is for further gains. It's "
            f"<b>{move}</b> versus a year ago.", votes=False, series_n=100)
    if unemp:
        st, gap = _sahm(unemp)
        near = "and it's now close to the level that has historically warned of a recession" if st == "neutral" \
            else "and it has crossed the level that has historically warned of a recession" if st == "headwind" \
            else "and it's still low"
        add("unemployment", "Unemployment rate", "%", "ABS Labour Force (seasonally adj.)", unemp, st,
            f"+{gap:.2f}pp vs 12mo low" if gap is not None else "—",
            "Sahm rule: when the 3-month average rises ≥0.5pp above the low of its prior-12-month 3-month "
            "averages, it has historically coincided with recessions. (0.2–0.5pp = Sersi's near-trigger tier.)",
            "How many people who want work can't find it. When it climbs, households feel less secure — that means "
            f"fewer confident buyers and more owners who may be forced to sell. It's been ticking up, {near}.",
            votes=True)
    if insol:
        st, pct = _yoy_state(insol)
        move = _dir_word(st, up="climbing", down="falling", flat="broadly flat")
        add("insolvencies", "Personal insolvencies", "/mo", "AFSA (monthly, personal)", insol, st,
            f"{pct:+.1f}% YoY" if pct is not None else "—",
            "Household financial stress that can precede forced sales — the voting insolvency signal. "
            "State = year-on-year change (>+10% headwind, <-10% tailwind).",
            f"How many ordinary people are going broke each month. Rising numbers mean more households in real "
            f"trouble (and potential forced sales); falling means people are coping. Right now it's <b>{move}</b> "
            "versus a year ago.", votes=True)
    if asic:
        st, pct = _yoy_state(asic)
        add("asic", "Company insolvencies (ASIC)", "/mo", "ASIC Series 1 (companies entering ext. admin.)",
            asic, st, f"{pct:+.1f}% YoY" if pct is not None else "—",
            "Context — the widely-quoted 'company insolvencies' series (Series 1: companies entering "
            "external administration). Business failures flow through to jobs and confidence, but it isn't "
            "counted a second time in the composite; the personal-insolvency signal above carries the vote.",
            "How many businesses are collapsing each month (this is the chart most people mean by 'bankruptcies'). "
            "A sharp jump like now means job losses and weaker confidence are likely coming — a warning light for "
            "the wider economy, even though it hits house prices less directly than household stress does.",
            votes=False)
    if dti:
        cur = dti[-1][1]
        hist_max = max(v for _, v in dti)
        band = "near record" if cur >= hist_max * 0.97 else ("elevated" if cur >= hist_max * 0.85 else "moderate")
        add("dti", "Household debt-to-income", "% of income", "Reserve Bank of Australia (E2)", dti,
            "amplifier", band,
            "Not a buy/sell signal on its own — high household leverage amplifies how sharply the other "
            "indicators feed through to prices (more forced sellers when rates or unemployment rise).",
            "How big households' debts are compared with what they earn — currently well over 1.5× income, among "
            "the highest in the world. It doesn't move much month to month, but because people are so stretched, "
            "any rise in rates or unemployment bites harder and faster than it would elsewhere.",
            votes=False, series_n=120)

    if apra:
        cur = apra[-1][1]
        prior = next((v for d, v in reversed(apra) if (apra[-1][0] - d).days >= 330), None)
        chg = (cur - prior) if prior is not None else None
        st = ("headwind" if chg is not None and chg >= 0.3        # investor share rising = speculative
              else "tailwind" if chg is not None and chg <= -0.3  # falling = owner-occupier-led
              else "neutral")
        move = _dir_word(st, up="rising", down="falling", flat="broadly flat")
        add("investor_share", "Investor share of housing credit", "% of home loans",
            "APRA Quarterly ADI Property Exposures (Tab 1b) · via DwellDelta", apra, st,
            f"{cur:.1f}% ({chg:+.1f}pp/yr)" if chg is not None else f"{cur:.1f}%",
            "Investor share of ADIs' housing loan book (investment ÷ owner-occupied + investment). "
            "Rising = demand driven more by investors (speculative, late-cycle); falling = owner-occupier-"
            "led (steadier). Context — it doesn't vote in the composite.",
            "What share of home loans are going to <b>investors</b> rather than people buying to live in. "
            "Owner-occupiers buy to live and rarely sell on a dip, so an owner-occupier-led market is steadier; "
            "when the investor share climbs, more of the buying is speculative and can unwind faster. Right now "
            f"it's <b>{move}</b> — investors are about <b>{cur:.0f}%</b> of the loan book.",
            votes=False, series_n=40)
    if arrears:
        cur = arrears[-1][1]
        prior = next((v for d, v in reversed(arrears) if (arrears[-1][0] - d).days >= 330), None)
        chg = (cur - prior) if prior is not None else None
        st = ("headwind" if chg is not None and chg >= 0.05       # bad loans rising = stress building
              else "tailwind" if chg is not None and chg <= -0.05 else "neutral")
        move = _dir_word(st, up="rising", down="easing", flat="broadly flat")
        add("arrears", "Bank loan arrears (non-performing)", "% of loans",
            "APRA Quarterly ADI Performance (Tab 1d) · via DwellDelta", arrears, st,
            f"{cur:.2f}% ({chg:+.2f}pp/yr)" if chg is not None else f"{cur:.2f}%",
            "Non-performing loans (90+ days past due or impaired) as a share of all ADI loans — the standard "
            "bank-stress gauge. Housing is ~⅔ of the book, so it tracks mortgage stress closely (it isn't "
            "housing-only). Rising = more borrowers behind → forced-sale risk. Context, doesn't vote.",
            "The share of bank loans where borrowers have fallen behind (90+ days) or gone bad. When this climbs, "
            "more households are in real trouble and some are forced to sell — which adds supply and drags prices. "
            f"It's a low <b>~{cur:.1f}%</b> right now and <b>{move}</b> versus a year ago.",
            votes=False, series_n=40)

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
        plain = ("In plain terms: most of the big economic dials are pushing against the market right now. "
                 "That has historically gone with slower, riskier price growth — a time to favour quality and be "
                 "patient on price rather than chase.")
    elif n and tail > n / 2:
        read = (f"{tail} of {n} voting signals are tailwinds — historically associated with improving "
                "borrowing capacity and firmer demand (earlier-cycle conditions).")
        plain = ("In plain terms: the wind is at buyers' backs — cheaper money and a steady economy have "
                 "historically supported firmer demand and rising prices. Conditions like these tend to reward "
                 "getting in earlier rather than waiting.")
    elif n:
        u_state = next((i["state"] for i in inds if i["key"] == "unemployment"), None)
        jobs_clause = ("but jobs and household finances are still holding up" if u_state == "tailwind"
                       else "and the jobs market is now softening too")
        read = (f"Signals are mixed ({head} headwind / {tail} tailwind of {n}) — historically a "
                "transitional backdrop with no clear cycle lean.")
        plain = ("In plain terms: the market isn't clearly rising or falling. Interest rates are the main "
                 f"pressure, {jobs_clause} — so it's more a 'watch closely and buy well' backdrop than a clear "
                 "signal to pile in or sit out.")
    else:
        read, plain = "", ""

    # Stagflation watch: inflation still hot AND unemployment rising together. Then the RBA's two
    # mandates (low inflation, full employment) pull opposite ways, so a rising jobless rate does not
    # reliably buy rate cuts. Fires only when both conditions hold (otherwise silent).
    stag, stag_note = False, ""
    cpi_hot = bool(cpi and cpi[-1][1] >= 3.2)
    ug = _sahm(unemp)[1] if unemp else None
    unemp_rising = ug is not None and ug >= 0.2
    if cpi_hot and unemp_rising:
        stag = True
        cpi_as = cpi[-1][0].strftime("%b %Y")
        un_as = unemp[-1][0].strftime("%b %Y")
        lvl = (f"has crossed the recession-warning level (+{ug:.2f}pp above its 12-month low, {un_as})"
               if ug >= 0.5 else
               f"is nearing the recession-warning level — +{ug:.2f}pp above its 12-month low ({un_as}), not yet triggered")
        bond_bit = ""
        if b2 and cash:
            sp2 = round(b2[-1][1] - cash[-1][1], 2)
            if sp2 > 0:
                bond_bit = (f" Bond markets agree relief isn't imminent — the 2-year yield ({b2[-1][1]:.1f}%) sits "
                            f"{sp2:.1f}pp above the cash rate, so no near-term cuts are priced.")
            else:
                bond_bit = (f" Bond markets, by contrast, are pricing cuts — the 2-year yield ({b2[-1][1]:.1f}%) is "
                            f"below the cash rate.")
        stag_note = (f"Stagflation watch: inflation is still above target (CPI {cpi[-1][1]:.1f}% year-ended to "
                     f"{cpi_as}) while unemployment {lvl}. When "
                     "these two rise together the RBA can't freely cut without re-stoking inflation, so a rising "
                     "jobless rate doesn't reliably mean rate relief is coming — the 1970s-style bind. A reason to "
                     "watch inflation, bond yields and unemployment together, not one as the other's trigger." + bond_bit)

    return {
        "indicators": inds,
        "composite": {"headwinds": head, "tailwinds": tail, "n": n, "read": read, "plain": plain,
                      "stagflation": stag, "stagflation_note": stag_note},
        "asof": max((i["asof"] for i in inds), key=lambda s: datetime.strptime(s, "%b %Y"), default=""),
    }


if __name__ == "__main__":
    m = build_macro()
    print(f"Macro as of {m['asof']} — {m['composite']['read']}\n")
    for i in m["indicators"]:
        print(f"  {i['label']:28} {i['current']:>7}{i['unit']:<5} {i['state']:>9}  ({i['metric']})")
