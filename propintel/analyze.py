"""Macro-fundamentals analysis engine (Domain-free).

Pulls ABS Data-by-Region + ERP, computes per-SA2 derived metrics and — separately
for **houses** and **townhouses/villas** (attached dwellings) — a composite
capital-growth score, then writes data/suburb_analysis.json (consumed by report).

Two strategies because the asset types behave differently: houses are a land /
capital-growth play (dearer, scarcer), townhouses & villas are a lower-entry,
higher-yield play with far more sub-$600k stock. Shared macro signals (population,
migration, supply, industry diversity, jobs) apply to both; price, yield,
affordability, price momentum and the price band are computed per asset type.

Scoring philosophy (forward-looking, trap-aware):
  - Reward yield + population growth + net migration + affordability runway.
  - Reward scarcity (few approvals per dwelling) and economic health (low unemp).
  - Reward industry diversity; penalise commodity single-industry exposure.
  - Gate on a minimum population so thin, illiquid towns don't top the list.
  - Percentile-normalise every component so one skewed metric can't dominate.
"""
from __future__ import annotations

import json
import math

from . import abs_client, abs_geo, abs_region
from .config import ROOT
from .db import connect, finish_run, now_iso, start_run, state_from_sa2

OUTPUT = ROOT / "data" / "suburb_analysis.json"

WEIGHTS = {
    "yield": 0.15,
    "population_growth": 0.12,
    "gentrification": 0.12,       # low socio-economic base × improvement momentum
    "net_migration": 0.10,
    "ripple": 0.10,              # priced below similar-income neighbours (arbitrage)
    "affordability": 0.09,
    "economic_resilience": 0.09,  # diverse / diversifying industries (single-industry risk)
    "supply_pressure": 0.08,     # committed dwelling-approval influx (5km), inverted
    "runway": 0.06,              # NOT already run (36mo growth <50% per framework)
    "economic_health": 0.04,
    "liquidity": 0.05,
}
MIN_POPULATION = 5000
NOWCAST_HORIZON_YEARS = 2.5           # ABS price vintage ~2024 (FY) -> ~Aug 2026
MARKET_YIELD_UPLIFT_PP = 1.4          # Census rent-paid -> ~market asking
OVERSUPPLY_RADIUS_KM = 5.0            # catchment for the supply-influx rule
OVERSUPPLY_MAX_INFLUX_PCT = 8.0      # >8% dwelling influx within radius -> rule out


def _pct(values: dict[str, float]) -> dict[str, float]:
    if not values:
        return {}
    order = sorted(values.items(), key=lambda kv: kv[1])
    n = len(order)
    return {k: (i / (n - 1) if n > 1 else 1.0) for i, (k, _) in enumerate(order)}


def _cycle(price_cagr, pop_growth, yield_) -> str:
    if price_cagr is None:
        return "Unknown"
    if price_cagr >= 12:
        return "Late"
    if price_cagr >= 6:
        return "Mid"
    if (pop_growth or 0) >= 1.0 or (yield_ or 0) >= 4.5:
        return "Early"
    return "Flat"


def _band(price) -> str:
    if price is None:
        return "unknown"
    if price < 400_000:
        return "under-400k"
    if price < 500_000:
        return "400-500k"
    if price < 600_000:
        return "500-600k"
    if price < 800_000:
        return "600-800k"
    if price <= 1_000_000:
        return "800k-1M"
    return "over-1M"


def _series_metrics(series: dict) -> dict | None:
    """{value, year, recent_cagr(2yr), full_cagr} from a {year: value} series."""
    s = {y: v for y, v in series.items() if v is not None}
    if not s:
        return None
    yrs = sorted(s)
    latest, val = yrs[-1], s[yrs[-1]]
    base = yrs[-3] if len(yrs) >= 3 else yrs[0]
    n = int(latest) - int(base)
    recent = ((val / s[base]) ** (1 / n) - 1) * 100 if n and s[base] else 0.0
    full = abs_region.cagr(series)
    return {"value": val, "year": latest, "recent_cagr": recent, "full_cagr": full}


