"""Suburb ranking engine — capital-growth-potential score.

Philosophy: rank suburbs on fundamentals FIRST (cheap, from free ABS data),
then pull live listings only for the top shortlist.

Each component is normalised to 0..1 by percentile rank (outlier-resistant —
greenfield suburbs can post 40%/yr population growth and would blow out a
min-max scale). Missing components have their weight redistributed across the
components that DO have data, so the engine produces a sensible score today on
ABS-only data and a richer one once Domain packages are enabled.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from .config import settings
from .db import now_iso

# component -> (macro/stat source, higher_is_better)
COMPONENTS = {
    "population_growth": True,
    "net_migration": True,
    "price_momentum": True,
    "rental_yield": True,
    "affordability": True,   # already computed as "more affordable = higher"
    "supply_pressure": True,  # scarcity score: fewer approvals per capita = higher
}


def _percentile_normalise(values: dict[str, float], higher_is_better: bool) -> dict[str, float]:
    """Map raw values to 0..1 by rank. Ties share the average rank."""
    if not values:
        return {}
    ordered = sorted(values.items(), key=lambda kv: kv[1])
    n = len(ordered)
    out: dict[str, float] = {}
    for i, (k, _v) in enumerate(ordered):
        pct = i / (n - 1) if n > 1 else 1.0
        out[k] = pct if higher_is_better else (1.0 - pct)
    return out


def _latest_macro(conn: sqlite3.Connection, metric: str) -> dict[str, float]:
    """Latest value per region for a metric (latest observation, then latest period)."""
    rows = conn.execute(
        """SELECT sa2_code, value FROM macro m
           WHERE metric=? AND observed_at || '|' || period = (
             SELECT MAX(observed_at || '|' || period) FROM macro
             WHERE metric=m.metric AND sa2_code=m.sa2_code)""",
        (metric,),
    ).fetchall()
    return {r["sa2_code"]: r["value"] for r in rows if r["value"] is not None}


def _raw_components(conn: sqlite3.Connection) -> dict[str, dict[str, float]]:
    """Gather raw per-suburb values for each component that has data."""
    raw: dict[str, dict[str, float]] = {}

    pop_growth = _latest_macro(conn, "pop_growth_pct")
    if pop_growth:
        raw["population_growth"] = pop_growth

    migration = _latest_macro(conn, "net_migration")
    if migration:
        raw["net_migration"] = migration

    # supply_pressure: building approvals per 1000 people, inverted (scarcity good)
    approvals = _latest_macro(conn, "building_approvals")
    population = _latest_macro(conn, "population")
    if approvals and population:
        supply = {}
        for code, appr in approvals.items():
            pop = population.get(code)
            if pop and pop > 0:
                supply[code] = appr / (pop / 1000.0)
        if supply:
            # invert: fewer approvals per capita -> higher score
            raw["supply_pressure"] = {k: -v for k, v in supply.items()}

    # Domain-derived components (present once Properties & Locations is enabled)
    stats = conn.execute(
        """SELECT sa2_code, growth_pct, gross_yield_pct, median_price
           FROM suburb_stats s
           WHERE sa2_code IS NOT NULL AND observed_at=(
             SELECT MAX(observed_at) FROM suburb_stats WHERE sa2_code=s.sa2_code)"""
    ).fetchall()
    momentum, yield_, median = {}, {}, {}
    for r in stats:
        if r["growth_pct"] is not None:
            momentum[r["sa2_code"]] = r["growth_pct"]
        if r["gross_yield_pct"] is not None:
            yield_[r["sa2_code"]] = r["gross_yield_pct"]
        if r["median_price"] is not None:
            median[r["sa2_code"]] = r["median_price"]
    if momentum:
        raw["price_momentum"] = momentum
    if yield_:
        raw["rental_yield"] = yield_
    # affordability: lower median price = higher score (more growth runway)
    if median:
        raw["affordability"] = {k: -v for k, v in median.items()}

    return raw


def _region_codes(conn: sqlite3.Connection, level: str) -> set[str]:
    rows = conn.execute(
        "SELECT region_code FROM geography WHERE region_type=?", (level,)
    ).fetchall()
    return {r["region_code"] for r in rows}


def compute_scores(conn: sqlite3.Connection, level: str = "SA4") -> list[dict[str, Any]]:
    """Rank regions of the given ABS level. Default SA4 = cities/large regions."""
    weights = dict(settings.ranking["weights"])
    min_pop = settings.ranking.get("min_population", 0)
    level_codes = _region_codes(conn, level)

    population = {k: v for k, v in _latest_macro(conn, "population").items() if k in level_codes}

    raw = {c: {k: v for k, v in vals.items() if k in level_codes}
           for c, vals in _raw_components(conn).items()}
    raw = {c: vals for c, vals in raw.items() if vals}
    available = [c for c in COMPONENTS if c in raw]
    if not available:
        raise RuntimeError("No component data available. Run 'refresh-macro' first.")

    # Renormalise weights across available components.
    wsum = sum(weights[c] for c in available)
    eff_w = {c: weights[c] / wsum for c in available}

    normalised = {c: _percentile_normalise(raw[c], COMPONENTS[c]) for c in available}

    # candidate suburbs: any with population above threshold
    candidates = [code for code, pop in population.items() if pop >= min_pop]
    results = []
    for code in candidates:
        score = 0.0
        comps: dict[str, float | None] = {c: None for c in COMPONENTS}
        contributing = 0.0
        for c in available:
            v = normalised[c].get(code)
            if v is not None:
                comps[c] = v
                score += eff_w[c] * v
                contributing += eff_w[c]
        if contributing == 0:
            continue
        # scale by fraction of weight that actually contributed, then to 0..100
        final = (score / contributing) * 100
        results.append({
            "sa2_code": code,
            "score": round(final, 2),
            "components": comps,
            "coverage": round(contributing, 2),
        })

    results.sort(key=lambda r: r["score"], reverse=True)
    for i, r in enumerate(results, 1):
        r["rank"] = i
    return results


def save_scores(conn: sqlite3.Connection, results: list[dict[str, Any]]) -> None:
    ts = now_iso()
    for r in results:
        c = r["components"]
        conn.execute(
            """INSERT OR REPLACE INTO suburb_scores(
                 sa2_code, computed_at, score, rank,
                 c_population_growth, c_net_migration, c_price_momentum,
                 c_rental_yield, c_affordability, c_supply_pressure, detail_json)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (r["sa2_code"], ts, r["score"], r["rank"],
             c["population_growth"], c["net_migration"], c["price_momentum"],
             c["rental_yield"], c["affordability"], c["supply_pressure"],
             json.dumps({"coverage": r["coverage"]})),
        )
    conn.commit()
