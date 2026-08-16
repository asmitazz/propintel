"""ABS "Data by Region" adapter (ABS_REGIONAL_ASGS2021).

The single richest free proptech dataset in Australia — per-SA2 indicators
sourced from ABS + state valuers-general. We pull the measures that drive a
pure macro-fundamentals model:

  HOUSES_3      Median price of established house transfers ($)      [valuers-general]
  HOUSES_5      Median price of attached (townhouse/unit) transfers ($)
  RENT_4        Median weekly household rental payment ($)           [Census]
  MIGRATION_4   Net internal migration (no.)
  MIGRATION_7   Net overseas migration (no.)
  BUILDING_2    Private-sector house approvals (no.)
  BUILDING_4    Total dwelling-unit approvals (no.)
  DWELLSTOCK_13 Total estimated dwelling stock (no.)
  INCOME_17     Median total income excl. gov. transfers ($)
  LF_4          Unemployment rate (%)
  TENURE_4      Occupied dwellings rented (%)
  CAPGAINS_3    Median value of gross capital gains ($)

Caveat: RENT_4 is Census *rent paid* (all sitting tenancies), so it runs ~1-2pp
below current market asking rent — excellent for RELATIVE yield ranking, but
absolute yields read low. Flagged wherever yield is surfaced.
"""
from __future__ import annotations

from collections import defaultdict

from .abs_client import fetch_observations

MEASURES = {
    "HOUSES_3": "median_house_price",
    "HOUSES_5": "median_attached_price",
    "RENT_4": "median_weekly_rent",
    "MIGRATION_4": "net_internal_migration",
    "MIGRATION_7": "net_overseas_migration",
    "BUILDING_2": "house_approvals",
    "BUILDING_4": "total_dwelling_approvals",
    "DWELLSTOCK_13": "dwelling_stock",
    "INCOME_17": "median_income",
    "INCOME_2": "employee_income",      # ATO median employee income — annual time series
    "LF_4": "unemployment_rate",
    "TENURE_4": "pct_rented",
    "CAPGAINS_3": "median_capital_gains",
}


def pull_region_indicators(
    region_type: str = "SA2", start_period: str = "2018"
) -> dict[str, dict]:
    """Return {sa2_code: {"name": str, metrics: {friendly_metric: {year: value}}}}."""
    key = "+".join(MEASURES) + f".{region_type}..A"
    obs, names = fetch_observations(
        "ABS_REGIONAL_ASGS2021", key=key, params={"startPeriod": start_period}, timeout=150
    )
    out: dict[str, dict] = {}
    for row in obs:
        code = row.get("ASGS_2021")
        meas = row.get("MEASURE")
        if not code or meas not in MEASURES:
            continue
        rec = out.setdefault(code, {"name": names.get(code, ""), "metrics": defaultdict(dict)})
        rec["metrics"][MEASURES[meas]][row["TIME_PERIOD"]] = row["VALUE"]
    return out


def latest(series: dict) -> float | None:
    """Latest non-null value from a {year: value} series."""
    if not series:
        return None
    for yr in sorted(series, reverse=True):
        if series[yr] is not None:
            return series[yr]
    return None


def pull_tenure(region_type: str = "SA2") -> dict[str, dict]:
    """Owner-occupier tenure by SA2, at both the 2016 and 2021 Census points so we
    can show *traction* (the change in owner-occupier share), not just a snapshot.

      TENURE_2  Owned outright (%)
      TENURE_3  Owned with a mortgage (%)     <- the recent owner-occupier buyers
      TENURE_4  Rented (%)

    Owner-occupier share = outright + mortgage (national ~66%). A rising share
    alongside positive net internal migration signals owner-occupier-led demand —
    generally more stable and growth-supportive than an investor-churned market.
    Kept as a SEPARATE small pull (startPeriod=2016) so it can't bloat the big
    combined indicator key or trip its timeout.
    """
    key = "TENURE_2+TENURE_3+TENURE_4." + region_type + "..A"
    obs, _ = fetch_observations(
        "ABS_REGIONAL_ASGS2021", key=key, params={"startPeriod": "2016"}, timeout=120
    )
    by: dict[str, dict] = defaultdict(lambda: defaultdict(dict))
    for r in obs:
        code, meas, val = r.get("ASGS_2021"), r.get("MEASURE"), r.get("VALUE")
        if code and meas in ("TENURE_2", "TENURE_3", "TENURE_4") and val is not None:
            by[code][meas][r["TIME_PERIOD"]] = val
    out: dict[str, dict] = {}
    for code, mm in by.items():
        def oo(yr: str) -> float | None:
            a, b = mm.get("TENURE_2", {}).get(yr), mm.get("TENURE_3", {}).get(yr)
            return round(a + b, 1) if (a is not None and b is not None) else None
        oo21, oo16 = oo("2021"), oo("2016")
        out[code] = {
            "owner_occupier_pct": oo21,
            "owner_occupier_delta": (round(oo21 - oo16, 1)
                                     if (oo21 is not None and oo16 is not None) else None),
            "owned_mortgage_pct": mm.get("TENURE_3", {}).get("2021"),
        }
    return out