def _asset_view(sm: dict, rent: float, inc: float | None) -> dict:
    """Per-asset price/yield/affordability/nowcast block."""
    price = sm["value"]
    # Never mark a suburb DOWN (2024→now was flat-to-up almost everywhere); a
    # suburb with weak recent momentum still held/rose. Floor the escalation at
    # a regional baseline so slow-momentum suburbs (e.g. Corio) aren't understated.
    cap = max(4.0, min(20.0, sm["recent_cagr"]))
    price_now = round(price * (1 + cap / 100) ** NOWCAST_HORIZON_YEARS)
    gy = rent * 52 / price * 100
    return {
        "price_2024": round(price),
        "price_year": sm["year"],
        "price_now": price_now,
        "nowcast_growth_pct": round(cap, 1),
        "growth_pa": round(sm["full_cagr"], 1) if sm["full_cagr"] is not None else None,
        "gross_yield": round(gy, 2),
        "market_yield": round(gy + MARKET_YIELD_UPLIFT_PP, 2),
        "price_to_income": round(price / inc, 1) if inc else None,
        "band": _band(price_now),
    }


def _score_asset(records: list[dict], asset: str) -> None:
    """Score + rank the suburbs eligible for one asset type, in place."""
    elig = [r for r in records if r.get(asset)]
    # gentrification potential = disadvantage (low SEIFA) × improvement momentum
    # (inflow + this asset's price growth). Rewards disadvantaged-but-RISING
    # suburbs; a disadvantaged suburb that's losing people scores ~0 (a trap).
    mig_p = _pct({r["code"]: r["net_migration_per_1000"] for r in elig})
    grw_p = _pct({r["code"]: r[asset]["growth_pa"] for r in elig if r[asset]["growth_pa"] is not None})
    inc_p = _pct({r["code"]: r["income_vs_state"] for r in elig if r.get("income_vs_state") is not None})
    gentr = {}
    for r in elig:
        dis = r.get("disadvantage")
        if dis is None:
            continue
        # momentum = inflow + price growth + income rising faster than state
        momentum = (mig_p.get(r["code"], 0.5) + grw_p.get(r["code"], 0.5) + inc_p.get(r["code"], 0.5)) / 3
        gentr[r["code"]] = dis * momentum
    sig = {
        "yield": {r["code"]: r[asset]["gross_yield"] for r in elig},
        "population_growth": {r["code"]: r["pop_growth_pa"] for r in elig if r["pop_growth_pa"] is not None},
        "net_migration": {r["code"]: r["net_migration_per_1000"] for r in elig},
        "affordability": {r["code"]: -r[asset]["price_to_income"] for r in elig if r[asset]["price_to_income"] is not None},
        "gentrification": gentr,
        "ripple": {r["code"]: r["ripple_gap"] for r in elig if r.get("ripple_gap") is not None},
        # runway = NOT already run: penalise high recent annual growth (framework
        # says 36mo growth >50% ≈ peak passed). Flat/declining is treated neutral.
        "runway": {r["code"]: -min(max(r[asset]["growth_pa"] or 0, 0), 25) for r in elig if r[asset]["growth_pa"] is not None},
        "supply_pressure": {r["code"]: -r["catchment_influx_pct"] for r in elig if r["catchment_influx_pct"] is not None},
        "economic_health": {r["code"]: -r["unemployment_rate"] for r in elig if r["unemployment_rate"] is not None},
        "economic_resilience": {r["code"]: r["resilience"] for r in elig if r["resilience"] is not None},
        "liquidity": {r["code"]: math.log(r["population"]) for r in elig},
    }
    norm = {c: _pct(v) for c, v in sig.items()}
    for r in elig:
        score, wsum = 0.0, 0.0
        for c, w in WEIGHTS.items():
            v = norm[c].get(r["code"])
            if v is not None:
                score += w * v
                wsum += w
        r[asset]["score"] = round(score / wsum * 100, 1) if wsum else 0.0
        r[asset]["cycle"] = _cycle(r[asset]["growth_pa"], r["pop_growth_pa"], r[asset]["gross_yield"])
    elig.sort(key=lambda r: r[asset]["score"], reverse=True)
    for i, r in enumerate(elig, 1):
        r[asset]["rank"] = i


def _aggregations(reg: dict, pop_sa2: dict, pop_sa4: dict) -> dict:
    """State- and city-level time-series for the trend charts."""
    from collections import defaultdict
    from statistics import median
    STATES = ["NSW", "VIC", "QLD", "SA", "WA", "TAS", "NT", "ACT"]

    price_by = defaultdict(lambda: defaultdict(list))
    inc_by = defaultdict(lambda: defaultdict(list))
    yield_by = defaultdict(list)
    mig_by = defaultdict(float)
    popn_by = defaultdict(float)
    appr_by = defaultdict(float)
    stock_by = defaultdict(float)
    for code, rec in reg.items():
        st = state_from_sa2(code)
        m = rec["metrics"]
        for y, v in m.get("median_house_price", {}).items():
            if v is not None:
                price_by[st][y].append(v)
        for y, v in m.get("employee_income", {}).items():
            if v is not None:
                inc_by[st][y].append(v)
        price = abs_region.latest(m.get("median_house_price", {}))
        rent = abs_region.latest(m.get("median_weekly_rent", {}))
        if price and rent:
            yield_by[st].append(rent * 52 / price * 100)
        mig_by[st] += (abs_region.latest(m.get("net_internal_migration", {})) or 0) + (abs_region.latest(m.get("net_overseas_migration", {})) or 0)
        appr_by[st] += abs_region.latest(m.get("total_dwelling_approvals", {})) or 0
        stock_by[st] += abs_region.latest(m.get("dwelling_stock", {})) or 0
    pop_years = defaultdict(lambda: defaultdict(float))
    for code, d in pop_sa2.items():
        st = state_from_sa2(code)
        popn_by[st] += d.get("population") or 0
        for y, v in d.get("periods", {}).items():
            pop_years[st][y] += v

    all_years = sorted({y for st in price_by.values() for y in st})
    states = []
    for st in STATES:
        py = price_by.get(st, {})
        yrs = [y for y in all_years if y in py]
        if not yrs:
            continue
        iyrs = sorted(inc_by.get(st, {}))
        states.append({
            "state": st,
            "years": yrs,
            "price": [round(median(py[y])) for y in yrs],
            "pop_years": [y for y in yrs if pop_years[st].get(y)],
            "pop": [round(pop_years[st][y]) for y in yrs if pop_years[st].get(y)],
            "income_years": iyrs,
            "income": [round(median(inc_by[st][y])) for y in iyrs],
            "yield": round(median(yield_by[st]), 2) if yield_by.get(st) else None,
            "net_migration": round(mig_by[st]),
            "mig_per_1000": round(mig_by[st] / popn_by[st] * 1000, 1) if popn_by.get(st) else None,
            "population": round(popn_by[st]),
            # supply side: new dwelling approvals as % of existing stock
            "approvals_pct": round(appr_by[st] / stock_by[st] * 100, 2) if stock_by.get(st) else None,
        })

    # cities = SA4, top by population, with growth + population series
    cities = []
    for code, d in pop_sa4.items():
        if (d.get("population") or 0) < 80000:
            continue
        per = d.get("periods", {})
        yrs = sorted(per)
        cities.append({
            "name": d["name"], "state": state_from_sa2(code),
            "population": round(d["population"]),
            "growth_pa": round(d["pop_growth_pct"], 1) if d.get("pop_growth_pct") is not None else None,
            "years": yrs, "pop": [round(per[y]) for y in yrs],
        })
    cities.sort(key=lambda c: (c["growth_pa"] or -9), reverse=True)
    return {"states": states, "cities": cities}