# ANZSIC divisions (EMP_IND_2..20) as % of employed residents, per SA2.
INDUSTRY = {
    2: "Agriculture", 3: "Mining", 4: "Manufacturing", 5: "Utilities",
    6: "Construction", 7: "Wholesale", 8: "Retail", 9: "Accommodation/Food",
    10: "Transport", 11: "Info/Media", 12: "Financial", 13: "Real estate",
    14: "Professional", 15: "Admin/Support", 16: "Public admin", 17: "Education",
    18: "Health care", 19: "Arts", 20: "Other services",
}
# Sectors whose local dominance drives property boom/bust (commodity/cyclical).
COMMODITY_INDUSTRIES = {2, 3}  # Agriculture, Mining
# "Knowledge/anchor" service industries that underpin resilient, diversifying
# economies (health, education, public admin, professional, finance, info/media).
ANCHOR_INDUSTRIES = {11, 12, 14, 16, 17, 18}


def pull_industry_diversity(region_type: str = "SA2") -> dict[str, dict]:
    """Return {sa2_code: {diversity_index, effective_industries, top_industry,
    top_share, commodity_exposure, resilience, risk}} from Census industry shares.

    diversity_index  — Shannon entropy normalised 0..1 (1 = perfectly even spread)
    effective_industries — inverse Simpson (1/HHI): how many industries "effectively"
    commodity_exposure — Mining + Agriculture share (%) — the classic single-town risk
    resilience — diversity*100 minus commodity penalty (higher = safer)
    risk — 'High' | 'Elevated' | 'Diversified'
    """
    import math
    from collections import defaultdict

    key = "+".join(f"EMP_IND_{i}" for i in INDUSTRY) + f".{region_type}..A"
    obs, _ = fetch_observations("ABS_REGIONAL_ASGS2021", key=key,
                                params={"startPeriod": "2021"}, timeout=150)
    shares: dict[str, dict[int, float]] = defaultdict(dict)
    for r in obs:
        m = r.get("MEASURE", "")
        if not m.startswith("EMP_IND_") or r["VALUE"] is None:
            continue
        num = int(m.split("_")[-1])
        if num in INDUSTRY:
            shares[r["ASGS_2021"]][num] = r["VALUE"]

    out: dict[str, dict] = {}
    for code, sh in shares.items():
        tot = sum(sh.values())
        if tot <= 0:
            continue
        ps = {i: v / tot for i, v in sh.items() if v > 0}
        entropy = -sum(p * math.log(p) for p in ps.values())
        diversity = entropy / math.log(len(INDUSTRY))
        hhi = sum(p * p for p in ps.values())
        eff_n = 1 / hhi if hhi else 0
        top_i = max(sh, key=sh.get)
        top_share = sh[top_i] / tot * 100
        commodity = sum(sh.get(i, 0) for i in COMMODITY_INDUSTRIES) / tot * 100
        anchor = sum(sh.get(i, 0) for i in ANCHOR_INDUSTRIES) / tot * 100
        top3 = [(INDUSTRY[i], round(sh[i] / tot * 100, 1))
                for i in sorted(sh, key=sh.get, reverse=True)[:3]]
        if commodity >= 20 or top_share >= 30 or eff_n < 6:
            risk = "High"
        elif commodity >= 12 or top_share >= 26 or eff_n < 8:
            risk = "Elevated"
        else:
            risk = "Diversified"
        # economic base label: what kind of economy underpins demand
        if commodity >= 15:
            base = "Commodity-exposed"
        elif anchor >= 45:
            base = "Knowledge/anchor-led"
        else:
            base = "Broad/mixed"
        out[code] = {
            "diversity_index": round(diversity, 3),
            "effective_industries": round(eff_n, 1),
            "top_industry": INDUSTRY[top_i],
            "top_share": round(top_share, 1),
            "top3_industries": top3,
            "commodity_exposure": round(commodity, 1),
            "anchor_exposure": round(anchor, 1),
            "econ_base": base,
            "resilience": round(diversity * 100 - commodity, 1),
            "risk": risk,
        }
    return out


def pull_seifa() -> dict[str, float]:
    """Return {sa2_code: IRSAD score} — ABS SEIFA 2021 Index of Relative
    Socio-economic Advantage and Disadvantage (mean ~1000; lower = more
    disadvantaged). The base signal for the gentrification-potential lens.
    """
    obs, _ = fetch_observations("ABS_SEIFA2021_SA2", key=".IRSAD.SCORE", params={}, timeout=90)
    return {r["ASGS_2021"]: r["VALUE"] for r in obs if r["VALUE"] is not None}


def cagr(series: dict) -> float | None:
    """Compound annual growth rate (%) across the span of a {year: value} series."""
    yrs = sorted(y for y, v in series.items() if v is not None)
    if len(yrs) < 2:
        return None
    a, b = series[yrs[0]], series[yrs[-1]]
    n = int(yrs[-1]) - int(yrs[0])
    if a and b and a > 0 and n > 0:
        return ((b / a) ** (1 / n) - 1) * 100
    return None