def _projections() -> dict:
    """ABS medium-series (Series B) population projections 2024→2034, by state
    and greater-capital-city. The official government 10-year outlook."""
    from collections import defaultdict
    obs, names = abs_client.fetch_observations(
        "POP_PROJ_REGION", key=".3.TT.2.1.2.2.A",   # persons, total age, medium series
        params={"startPeriod": "2024", "endPeriod": "2034"}, timeout=90,
    )
    byreg = defaultdict(dict)
    for o in obs:
        if o["VALUE"] is not None:
            byreg[o["REGION"]][o["TIME_PERIOD"]] = o["VALUE"]
    STATES = {"1": "NSW", "2": "VIC", "3": "QLD", "4": "SA", "5": "WA", "6": "TAS", "7": "NT", "8": "ACT"}
    CAPS = {"11": "Gtr Sydney", "21": "Gtr Melbourne", "31": "Gtr Brisbane", "41": "Gtr Adelaide",
            "51": "Gtr Perth", "61": "Gtr Hobart", "71": "Gtr Darwin"}

    def series(codes):
        out = []
        for c, nm in codes.items():
            d = byreg.get(c, {})
            if "2024" not in d:
                continue
            yrs = sorted(d)
            end = yrs[-1]
            out.append({"name": nm, "years": yrs, "pop": [round(d[y]) for y in yrs],
                        "start": round(d["2024"]), "end": round(d[end]), "end_year": end,
                        "growth_pct": round((d[end] / d["2024"] - 1) * 100, 1),
                        "added": round(d[end] - d["2024"])})
        return out

    states = series(STATES)
    return {"states": states, "capitals": series(CAPS),
            "national_added": sum(s["added"] for s in states),
            "end_year": states[0]["years"][-1] if states else "2034"}


def build_analysis() -> dict:
    conn = connect()
    run_id = start_run(conn, "analyze")
    try:
        reg = abs_region.pull_region_indicators("SA2")
        pop = abs_client.pull_population("SA2")
        diversity = abs_region.pull_industry_diversity("SA2")
        seifa = abs_region.pull_seifa()
        # Owner-occupier tenure (share + 2016->2021 traction). Enhancement only —
        # a hiccup here must not kill the core daily run, so degrade to {}.
        try:
            tenure = abs_region.pull_tenure("SA2")
        except Exception:
            tenure = {}

        # 5km supply-influx catchment (committed building approvals ÷ dwelling stock)
        centroids = abs_geo.fetch_sa2_centroids()
        supply = {c: {"approvals": abs_region.latest(r["metrics"].get("total_dwelling_approvals", {})),
                      "stock": abs_region.latest(r["metrics"].get("dwelling_stock", {}))}
                  for c, r in reg.items()}
        catchment = abs_geo.catchment_influx(supply, centroids, OVERSUPPLY_RADIUS_KM)

        records, ruled_out = [], []
        for code, rec in reg.items():
            m = rec["metrics"]
            rent = abs_region.latest(m.get("median_weekly_rent", {}))
            if not rent:
                continue
            p = pop.get(code, {})
            population = p.get("population")
            if not population or population < MIN_POPULATION:
                continue
            inc = abs_region.latest(m.get("median_income", {}))
            mig = (abs_region.latest(m.get("net_internal_migration", {})) or 0) + \
                  (abs_region.latest(m.get("net_overseas_migration", {})) or 0)
            appr = abs_region.latest(m.get("house_approvals", {})) or 0
            stock = abs_region.latest(m.get("dwelling_stock", {}))
            unemp = abs_region.latest(m.get("unemployment_rate", {}))
            div = diversity.get(code, {})

            house_sm = _series_metrics(m.get("median_house_price", {}))
            att_sm = _series_metrics(m.get("median_attached_price", {}))
            h_price = house_sm["value"] if house_sm else None
            a_price = att_sm["value"] if att_sm else None

            # house view — skip apartment-dominated SA2s where the house median is
            # a thin/unreliable sample (house median well below the unit median).
            house = None
            if house_sm and not (a_price and h_price < a_price * 0.85):
                house = _asset_view(house_sm, rent, inc)
            townhouse = _asset_view(att_sm, rent, inc) if att_sm else None
            if not house and not townhouse:
                continue

            # SUPPLY RULE: committed dwelling-approval influx within 5km. Building
            # approvals are a known influx (unlike uncertain developable land); an
            # influx > 8% of dwelling stock drowns capital growth -> rule out.
            total_appr = abs_region.latest(m.get("total_dwelling_approvals", {}))
            suburb_influx = (total_appr / stock * 100) if (total_appr is not None and stock) else None
            catchment_influx = catchment.get(code)
            # Rule out if the suburb ITSELF is flooding (own influx) OR the 5km area
            # is (catchment influx). Either alone means a committed oversupply.
            over_self = suburb_influx is not None and suburb_influx > OVERSUPPLY_MAX_INFLUX_PCT
            over_area = catchment_influx is not None and catchment_influx > OVERSUPPLY_MAX_INFLUX_PCT
            if over_self or over_area:
                ruled_out.append({"name": rec["name"], "state": state_from_sa2(code),
                                  "suburb_influx_pct": round(suburb_influx, 1) if suburb_influx is not None else None,
                                  "catchment_influx_pct": round(catchment_influx, 1) if catchment_influx is not None else None,
                                  "trigger": "suburb" if over_self and not over_area else ("area" if over_area and not over_self else "both")})
                continue

            records.append({
                "code": code, "name": rec["name"], "state": state_from_sa2(code),
                "population": round(population),
                "median_weekly_rent": round(rent),
                "median_income": round(inc) if inc else None,
                "pop_growth_pa": round(p.get("pop_growth_pct"), 1) if p.get("pop_growth_pct") is not None else None,
                "net_migration": round(mig),
                "net_migration_per_1000": round(mig / population * 1000, 1),
                "approvals_per_1000_dwellings": round(appr / stock * 1000, 1) if stock else None,
                "dwelling_influx_pct": round(suburb_influx, 1) if suburb_influx is not None else None,
                "catchment_influx_pct": round(catchment_influx, 1) if catchment_influx is not None else None,
                "unemployment_rate": unemp,
                "house_unit_ratio": round(h_price / a_price, 2) if (h_price and a_price) else None,
                "seifa_irsad": round(seifa[code]) if code in seifa else None,
                "pct_rented": abs_region.latest(m.get("pct_rented", {})),
                "owner_occupier_pct": (tenure.get(code) or {}).get("owner_occupier_pct"),
                "owner_occupier_delta": (tenure.get(code) or {}).get("owner_occupier_delta"),
                "owned_mortgage_pct": (tenure.get(code) or {}).get("owned_mortgage_pct"),
                "income_growth_pa": round(abs_region.cagr(m.get("employee_income", {})), 1)
                    if abs_region.cagr(m.get("employee_income", {})) is not None else None,
                "top_industry": div.get("top_industry"),
                "top_industry_share": div.get("top_share"),
                "top3_industries": div.get("top3_industries"),
                "commodity_exposure": div.get("commodity_exposure"),
                "anchor_exposure": div.get("anchor_exposure"),
                "econ_base": div.get("econ_base"),
                "resilience": div.get("resilience"),
                "econ_risk": div.get("risk"),
                "house": house,
                "townhouse": townhouse,
            })

        # Income growth vs STATE average (framework Layer-3 gentrification confirmation):
        # incomes rising faster than the state = gentrification is actually underway.
        state_inc: dict[str, list[float]] = {}
        for r in records:
            if r.get("income_growth_pa") is not None:
                state_inc.setdefault(r["state"], []).append(r["income_growth_pa"])
        state_avg = {s: sum(v) / len(v) for s, v in state_inc.items()}
        for r in records:
            ig = r.get("income_growth_pa")
            r["income_vs_state"] = round(ig - state_avg.get(r["state"], ig), 1) if ig is not None else None

        # Ripple / arbitrage: priced below similar-income neighbours within 10km.
        ripple_info = {}
        for r in records:
            price = (r["house"] or r["townhouse"])["price_now"]
            if price and r.get("median_income"):
                ripple_info[r["code"]] = {"price": price, "income": r["median_income"]}
        ripple = abs_geo.compute_ripple(ripple_info, centroids, radius_km=10.0)
        for r in records:
            rg = ripple.get(r["code"])
            r["ripple_gap"] = rg
            r["ripple_flag"] = "Ripple" if (rg is not None and rg >= 15) else ""

        # Socio-economic percentile across the scored set → disadvantage (0..1,
        # high = disadvantaged), decile (1 = most disadvantaged), gentrifying flag.
        seifa_pct = _pct({r["code"]: r["seifa_irsad"] for r in records if r.get("seifa_irsad") is not None})
        for r in records:
            p = seifa_pct.get(r["code"])
            if p is None:
                r["disadvantage"] = None
                r["seifa_decile"] = None
                r["gentrify_flag"] = ""
                continue
            r["disadvantage"] = round(1 - p, 3)
            r["seifa_decile"] = min(10, int(p * 10) + 1)
            # disadvantaged now (bottom ~half) + people moving in + growing = gentrifying
            rising = (r["net_migration_per_1000"] or 0) > 0 and (r["pop_growth_pa"] or 0) >= 0.5
            # income rising faster than state = confirmation gentrification is real
            r["income_confirmed"] = (r.get("income_vs_state") or 0) > 0.2
            if r["seifa_decile"] <= 5 and rising:
                r["gentrify_flag"] = "Gentrifying"
            elif r["seifa_decile"] <= 4 and (r["net_migration_per_1000"] or 0) < 0:
                r["gentrify_flag"] = "Trap"      # disadvantaged AND losing people
            else:
                r["gentrify_flag"] = ""

        _score_asset(records, "house")
        _score_asset(records, "townhouse")

        # Hotspot watch — the "before the crowd" profile: strong arbitrage or
        # confirmed gentrification, demand inflowing, and NOT already run.
        for r in records:
            a = r.get("house") or r.get("townhouse")
            cyc = a["cycle"] if a else "Unknown"
            leading = (r.get("ripple_gap") or 0) >= 15 or (r.get("gentrify_flag") == "Gentrifying" and r.get("income_confirmed"))
            r["hotspot"] = bool(leading and cyc in ("Early", "Mid") and (r.get("net_migration_per_1000") or 0) > 0)

        pop_sa4 = abs_client.pull_population("SA4")
        trends = _aggregations(reg, pop, pop_sa4)
        projections = _projections()

        # Suburb-level 10yr population projection (ABS only projects to capital-city
        # level, so we scale each suburb's STATE projection by its recent momentum
        # relative to its state — an estimate, labelled as such in the report).
        from collections import defaultdict as _dd
        from statistics import median as _median
        state_proj = {s["name"]: s["growth_pct"] for s in projections["states"]}
        _byst = _dd(list)
        for r in records:
            if r.get("pop_growth_pa") is not None:
                _byst[r["state"]].append(r["pop_growth_pa"])
        st_recent = {st: (_median(v) if v else None) for st, v in _byst.items()}
        for r in records:
            sp, pg, sr = state_proj.get(r["state"]), r.get("pop_growth_pa"), st_recent.get(r["state"])
            if sp is not None and pg is not None and sr and sr > 0:
                r["proj_pop_growth_10yr"] = round(sp * min(2.2, max(0.4, pg / sr)), 1)
            else:
                r["proj_pop_growth_10yr"] = sp

        ruled_out.sort(key=lambda r: max(r.get("suburb_influx_pct") or 0, r.get("catchment_influx_pct") or 0), reverse=True)
        OUTPUT.write_text(json.dumps({
            "generated": now_iso(), "count": len(records), "weights": WEIGHTS,
            "n_house": sum(1 for r in records if r.get("house")),
            "n_townhouse": sum(1 for r in records if r.get("townhouse")),
            "ruled_out_oversupply": ruled_out,
            "trends": trends,
            "projections": projections,
            "suburbs": records,
        }, indent=1))
        finish_run(conn, run_id, len(records), 0, "ok",
                   f"house+townhouse; {len(ruled_out)} ruled out (oversupply)")
        return {"records": records, "ruled_out": ruled_out}
    except Exception as e:
        finish_run(conn, run_id, 0, 0, "error", str(e)[:200])
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    out = build_analysis()
    print(f"Analysed {len(out['records'])} suburbs -> {OUTPUT}")
