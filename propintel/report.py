"""Generate the local HTML findings report (pure macro-fundamentals, Domain-free).

Two strategy modes — Houses and Townhouses/Villas — each scored on that asset
type's own price, yield, affordability and momentum, with price-band sub-tabs.

Reads:  data/suburb_analysis.json, data/policies.json, data/catalysts.json
Writes: reports/emerging-growth-report.html
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from .config import ROOT


def _built_stamp() -> str:
    """Timestamp for when this report was generated — proves the daily refresh ran,
    even on days ABS releases nothing new. Australian time where available."""
    try:
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo("Australia/Sydney"))
        return now.strftime("%a %-d %b %Y, %-I:%M%p %Z")
    except Exception:
        try:
            return datetime.now(timezone.utc).strftime("%a %d %b %Y, %H:%M UTC")
        except Exception:
            return datetime.now().strftime("%a %d %b %Y, %H:%M")

OUTPUT = ROOT / "reports" / "emerging-growth-report.html"
ANALYSIS = ROOT / "data" / "suburb_analysis.json"
POLICIES = ROOT / "data" / "policies.json"
CATALYSTS = ROOT / "data" / "catalysts.json"

ASSETS = [("house", "🏠 Houses"), ("townhouse", "🏘 Townhouses & Villas")]
# The shortlist is organised by state (not price band) — the site leads with growth
# fundamentals and shows no dollar figures, so price bands no longer make sense.
STATE_ORDER = ["NSW", "VIC", "QLD", "WA", "SA", "TAS", "ACT", "NT"]
CYCLE_CLASS = {"Early": "b-early", "Mid": "b-mid", "Late": "b-late", "Flat": "b-flat", "Unknown": "b-flat"}
RISK_CLASS = {"Diversified": "b-early", "Elevated": "b-mid", "High": "b-late"}
STATUS_CLASS = {"Active": "b-early", "Under construction": "b-early", "Announced": "b-early",
                "Planned": "b-mid", "Emerging": "b-mid", "Delayed": "b-mid",
                "Uncertain": "b-late", "Cancelled": "b-late"}


def _status_badge(status: str) -> str:
    return f'<span class="badge {STATUS_CLASS.get(status, "b-flat")}">{status}</span>'


def _yield_cell(a: dict) -> str:
    # Rental yield is still one of the scoring inputs, but the site shows no dollar figures,
    # so the actual % is hidden — we only surface whether the suburb clears the ~4.5% gate.
    if a["market_yield"] >= 4.5:
        return '<span class="hit" title="Clears the ~4.5%+ market rental-yield gate">✓</span>'
    return '<span class="sub2" title="Below the ~4.5% market rental-yield gate">—</span>'


def _top3_str(s: dict) -> str:
    t3 = s.get("top3_industries") or []
    return ", ".join(f"{n} {p}%" for n, p in t3)


def _base_short(base: str | None) -> str:
    return {"Knowledge/anchor-led": "Anchor", "Commodity-exposed": "Commodity",
            "Broad/mixed": "Mixed"}.get(base, "—")


def _ripple_cell(s: dict) -> str:
    rg = s.get("ripple_gap")
    if rg is None:
        return '<span class="sub2">—</span>'
    if rg >= 15:
        return f'<span class="hit" title="Priced {rg}% below similar-income neighbours within 10km — ripple/arbitrage upside">+{rg:.0f}%</span>'
    if rg <= -15:
        return f'<span class="warn-flag" title="Priced {abs(rg):.0f}% above similar-income neighbours — limited arbitrage">{rg:.0f}%</span>'
    return f'<span class="sub2" title="vs similar-income neighbours within 10km">{rg:+.0f}%</span>'


def _supply_cell(s: dict) -> str:
    """5km dwelling-approval influx as % of stock (rule-out is >8%; lower = scarcer)."""
    c = s.get("catchment_influx_pct")
    if c is None:
        return '<span class="sub2">—</span>'
    own = s.get("dwelling_influx_pct")
    tip = f"5km catchment influx {c}% · suburb {own}% (rule-out >8%)"
    cls = "hit" if c < 3 else ("warn-flag" if c >= 6 else "")
    return f'<span class="{cls}" title="{tip}">{c:.1f}%</span>'


def _ses_cell(s: dict) -> str:
    d = s.get("seifa_decile")
    if d is None:
        return '<span class="sub2">—</span>'
    flag = s.get("gentrify_flag")
    ivs = s.get("income_vs_state")
    conf = "✓" if s.get("income_confirmed") else ""
    tip = (f"SEIFA IRSAD decile {d}/10 (1=most disadvantaged). "
           f"Income growth vs state: {ivs:+}pp"
           + (" — CONFIRMED gentrification (incomes rising faster than state)" if conf else "")
           if ivs is not None else f"SEIFA decile {d}/10")
    if flag == "Gentrifying":
        return f'<span class="hit" title="Gentrifying: low socio-economic + inflow + growth. {tip}">▲ D{d}{conf}</span>'
    if flag == "Trap":
        return f'<span class="warn-flag" title="Value trap: disadvantaged AND losing people. {tip}">▼ D{d}</span>'
    return f'<span class="sub2" title="{tip}">D{d}{conf}</span>'


def _row(s: dict, asset: str) -> str:
    a = s[asset]
    risk = s.get("econ_risk") or "—"
    def g(v, suf="%"):
        return f'{v}{suf}' if v is not None else "—"
    return f'''<tr>
      <td class="num">{a["rank"]}</td>
      <td><b>{s["name"]}</b>{' <span title="Hotspot watch — before-the-crowd profile">🔥</span>' if s.get("hotspot") else ''}</td>
      <td>{s["state"]}</td>
      <td class="num">{_yield_cell(a)}</td>
      <td class="num">{_ripple_cell(s)}</td>
      <td class="num">{g(s["pop_growth_pa"])}</td>
      <td class="num">{g(s["net_migration_per_1000"],"")}</td>
      <td class="num">{_supply_cell(s)}</td>
      <td class="num">{_ses_cell(s)}</td>
      <td><span class="badge {RISK_CLASS.get(risk,'b-flat')}" title="Economic base: {s.get('econ_base','')} · top industries: {_top3_str(s)} · commodity {s.get('commodity_exposure','')}%">{_base_short(s.get('econ_base'))}</span></td>
      <td><span class="badge {CYCLE_CLASS.get(a['cycle'],'b-flat')}">{a['cycle']}</span></td>
      <td class="num"><b>{a["score"]}</b></td>
    </tr>'''


def _table(rows_html: str) -> str:
    return f'''<div class="tablewrap"><table>
      <thead><tr>
        <th class="num">#</th><th>Suburb (SA2)</th><th>St</th>
        <th class="num" title="✓ = clears the ~4.5%+ market rental-yield gate (the yield % is a scoring input but not shown — this is a fundamentals-only view)">Yield</th>
        <th class="num" title="% below similar-income neighbours within 10km (ripple/arbitrage upside)">Ripple</th><th class="num">Pop g/yr</th><th class="num">Net mig /1k</th>
        <th class="num" title="Dwelling-approval influx within 5km as % of stock (rule-out >8%)">Supply 5km</th>
        <th class="num" title="Socio-economic decile (1=most disadvantaged). ▲ gentrifying · ▼ trap">SES</th>
        <th title="Economic base by industry mix — hover for top industries">Econ base</th><th>Cycle</th><th class="num">Score</th>
      </tr></thead><tbody>{rows_html}</tbody></table></div>'''


def _strategy_block(records: list[dict], asset: str, label: str, active: bool) -> str:
    elig = [r for r in records if r.get(asset)]
    elig.sort(key=lambda r: r[asset]["rank"])
    n_meet = len([r for r in elig if r[asset]["market_yield"] >= 4.5])
    noun = label.split(" ", 1)[1].lower()   # "houses" / "townhouses & villas"

    # Tabs: a national "Top overall" plus one per state present in the data.
    states = [st for st in STATE_ORDER if any(r["state"] == st for r in elig)]
    tabs = [("overview", "★ Top overall")] + [(st, st) for st in states]
    btns = "".join(
        f'<button class="tabbtn{" active" if key=="overview" else ""}" onclick="showBand(\'{asset}\',\'{key}\')" data-band="{asset}-{key}">{lbl}</button>'
        for key, lbl in tabs
    )
    panels = []
    for key, lbl in tabs:
        if key == "overview":
            rows = elig[:15]
            note = (f'Top 15 {noun} nationwide, ranked purely on the growth fundamentals (no price used in the display). '
                    f'{len(elig)} suburbs have a {asset} market; {n_meet} clear the ~4.5%+ rental-yield gate.')
        else:
            in_state = [r for r in elig if r["state"] == key]
            rows = in_state[:15]
            note = f'Top {len(rows)} {noun} in {lbl} by growth-fundamentals score ({len(in_state)} scored).'
        panels.append(
            f'<section class="tabpanel{" active" if key=="overview" else ""}" id="panel-{asset}-{key}">'
            f'<div class="panel-note">{note}</div>{_table("".join(_row(r, asset) for r in rows))}</section>'
        )
    return (f'<div class="strat{" active" if active else ""}" id="strat-{asset}">'
            f'<div class="tabs">{btns}</div>{"".join(panels)}</div>')


def _scenario_section(recs: list[dict]) -> str:
    # top ripple candidates within budget (house or townhouse <= $600k)
    rip = []
    for s in recs:
        for asset in ("house", "townhouse"):
            a = s.get(asset)
            if a and a["price_now"] <= 1_000_000 and (s.get("ripple_gap") or 0) >= 15:
                rip.append((s, asset, a))
                break
    rip.sort(key=lambda x: -(x[0]["ripple_gap"] or 0))
    rows = "".join(
        f'<tr><td><b>{s["name"]}</b></td><td>{s["state"]}</td>'
        f'<td class="num"><span class="hit">+{s["ripple_gap"]:.0f}%</span></td>'
        f'<td>{s.get("econ_base","")}</td>'
        f'<td>{"▲ Gentrifying" if s.get("gentrify_flag")=="Gentrifying" else ""}</td></tr>'
        for s, asset, a in rip[:12]
    )
    return f'''<h2>Scenario &amp; ripple analysis — how growth propagates</h2>
    <p class="sub">Capital growth spreads through a <b>domino / ripple effect</b>: a funded catalyst lands, and its impact cascades outward and down the value chain. This is how to read the shortlist forward, not just as a snapshot.</p>
    <div class="panel">
      <p class="m-detail"><b>The domino chain (how a government catalyst becomes price growth):</b></p>
      <p class="m-detail" style="margin-top:6px">① <b>Funded infrastructure / jobs</b> (e.g. AUKUS at Osborne, Olympics transport in SEQ, Hunter energy transition) → ② <b>employment</b> rises and non-local workers arrive → ③ <b>net migration &amp; population</b> climb (demand) → ④ if <b>supply is constrained</b> (approvals &lt;8% / 5km) demand outstrips it → ⑤ <b>prices &amp; rents</b> rise in the core suburbs → ⑥ buyers priced out spill into <b>cheaper adjacent suburbs of similar income</b> → the <b>ripple</b> lifts those next.</p>
      <p class="m-detail" style="margin-top:10px"><b>Why the ripple column matters:</b> a suburb priced well below its similar-income neighbours (within 10km) is the arbitrage — as the dearer neighbours become unaffordable, demand ripples to it and the gap closes. Combined with a <b>diversifying, anchor-led economy</b> and <b>low socio-economic base that's rising</b>, that's the highest-conviction forward setup.</p>
      <p class="m-detail" style="margin-top:10px"><b>Top ripple / arbitrage candidates</b> (priced below similar-income neighbours within 10km):</p>
      <div class="tablewrap" style="margin-top:8px"><table style="min-width:520px">
        <thead><tr><th>Suburb</th><th>St</th><th class="num">Below peers</th><th>Economic base</th><th>Trajectory</th></tr></thead>
        <tbody>{rows}</tbody></table></div>
      <p class="m-detail" style="margin-top:10px"><b>Economic diversification as a driver (beyond migration):</b> an area's <i>potential</i> depends on <i>what</i> drives its jobs. <b>Anchor-led</b> economies (health, education, defence/public admin, professional, finance) provide stable, growing demand that compounds; <b>commodity-exposed</b> economies (mining/agriculture) carry boom-bust risk. The <b>Econ base</b> column shows each suburb's mix — hover for the top-3 industries. A diversifying, anchor-led base is a long-term (6–15yr) growth factor that migration alone doesn't capture.</p>
    </div>'''


CHART_COLORS = ["#4f8bff", "#43c491", "#e0a458", "#f0776c", "#a78bfa", "#22d3ee", "#f472b6", "#94a3b8"]


def _line_chart(title, series, x_labels, subtitle=""):
    """Multi-series line chart. series = [{name, values:[float], color}]."""
    W, H, pl, pr, pt, pb = 700, 300, 46, 12, 16, 34
    vals = [v for s in series for v in s["values"] if v is not None]
    if not vals:
        return ""
    vmin, vmax = min(vals), max(vals)
    if vmin == vmax:
        vmax += 1
    n = len(x_labels)
    def X(i): return pl + (i / (n - 1) if n > 1 else 0) * (W - pl - pr)
    def Y(v): return H - pb - (v - vmin) / (vmax - vmin) * (H - pt - pb)
    grid = "".join(f'<line x1="{pl}" y1="{Y(vmin+(vmax-vmin)*f)}" x2="{W-pr}" y2="{Y(vmin+(vmax-vmin)*f)}" stroke="var(--line)" stroke-width="1"/>' for f in (0, .25, .5, .75, 1))
    ylabs = "".join(f'<text x="{pl-6}" y="{Y(vmin+(vmax-vmin)*f)+3}" text-anchor="end" font-size="9" fill="var(--muted)">{round(vmin+(vmax-vmin)*f)}</text>' for f in (0, .5, 1))
    xlabs = "".join(f'<text x="{X(i)}" y="{H-pb+14}" text-anchor="middle" font-size="9" fill="var(--muted)">{x}</text>' for i, x in enumerate(x_labels))
    lines = ""
    for s in series:
        pts = " ".join(f"{X(i):.0f},{Y(v):.0f}" for i, v in enumerate(s["values"]) if v is not None)
        lines += f'<polyline points="{pts}" fill="none" stroke="{s["color"]}" stroke-width="2"/>'
    legend = " ".join(f'<span class="lg"><i style="background:{s["color"]}"></i>{s["name"]}</span>' for s in series)
    return (f'<div class="chart"><div class="chart-t">{title}</div>'
            + (f'<div class="chart-s">{subtitle}</div>' if subtitle else "")
            + f'<svg viewBox="0 0 {W} {H}" preserveAspectRatio="xMidYMid meet">{grid}{ylabs}{xlabs}{lines}</svg>'
            + f'<div class="legend">{legend}</div></div>')


def _hbar(title, pairs, unit="", subtitle=""):
    """Horizontal bar chart. pairs = [(label, value)]."""
    pairs = [(l, v) for l, v in pairs if v is not None]
    if not pairs:
        return ""
    vmax = max(v for _, v in pairs) or 1
    vmin = min(0, min(v for _, v in pairs))
    span = (vmax - vmin) or 1
    rowh = 26
    W = 700
    bx = 150  # label column
    barw = W - bx - 60
    zero = bx + (0 - vmin) / span * barw
    rows = ""
    for i, (l, v) in enumerate(pairs):
        y = i * rowh + 6
        x = bx + (min(v, 0) - vmin) / span * barw
        w = abs(v) / span * barw
        col = CHART_COLORS[i % len(CHART_COLORS)]
        rows += (f'<text x="{bx-8}" y="{y+14}" text-anchor="end" font-size="11" fill="var(--ink)">{l}</text>'
                 f'<rect x="{x:.0f}" y="{y}" width="{max(w,1):.0f}" height="16" rx="3" fill="{col}"/>'
                 f'<text x="{x+w+5 if v>=0 else x-5:.0f}" y="{y+13}" text-anchor="{"start" if v>=0 else "end"}" font-size="10" fill="var(--muted)">{v}{unit}</text>')
    H = len(pairs) * rowh + 10
    return (f'<div class="chart"><div class="chart-t">{title}</div>'
            + (f'<div class="chart-s">{subtitle}</div>' if subtitle else "")
            + f'<svg viewBox="0 0 {W} {H}" preserveAspectRatio="xMidYMid meet">'
            f'<line x1="{zero:.0f}" y1="0" x2="{zero:.0f}" y2="{H-10}" stroke="var(--line)"/>{rows}</svg></div>')


def _trends_section(trends: dict) -> str:
    states = trends["states"]
    cities = trends["cities"]
    # price index (base 100 at first year) by state
    price_series = []
    xyears = states[0]["years"] if states else []
    for i, s in enumerate(states):
        base = s["price"][0]
        price_series.append({"name": s["state"], "color": CHART_COLORS[i % len(CHART_COLORS)],
                             "values": [round(p / base * 100) for p in s["price"]]})
    price_growth = [(s["state"], round((s["price"][-1] / s["price"][0] - 1) * 100)) for s in states]
    price_growth.sort(key=lambda x: -x[1])
    mig = sorted([(s["state"], s["mig_per_1000"]) for s in states], key=lambda x: -(x[1] or 0))
    yld = sorted([(s["state"], s["yield"]) for s in states], key=lambda x: -(x[1] or 0))
    citypop = [(c["name"], c["growth_pa"]) for c in cities[:15]]

    return f'''<h2>Trends by state &amp; city</h2>
    <p class="sub">Median house price, migration and yield aggregated from every SA2 (state) and SA4 (city). Charts are theme-aware and built from ABS time-series — no external libraries.</p>
    <div class="chart-grid">
      {_line_chart("Median house price growth by state (indexed, 2019 = 100)", price_series, xyears, "Established-house median of SA2 medians. Steeper = faster capital growth.")}
      {_hbar("Total house-price growth 2019→latest, by state", price_growth, "%", "Where the last cycle delivered most — and where more runway may remain.")}
      {_hbar("Net migration per 1,000 people, by state", mig, "", "Demand pressure — WA & QLD lead the interstate + overseas inflow.")}
      {_hbar("Median gross yield by state (Census-based)", yld, "%", "Indicative relative yield; market asking yields run ~1–1.5pp higher.")}
      {_hbar("Fastest-growing cities/regions (SA4) — population growth/yr", citypop, "%", "The city-level demand engines under the suburb shortlists.")}
    </div>'''


def _sersi_update() -> str:
    p = ROOT / "data" / "latest_update.md"
    if not p.exists():
        return ('<div class="sersi"><div class="sersi-h">🛰 Sersi — Daily Update</div>'
                '<p class="m-detail">Runs each morning; the day\'s changes will appear here.</p></div>')
    md = p.read_text()
    title = "🛰 Sersi — Daily Update"
    body_lines, ul_open = [], False
    for line in md.splitlines():
        line = line.strip()
        if line.startswith("# "):
            title = line[2:]
            continue
        html = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", line)
        html = re.sub(r"\*(.+?)\*", r"<i>\1</i>", html)
        html = re.sub(r"_(.+?)_", r"<i>\1</i>", html)
        if line.startswith("- "):
            if not ul_open:
                body_lines.append("<ul class='tight'>"); ul_open = True
            body_lines.append(f"<li>{html[2:]}</li>")
        else:
            if ul_open:
                body_lines.append("</ul>"); ul_open = False
            if line:
                body_lines.append(f'<p class="m-detail">{html}</p>')
    if ul_open:
        body_lines.append("</ul>")
    return (f'<div class="sersi"><div class="sersi-h">{title}</div>{"".join(body_lines)}</div>')


def _summary_section(recs: list[dict], trends: dict, proj: dict) -> str:
    def top(asset, n=5):
        el = [r for r in recs if r.get(asset) and r[asset]["price_now"] <= 1_000_000]
        el.sort(key=lambda r: r[asset]["rank"])
        return el[:n]
    th, tt = top("house"), top("townhouse")
    n_hot = sum(1 for r in recs if r.get("hotspot"))
    n_gent = sum(1 for r in recs if r.get("gentrify_flag") == "Gentrifying")
    nat, end = proj.get("national_added", 0), proj.get("end_year", "2034")
    pstates = proj.get("states", [])
    fastest = max(pstates, key=lambda s: s["growth_pct"]) if pstates else None
    st = trends.get("states", [])
    tight = sorted(st, key=lambda s: -((s.get("mig_per_1000") or 0) - (s.get("approvals_pct") or 0) * 5))
    tight_names = ", ".join(f'{s["state"]}' for s in tight[:2]) if tight else "—"

    def pick_rows(rows, asset):
        out = ""
        for r in rows:
            a = r[asset]
            tags = []
            if r.get("hotspot"): tags.append('🔥')
            if r.get("gentrify_flag") == "Gentrifying": tags.append('▲gentrifying' + ('✓' if r.get("income_confirmed") else ''))
            if (r.get("ripple_gap") or 0) >= 15: tags.append(f'ripple+{r["ripple_gap"]:.0f}%')
            out += (f'<tr><td><b>{r["name"]}</b> {" ".join(tags)}</td><td>{r["state"]}</td>'
                    f'<td class="num">{_yield_cell(a)}</td>'
                    f'<td class="num">{r.get("proj_pop_growth_10yr","—")}%</td>'
                    f'<td><span class="badge {CYCLE_CLASS.get(a["cycle"],"b-flat")}">{a["cycle"]}</span></td>'
                    f'<td class="num"><b>{a["score"]}</b></td></tr>')
        return out

    def jump(pg, label):
        return f'<button class="jump" onclick="showPage(\'{pg}\')">{label} →</button>'

    return f'''<h2>Today's shortlist</h2>
    {_sersi_update()}

    <div class="stat-row">
      <div class="stat"><div class="v">{len(recs):,}</div><div class="l">suburbs scored · {jump("shortlist","Shortlist")}</div></div>
      <div class="stat"><div class="v">🔥 {n_hot}</div><div class="l">hotspot-watch (before the crowd)</div></div>
      <div class="stat"><div class="v">▲ {n_gent}</div><div class="l">gentrifying (low SES + rising)</div></div>
      <div class="stat"><div class="v">+{nat/1e6:.2f}M</div><div class="l">people by {end} · {jump("outlook","Outlook")}</div></div>
    </div>

    <h3 class="ph">Top houses {jump("shortlist","full list")}</h3>
    <div class="tablewrap"><table style="min-width:560px"><thead><tr><th>Suburb</th><th>St</th><th class="num" title="✓ = clears the ~4.5%+ market rental-yield gate">Yield</th><th class="num">Proj 10yr</th><th>Cycle</th><th class="num">Score</th></tr></thead><tbody>{pick_rows(th,"house")}</tbody></table></div>

    <h3 class="ph">Top townhouses / villas {jump("shortlist","full list")}</h3>
    <div class="tablewrap"><table style="min-width:560px"><thead><tr><th>Suburb</th><th>St</th><th class="num" title="✓ = clears the ~4.5%+ market rental-yield gate">Yield</th><th class="num">Proj 10yr</th><th>Cycle</th><th class="num">Score</th></tr></thead><tbody>{pick_rows(tt,"townhouse")}</tbody></table></div>

    <div class="panel" style="margin-top:16px">
      <p class="m-detail"><b>How Sersi picks these:</b> every Australian suburb (SA2) is scored on the fundamentals that drive capital growth <i>before</i> price moves — yield, gentrification (low socio-economic base rising, confirmed by incomes outpacing the state), ripple/arbitrage vs richer neighbours, migration, affordability, industry diversity and supply scarcity — then oversupplied greenfield estates are ruled out (&gt;8% approvals within 5km). 100% free public data (ABS + valuers-general); no listings, Domain-free.</p>
    </div>

    <div class="grid2" style="margin-top:16px">
      <div class="panel">
        <p class="m-name">What the macro says {jump("outlook","Outlook")} {jump("trends","Trends")}</p>
        <ul class="tight" style="margin-top:8px">
          <li><b>+{nat/1e6:.2f}M more people by {end}</b> (~{round(nat/2.5/1e6,2)}M dwellings needed) — {("<b>"+fastest["name"]+" +"+str(fastest["growth_pct"])+"%</b> leads") if fastest else ""}.</li>
          <li><b>Tightest supply-demand: {tight_names}</b> — strong migration against limited new building → upward pressure on <b>prices and rents</b>.</li>
          <li>Australia runs on a <b>~18-year land cycle</b>; favour early-cycle, supply-constrained suburbs. {jump("context","Context")}</li>
        </ul>
      </div>
      <div class="panel">
        <p class="m-name">Where the growth is being built {jump("news","News")} {jump("catalysts","Catalysts")}</p>
        <ul class="tight" style="margin-top:8px">
          <li><b>Funded catalysts</b> tie jobs to specific suburbs: AUKUS (Adelaide north), Olympics (SEQ — Ipswich/Logan), energy transition (Townsville/Gladstone/Hunter).</li>
          <li><b>Policy tailwinds</b>: uncapped Help-to-Buy &amp; First-Home Guarantee concentrate demand at sub-$600k. {jump("policy","Policy")}</li>
          <li><b>Economy</b>: diverse, anchor-led (health/education/defence) economies pull more migration than commodity towns. {jump("economy","Economy")}</li>
        </ul>
      </div>
    </div>
    <p class="sub" style="margin-top:14px">🔎 Use the search box at the very top to look up <b>any</b> suburb (e.g. Geelong) and see it across every metric, including its projected 10-year growth.</p>'''


def _outlook_section(proj: dict, trends: dict) -> str:
    states = proj.get("states", [])
    caps = proj.get("capitals", [])
    end = proj.get("end_year", "2034")
    nat = proj.get("national_added", 0)
    dwellings = round(nat / 2.5)   # ~2.5 persons per household
    # projected trajectory (indexed to 100 at 2024) by state
    traj = []
    xyears = states[0]["years"] if states else []
    for i, s in enumerate(states):
        base = s["pop"][0]
        traj.append({"name": s["name"], "color": CHART_COLORS[i % len(CHART_COLORS)],
                     "values": [round(p / base * 100, 1) for p in s["pop"]]})
    state_growth = sorted([(s["name"], s["growth_pct"]) for s in states], key=lambda x: -x[1])
    cap_growth = sorted([(s["name"], s["growth_pct"]) for s in caps], key=lambda x: -x[1])

    # supply-demand table by state
    tmap = {t["state"]: t for t in trends.get("states", [])}
    sd_rows = ""
    for s in sorted(states, key=lambda x: -x["growth_pct"]):
        t = tmap.get(s["name"], {})
        mig = t.get("mig_per_1000")
        sup = t.get("approvals_pct")
        if mig is not None and sup is not None:
            tight = mig >= 10 and sup <= 1.85
            loose = sup >= 2.0
            read = ('<span class="hit">Tight — price &amp; rent pressure</span>' if tight
                    else ('<span class="warn-flag">More supply coming</span>' if loose else "Balanced"))
        else:
            read = "—"
        sd_rows += (f'<tr><td><b>{s["name"]}</b></td>'
                    f'<td class="num">+{s["growth_pct"]}%</td>'
                    f'<td class="num">+{s["added"]:,}</td>'
                    f'<td class="num">{mig if mig is not None else "—"}</td>'
                    f'<td class="num">{sup if sup is not None else "—"}%</td>'
                    f'<td>{read}</td></tr>')

    # jobs pipeline from catalysts
    cat = json.loads(CATALYSTS.read_text())
    jobs_sorted = sorted(cat["markets"], key=lambda m: 0 if m.get("status") in ("Active", "Under construction", "Announced") else 1)
    job_rows = "".join(
        f'<tr><td><b>{m["market"]}</b></td><td>{m["state"]}</td>'
        f'<td>{m["theme"]}</td><td>{_status_badge(m.get("status",""))}</td>'
        f'<td>{m["jobs"]}</td><td>{m.get("funding","")}</td><td>{m.get("timeframe","")}</td></tr>'
        for m in jobs_sorted
    )
    reviewed = cat.get("last_reviewed", "")

    return f'''<h2>10-year outlook — population projections, supply-demand &amp; jobs</h2>
    <p class="sub">The forward view: where government projects population and jobs to grow to <b>{end}</b>, and where demand is set to outrun supply — the two forces behind capital <i>and</i> rental growth.</p>
    <div class="stat-row">
      <div class="stat"><div class="v">+{nat/1e6:.2f}M</div><div class="l">projected extra people nationally by {end} (ABS medium series)</div></div>
      <div class="stat"><div class="v">~{dwellings/1e6:.2f}M</div><div class="l">new <b>dwellings</b> implied (@2.5/household) — the supply task</div></div>
      <div class="stat"><div class="v">{state_growth[0][0] if state_growth else ""} {state_growth[0][1] if state_growth else ""}%</div><div class="l">fastest-growing state to {end}</div></div>
    </div>
    <div class="chart-grid">
      {_line_chart(f"Projected population trajectory to {end} (indexed, 2024 = 100)", traj, xyears, "ABS Series B (medium) projections by state.")}
      {_hbar(f"Projected population growth 2024→{end}, by state", state_growth, "%", "Official ABS projections — forward demand.")}
      {_hbar(f"Projected growth 2024→{end}, by capital city", cap_growth, "%", "Greater-capital-city projections.")}
    </div>
    <h3 class="ph">Supply vs demand by state — the capital &amp; rental growth engine</h3>
    <p class="sub">Demand (net migration + projected growth) pushing against supply (new dwelling approvals as % of stock). Where demand outruns the building response, prices and rents face upward pressure.</p>
    <div class="tablewrap"><table style="min-width:720px">
      <thead><tr><th>State</th><th class="num">Proj. growth →{end}</th><th class="num">People added</th>
        <th class="num" title="net migration per 1,000 — demand">Demand /1k</th>
        <th class="num" title="dwelling approvals as % of stock — supply">Supply %/yr</th><th>Balance</th></tr></thead>
      <tbody>{sd_rows}</tbody></table></div>
    <h3 class="ph">Jobs &amp; funding pipeline — committed government + industry projects</h3>
    <p class="sub">The employment the government/industry expects from committed projects — the demand that pulls people (and rents) into these regions over the next 5–10 years. <b>Status</b> matters: <span class="badge b-early">Active</span> is funded &amp; underway · <span class="badge b-mid">Planned/Delayed</span> · <span class="badge b-late">Uncertain/Cancelled</span> (e.g. Whyalla H2). Curated from government/industry sources, last reviewed <b>{reviewed}</b>.</p>
    <div class="tablewrap"><table style="min-width:900px">
      <thead><tr><th>Region</th><th>St</th><th>Sector</th><th>Status</th><th>Jobs</th><th>Funding</th><th>Timeframe</th></tr></thead>
      <tbody>{job_rows}</tbody></table></div>'''


def _economy_section(recs: list[dict]) -> str:
    # Evidence for the thesis: diverse economies attract more migration.
    def avg_mig(base):
        vals = [r["net_migration_per_1000"] for r in recs
                if r.get("econ_base") == base and r.get("net_migration_per_1000") is not None]
        return sum(vals) / len(vals) if vals else 0
    a_anchor, a_mixed, a_comm = avg_mig("Knowledge/anchor-led"), avg_mig("Broad/mixed"), avg_mig("Commodity-exposed")

    # In-budget suburbs (house or townhouse <=$600k), most diversified first, with mix
    inb = []
    for r in recs:
        a = r.get("house") or r.get("townhouse")
        if a and a["price_now"] <= 600000 and r.get("top3_industries"):
            inb.append((r, a))
    inb.sort(key=lambda x: (x[0].get("effective_industries") or 0), reverse=True)

    def mix_bars(r):
        # show top-3 industry shares as tiny inline bars
        out = []
        for name, pct in (r.get("top3_industries") or []):
            out.append(f'<div class="ind"><span>{name}</span><span class="indpct">{pct}%</span></div>')
        return "".join(out)

    rows = "".join(
        f'<tr><td><b>{r["name"]}</b></td><td>{r["state"]}</td>'
        f'<td>{_base_badge(r.get("econ_base"))}</td>'
        f'<td class="num">{r.get("effective_industries","")}</td>'
        f'<td style="min-width:230px">{mix_bars(r)}</td>'
        f'<td class="num">{r.get("net_migration_per_1000","")}</td>'
        f'<td class="num">{g_pct(r.get("pop_growth_pa"))}</td></tr>'
        for r, a in inb[:20]
    )
    return f'''<h2>Economy &amp; industry — the migration engine</h2>
    <p class="sub">Industry diversity is a <b>leading driver of migration</b>, and migration is what stacks the demand fundamentals for future growth.</p>
    <div class="panel">
      <p class="m-detail">An economy spread across many industries — health, education, professional services, construction, retail, public administration — <b>can't be hollowed out by a single-sector downturn</b>. So employment stays resilient, people keep moving in, and that sustained migration compounds into demand for housing. A suburb reliant on <b>one volatile sector (mining or agriculture)</b> is the opposite: one commodity cycle can reverse jobs, migration and prices together.</p>
      <div class="stat-row" style="margin-top:14px">
        <div class="stat"><div class="v">{a_anchor:+.1f}</div><div class="l">avg net migration /1k — <b>Knowledge/anchor-led</b></div></div>
        <div class="stat"><div class="v">{a_mixed:+.1f}</div><div class="l">avg net migration /1k — <b>Broad/mixed</b></div></div>
        <div class="stat"><div class="v">{a_comm:+.1f}</div><div class="l">avg net migration /1k — <b>Commodity-exposed</b></div></div>
      </div>
      <p class="m-detail" style="margin-top:6px"><i>Diversified economies pull in materially more migration than commodity-dependent ones — the fundamentals stack up before price moves.</i></p>
    </div>
    <p class="sub" style="margin-top:18px">Most-diversified suburbs (effective number of industries = how many sectors the economy effectively spreads across):</p>
    <div class="tablewrap"><table style="min-width:820px">
      <thead><tr><th>Suburb</th><th>St</th><th>Economic base</th>
        <th class="num" title="Inverse-Simpson: effective number of industries">Eff. industries</th>
        <th>Top industries</th><th class="num">Net mig /1k</th><th class="num">Pop g/yr</th></tr></thead>
      <tbody>{rows}</tbody></table></div>'''


def _live_news_section() -> str:
    p = ROOT / "data" / "news_feed.json"
    if not p.exists():
        return ""
    data = json.loads(p.read_text())
    items = data.get("items", [])[:30]
    generated = data.get("generated", "")
    rows = ""
    for i in items:
        tags = "".join(f'<span class="chip">{t}</span>' for t in i.get("tags", []))
        fresh = ' <span class="hit" style="font-size:10px">NEW</span>' if i.get("first_seen") == generated else ""
        rows += (f'<div class="newsitem"><a href="{i["link"]}" target="_blank">{i["title"]}</a>{fresh}'
                 f'<div class="newsmeta"><span>{i["source"]}</span> {tags}</div></div>')
    sources = "PM &amp; Cabinet · Treasury · Infrastructure Magazine · RenewEconomy · Sourceable · realestate.com.au"
    return (f'<h2>Latest headlines — auto-pulled</h2>'
            f'<p class="sub">Property, infrastructure, funding &amp; jobs news pulled from public RSS feeds each morning ({sources}), '
            f'filtered for relevance and tagged by state. Last pulled <b>{generated}</b>. '
            f'<span class="chip">NEW</span> = added today.</p>'
            f'<div class="newslist">{rows}</div>')


def _news_growth_section(recs: list[dict]) -> str:
    cat = json.loads(CATALYSTS.read_text())
    # index suburbs by state for matching
    by_state: dict[str, list[dict]] = {}
    for r in recs:
        by_state.setdefault(r["state"], []).append(r)

    def match_suburbs(market_name: str, state: str) -> list[dict]:
        # pull the alphabetic name fragments (drop bracketed/extra words)
        frags = [w for w in re.split(r"[ /,()–-]+", market_name)
                 if len(w) > 3 and w[0].isupper()]
        hits = []
        for r in by_state.get(state, []):
            if any(f in r["name"] for f in frags):
                hits.append(r)
        # rank the matched suburbs by the better of the two asset scores
        hits.sort(key=lambda r: max((r.get("house") or {}).get("score", 0),
                                    (r.get("townhouse") or {}).get("score", 0)), reverse=True)
        return hits[:4]

    cards = []
    for m in cat["markets"]:
        subs = match_suburbs(m["market"], m["state"])
        if not subs:
            continue
        sub_rows = "".join(
            f'<tr><td>{r["name"]}</td>'
            f'<td class="num">{g_pct(r.get("pop_growth_pa"))}</td>'
            f'<td class="num">{r.get("net_migration_per_1000","")}</td>'
            f'<td class="num">{("+"+str(r["ripple_gap"])+"%") if (r.get("ripple_gap") or 0) >= 15 else "—"}</td>'
            f'<td>{"▲ Gentrifying" if r.get("gentrify_flag")=="Gentrifying" else ""}</td>'
            f'<td class="num">{max((r.get("house") or {}).get("score",0),(r.get("townhouse") or {}).get("score",0))}</td></tr>'
            for r in subs
        )
        cards.append(f'''<div class="pcard">
          <div class="pcard-h"><span class="ploc">{m["market"]} · {m["state"]}</span>{_status_badge(m.get("status",""))}</div>
          <p class="m-detail"><b>News / catalyst:</b> {m["catalyst"]}</p>
          <p class="m-detail" style="color:var(--muted);margin-top:4px"><b>Jobs:</b> {m["jobs"]} &nbsp;·&nbsp; <b>Funding:</b> {m.get("funding","")} &nbsp;·&nbsp; <b>Timeframe:</b> {m.get("timeframe","")}</p>
          <p class="m-detail invest" style="margin-top:6px"><b>Impact:</b> {m.get("impact","")}</p>
          <div class="tablewrap" style="margin-top:8px"><table style="min-width:520px">
            <thead><tr><th>Suburb</th><th class="num">Pop g/yr</th><th class="num">Mig /1k</th><th class="num">Ripple</th><th>Trajectory</th><th class="num">Score</th></tr></thead>
            <tbody>{sub_rows}</tbody></table></div>
        </div>''')
    return f'''<h2>News &amp; growth — catalysts linked to suburbs</h2>
    <p class="sub">Each funded catalyst in the news, tied to the specific suburbs it touches and their live growth fundamentals — so you can see the story <i>and</i> the numbers together.</p>
    <div class="pgrid">{"".join(cards)}</div>'''


def _base_badge(base: str | None) -> str:
    cls = {"Knowledge/anchor-led": "b-early", "Broad/mixed": "b-flat", "Commodity-exposed": "b-late"}.get(base, "b-flat")
    return f'<span class="badge {cls}">{_base_short(base)}</span>'


def g_pct(v):
    return f'{v}%' if v is not None else "—"


def _context_section() -> str:
    return '''<h2>Market context &amp; where to sharpen your edge</h2>
    <p class="sub">Macro framing that sits above the suburb data — and the external sources to cross-check a shortlisted suburb before you buy.</p>
    <div class="grid2">
      <div class="panel">
        <p class="m-name">The ~18-year land cycle</p>
        <p class="m-detail">Australian property has historically moved in a long <b>~18-year land cycle</b> (Catherine Cashmore / Prosper Australia) — a mid-cycle slowdown followed by a stronger second-half boom, then a peak. Knowing roughly where the cycle sits shapes how aggressively to weight <i>early-cycle</i> suburbs vs banking gains. Prosper's <b>Speculative Vacancies</b> work also flags how much stock sits deliberately empty — real supply that headline vacancy rates miss.</p>
        <div class="src"><a href="https://www.prosper.org.au/">prosper.org.au</a></div>
      </div>
      <div class="panel">
        <p class="m-name">Affordability &amp; serviceability</p>
        <p class="m-detail">Prices sit near record highs relative to income (Sydney ~12× income vs ~4.5× in 1970), yet mortgage <i>serviceability</i> isn't at its worst thanks to rates well below the 1990 peak — so affordability has room to compress further before it caps growth, but rate moves are the key swing factor. Long-run, population growth alone doesn't explain price growth — scarcity, credit and land value do.</p>
        <div class="src"><a href="https://datamentary.net/australian-house-prices-over-the-last-50-years-a-retrospective/">50-year retrospective</a></div>
      </div>
    </div>
    <div class="panel" style="margin-top:12px">
      <p class="m-name">Cross-check a suburb before buying (free/low-cost tools)</p>
      <p class="m-detail">This model gives the shortlist; confirm a specific suburb's street-level detail and latest movement on:</p>
      <ul class="tight" style="margin-top:8px">
        <li><a href="https://www.cotality.com/au/our-data/mapping-market">Cotality (CoreLogic) Mapping the Market</a> — median &amp; growth heatmaps</li>
        <li><a href="https://profile.id.com.au/">profile.id (.id)</a> — community demographics, migration &amp; employment by LGA</li>
        <li><a href="https://experience.arcgis.com/experience/32dcbb18c1d24f4aa89caf680413c741/">ArcGIS market dashboard</a> — spatial market view</li>
        <li><a href="https://www.dropbee.au/vic">Dropbee (VIC)</a> — Victorian suburb data</li>
        <li><a href="https://www.livewiremarkets.com/feeds/latest">Livewire Markets</a> — macro / rates commentary</li>
      </ul>
      <p class="m-detail" style="margin-top:8px;color:var(--muted)">These are interactive dashboards, so they're linked for manual cross-check rather than auto-ingested. The News &amp; Growth tab already ties funded catalysts to the specific suburbs they touch.</p>
    </div>'''


def _framework_note() -> str:
    return '''<h2>Alignment to the 5-layer selection framework</h2>
    <p class="sub">Factors mapped to the five-layer location-selection method. ✅ modelled from public data · ◻︎ deferred (needs paid SQM/DSR feeds or point-in-time Census comparison).</p>
    <div class="grid2">
      <div class="panel"><h3 class="ph">Modelled ✅</h3><ul class="tight">
        <li><b>Gross yield</b> &gt;4% (Layer 3)</li>
        <li><b>Building approvals &lt;8%</b> — suburb + 5km catchment (Layer 3 &amp; 2)</li>
        <li><b>Not already run</b> — penalise &gt;50%/3yr median growth (Layer 3)</li>
        <li><b>Diverse / diversifying industries</b> — economic base (Layer 2)</li>
        <li><b>Housing affordability</b> — price-to-income (Layer 2 &amp; 3)</li>
        <li><b>Ripple / arbitrage</b> vs similar-income neighbours (Layer 3)</li>
        <li><b>Gentrification</b> — low socio-economic base rising (Layer 3)</li>
        <li><b>Income rising faster than state</b> — gentrification confirmation, ATO 2019–23 (Layer 3)</li>
        <li><b>Hotspot watch</b> — before-the-crowd leading-indicator profile</li>
        <li><b>Renter proportion</b>, net migration, population, jobs/infrastructure catalysts</li>
      </ul></div>
      <div class="panel"><h3 class="ph">Deferred ◻︎ (need SQM/DSR feeds)</h3><ul class="tight">
        <li>Vacancy rate &lt;2%, stock on market, days on market</li>
        <li>Auction clearance, vendor discounting, online search interest</li>
        <li>12-month rental growth &gt;5% (Census rent is a single point)</li>
        <li>Demand-Supply Ratio (DSR) — excluded by request</li>
        <li>Professional-occupation growth rate vs state (occupation not in 2021 release)</li>
        <li>Level of amenity, exact developable-land zoning (council plans)</li>
      </ul></div>
    </div>'''


def _policy_section() -> str:
    pol = json.loads(POLICIES.read_text())
    def card(p, gov):
        badge = {"Demand ↑": "b-early", "Supply ↑": "b-late", "Supply ↑ (social)": "b-flat",
                 "Uplift near transport": "b-mid", "Uplift near centres": "b-mid",
                 "Demand ↑ (new builds)": "b-early", "Reform-friendly": "b-mid"}.get(p.get("impact"), "b-flat")
        return f'''<div class="pcard">
          <div class="pcard-h"><span class="ploc">{p.get("state", gov)}</span><span class="badge {badge}">{p.get("impact","")}</span></div>
          <p class="m-name">{p["name"]}</p><p class="pstatus">{p["status"]}</p>
          <p class="m-detail">{p["detail"]}</p>
          <p class="m-detail invest"><b>Investor read:</b> {p["investor_read"]}</p>
          <div class="src"><a href="{p["source"]}">source</a></div></div>'''
    fed = "".join(card(p, "Federal") for p in pol["federal"])
    st = "".join(card(p, "State") for p in pol["state"])
    return (f'<h2>Government policy &amp; laws coming onboard</h2>'
            f'<p class="sub">Demand-side federal schemes concentrate buyer competition at affordable price points; '
            f'state planning reforms create land-value uplift near transport. Both are tailwinds for sub-$600k stock.</p>'
            f'<h3 class="ph">Federal</h3><div class="pgrid">{fed}</div>'
            f'<h3 class="ph">State &amp; Territory</h3><div class="pgrid">{st}</div>')


def _catalyst_section() -> str:
    cat = json.loads(CATALYSTS.read_text())
    reviewed = cat.get("last_reviewed", "")
    order = {"Active": 0, "Under construction": 0, "Announced": 1, "Planned": 2,
             "Delayed": 2, "Emerging": 2, "Uncertain": 3, "Cancelled": 3}
    ms = sorted(cat["markets"], key=lambda m: (order.get(m.get("status"), 2), m["state"], m["market"]))
    cards = "".join(
        f'''<div class="pcard">
          <div class="pcard-h"><span class="ploc">{m["market"]} · {m["state"]}</span>{_status_badge(m.get("status",""))}</div>
          <p class="m-theme">{m["theme"]}</p>
          <p class="m-detail" style="color:var(--muted)"><b>Funding:</b> {m.get("funding","")} &nbsp;·&nbsp; <b>Jobs:</b> {m.get("jobs","")} &nbsp;·&nbsp; <b>By:</b> {m.get("timeframe","")}</p>
          <p class="m-detail invest"><b>Potential impact on growth:</b> {m.get("impact","")}</p>
          <div class="src"><a href="{m.get("source","")}">source</a></div>
        </div>''' for m in ms
    )
    watch = "".join(f'<li><b>{w["market"]} ({w["state"]})</b> — {w["note"]}</li>' for w in cat.get("watch", []))
    return (f'<h2>Catalysts &amp; their potential impact</h2>'
            f'<p class="sub">Every committed funding/jobs catalyst and <b>what it likely means for capital and rental growth</b> — ordered by status, so cancelled/uncertain projects (e.g. Whyalla) don\'t mislead. Last reviewed {reviewed}.</p>'
            f'<div class="pgrid">{cards}</div>'
            + (f'<h3 class="ph">On the watchlist</h3><div class="panel"><ul class="tight">{watch}</ul></div>' if watch else ""))


def _load_live_prices() -> dict:
    p = ROOT / "data" / "live_prices.json"
    if not p.exists():
        return {}
    try:
        d = json.loads(p.read_text())
        return {code: v.get("median_asking") for code, v in d.get("suburbs", {}).items() if v.get("median_asking")}
    except Exception:
        return {}


def _lookup_data(recs: list[dict], live: dict) -> str:
    """Compact JSON of every suburb for the client-side lookup."""
    def asset(a):
        # [price(hidden→None), score, band(hidden), cycle, market_yield, gross_yield]
        # The site shows no dollar figures, so the price is not shipped; yield is kept only
        # to render the ✓/— rental-yield gate (a scoring input, shown non-numerically).
        return [None, a["score"], None, a["cycle"],
                a["market_yield"], a["gross_yield"]] if a else None
    out = []
    for r in recs:
        out.append({
            "n": r["name"], "st": r["state"],
            "h": asset(r.get("house")), "t": asset(r.get("townhouse")),
            "rp": r.get("ripple_gap"), "ses": r.get("seifa_decile"), "gf": r.get("gentrify_flag"),
            "ic": 1 if r.get("income_confirmed") else 0, "ivs": r.get("income_vs_state"),
            "hs": 1 if r.get("hotspot") else 0, "eb": r.get("econ_base"),
            "mig": r.get("net_migration_per_1000"), "pg": r.get("pop_growth_pa"),
            "pj": r.get("proj_pop_growth_10yr"), "sup": r.get("catchment_influx_pct"),
            "i3": [f"{n} {p}%" for n, p in (r.get("top3_industries") or [])],
            "oo": r.get("owner_occupier_pct"),      # owner-occupier share % (2021)
            "od": r.get("owner_occupier_delta"),    # change in that share 2016->2021 (traction)
            "om": r.get("owned_mortgage_pct"),      # owned-with-mortgage % (recent OO buyers)
            "dh": r.get("pct_house"), "dt": r.get("pct_townhouse"), "df": r.get("pct_flat"),
        })
    return json.dumps(out, separators=(",", ":"))


def build():
    data = json.loads(ANALYSIS.read_text())
    recs = data["suburbs"]
    live = _load_live_prices()
    strat_nav = "".join(
        f'<button class="strat-btn{" active" if a==ASSETS[0][0] else ""}" onclick="showStrategy(\'{a}\')" data-strat="{a}">{lbl}</button>'
        for a, lbl in ASSETS
    )
    strat_blocks = "".join(
        _strategy_block(recs, a, lbl, active=(a == ASSETS[0][0])) for a, lbl in ASSETS
    )
    n_gentrify = sum(1 for r in recs if r.get("gentrify_flag") == "Gentrifying")
    n_ruled = len(data.get("ruled_out_oversupply", []))
    n_hotspot = sum(1 for r in recs if r.get("hotspot"))
    # Preload the Compare tab with the 3 top-scoring house suburbs so it's useful on open.
    cmp_default = [r["name"] for r in sorted(
        recs, key=lambda r: (r.get("house") or {}).get("score", 0), reverse=True)[:3]]
    # Compact change-signature embedded in the page so tomorrow's run can diff against
    # it (per code: name, state, house score+rank, townhouse score+rank, hotspot, gentrify).
    sig = {"generated": data["generated"][:10], "s": {}}
    for r in recs:
        h, t = r.get("house") or {}, r.get("townhouse") or {}
        sig["s"][r["code"]] = [r["name"], r["state"], h.get("score"), h.get("rank"),
                               t.get("score"), t.get("rank"),
                               1 if r.get("hotspot") else 0, r.get("gentrify_flag") or ""]
    html = _PAGE.format(
        cmpdefault=json.dumps(cmp_default),
        sigdata=json.dumps(sig, separators=(",", ":")),
        stratnav=strat_nav, stratblocks=strat_blocks,
        economy=_economy_section(recs), news=_live_news_section() + _news_growth_section(recs),
        scenario=_scenario_section(recs), framework=_framework_note(),
        policy=_policy_section(), catalyst=_catalyst_section(), context=_context_section(),
        trends=_trends_section(data.get("trends", {"states": [], "cities": []})),
        outlook=_outlook_section(data.get("projections", {}), data.get("trends", {"states": []})),
        summary=_summary_section(recs, data.get("trends", {"states": []}), data.get("projections", {})),
        lookupdata=_lookup_data(recs, live),
        total=data["count"], n_house=data["n_house"], n_townhouse=data["n_townhouse"],
        n_gentrify=n_gentrify, n_ruled=n_ruled, n_hotspot=n_hotspot,
        generated=data["generated"][:10], built=_built_stamp(),
    )
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)   # reports/ isn't tracked in git
    OUTPUT.write_text(html)
    return OUTPUT


_PAGE = """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#0b0f0d">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Sersi">
<link rel="manifest" href="manifest.json">
<title>Sersi — Property Growth Map</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;450;500;600;700&family=IBM+Plex+Serif:ital,wght@0,400;0,500;0,600;0,700;1,400&display=swap" rel="stylesheet">
<style>
/* ---- "The Survey": an analyst's research instrument. Ink, paper & signal-green.
   Type: IBM Plex Serif (display) · Sans (body) · Mono (data/labels/tabs). ---- */
:root{{
  --bg:#eceeea;--panel:#fbfcfa;--panel2:#f2f4ef;--ink:#161f1a;--muted:#5c6a61;
  --line:#d6dcd2;--line2:#c4ccbe;--accent:#0b7a54;--accent2:#0d6e4e;--accent-soft:#dbeee5;
  --good:#0b7a54;--warn:#9a5c17;--bad:#a5322a;--chip:#e4e8df;
  --serif:"IBM Plex Serif",Georgia,"Times New Roman",serif;
  --sans:"IBM Plex Sans",-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  --mono:"IBM Plex Mono",ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,monospace;
  --shadow:0 1px 2px rgba(22,31,26,.05),0 8px 24px rgba(22,31,26,.05);
}}
@media(prefers-color-scheme:dark){{:root:not([data-theme="light"]){{
  --bg:#0b0f0d;--panel:#121815;--panel2:#0f1512;--ink:#e5ece7;--muted:#8a988e;
  --line:#212a24;--line2:#2c372f;--accent:#35d69a;--accent2:#43e0a6;--accent-soft:#102019;
  --good:#35d69a;--warn:#e0a95e;--bad:#ef8a7e;--chip:#182019;
  --shadow:0 1px 2px rgba(0,0,0,.3),0 10px 30px rgba(0,0,0,.35);
}}}}
:root[data-theme="dark"]{{
  --bg:#0b0f0d;--panel:#121815;--panel2:#0f1512;--ink:#e5ece7;--muted:#8a988e;
  --line:#212a24;--line2:#2c372f;--accent:#35d69a;--accent2:#43e0a6;--accent-soft:#102019;
  --good:#35d69a;--warn:#e0a95e;--bad:#ef8a7e;--chip:#182019;
  --shadow:0 1px 2px rgba(0,0,0,.3),0 10px 30px rgba(0,0,0,.35);
}}
:root[data-theme="light"]{{
  --bg:#eceeea;--panel:#fbfcfa;--panel2:#f2f4ef;--ink:#161f1a;--muted:#5c6a61;
  --line:#d6dcd2;--line2:#c4ccbe;--accent:#0b7a54;--accent2:#0d6e4e;--accent-soft:#dbeee5;
  --good:#0b7a54;--warn:#9a5c17;--bad:#a5322a;--chip:#e4e8df;
  --shadow:0 1px 2px rgba(22,31,26,.05),0 8px 24px rgba(22,31,26,.05);
}}
*{{box-sizing:border-box}}
html{{scroll-behavior:smooth}}
body{{margin:0;background:var(--bg);color:var(--ink);font:16px/1.6 var(--sans);-webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility}}
.wrap{{max-width:1160px;margin:0 auto;padding:28px 22px 90px}}
/* Hero reads like a survey document header: mono eyebrow, serif title, a data status-strip. */
header.hero{{padding:30px 0 20px;border-bottom:2px solid var(--ink);margin-bottom:22px;position:relative}}
.kicker{{color:var(--accent);font-family:var(--mono);font-weight:600;letter-spacing:.16em;text-transform:uppercase;font-size:11.5px;display:flex;align-items:center;gap:8px}}
.kicker::before{{content:"";width:26px;height:2px;background:var(--accent);display:inline-block}}
h1{{font-family:var(--serif);font-weight:600;font-size:38px;line-height:1.08;margin:14px 0 12px;letter-spacing:-.015em;max-width:16ch;text-wrap:balance}}
.lede{{color:var(--muted);font-size:16px;line-height:1.6;max-width:60ch;margin:0}}
.lede b{{color:var(--ink);font-weight:600}}
.meta{{color:var(--muted);font-family:var(--mono);font-size:11.5px;letter-spacing:.02em;margin-top:16px;padding-top:12px;border-top:1px solid var(--line);display:flex;flex-wrap:wrap;gap:6px 16px}}
h2{{font-family:var(--serif);font-weight:600;font-size:25px;margin:40px 0 8px;letter-spacing:-.01em;text-wrap:balance}} h2 .num{{color:var(--accent);margin-right:8px;font-variant-numeric:tabular-nums}}
h3.ph{{font-family:var(--mono);font-size:11.5px;color:var(--muted);text-transform:uppercase;letter-spacing:.12em;margin:24px 0 12px;display:flex;align-items:center;gap:10px}}
h3.ph::after{{content:"";flex:1;height:1px;background:var(--line)}}
.sub{{color:var(--muted);margin:0 0 16px;max-width:70ch;line-height:1.6}}
.banner{{background:var(--accent-soft);border:1px solid var(--line);border-radius:10px;padding:12px 16px;font-size:13.5px;margin:16px 0}}
.panel{{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:18px 20px;box-shadow:var(--shadow)}}
.stat-row{{display:flex;gap:12px;flex-wrap:wrap;margin:18px 0}}
.stat{{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px 16px;flex:1;min-width:150px;box-shadow:var(--shadow);position:relative;overflow:hidden}}
.stat::before{{content:"";position:absolute;left:0;top:0;bottom:0;width:3px;background:var(--accent)}}
.stat .v{{font-family:var(--mono);font-size:23px;font-weight:600;letter-spacing:-.01em}} .stat .l{{font-size:12px;color:var(--muted);margin-top:2px}}
/* Horizontal tab bar across the top (sticky), content full-width below it */
.layout{{display:block;margin-top:12px}}
.sidebar{{position:sticky;top:0;z-index:30;display:flex;flex-direction:row;flex-wrap:wrap;gap:4px;width:100%;background:color-mix(in srgb,var(--bg) 88%,transparent);backdrop-filter:blur(8px);border-bottom:1px solid var(--line);padding:10px 0;margin-bottom:18px}}
.content{{width:100%;min-width:0}}
.ptab{{background:transparent;border:1px solid transparent;border-radius:8px;padding:7px 12px;font-family:var(--mono);font-size:12.5px;font-weight:500;letter-spacing:.01em;color:var(--muted);cursor:pointer;white-space:nowrap;transition:color .12s,background .12s}}
.ptab:hover{{background:var(--panel2);color:var(--ink)}}
.ptab.active{{color:var(--accent);background:var(--accent-soft);border-color:var(--accent);font-weight:600}}
.page{{display:none}} .page.active{{display:block}}
/* Mobile: same bar, but swipe sideways instead of wrapping */
@media(max-width:760px){{
  .sidebar{{flex-wrap:wrap;overflow:visible;gap:5px;padding:8px 0;box-shadow:0 2px 8px rgba(0,0,0,.06)}}
  .ptab{{flex:0 0 auto;padding:6px 10px;font-size:12.5px}}
}}
/* Floating back-to-top */
.totop{{position:fixed;right:16px;bottom:16px;width:46px;height:46px;border-radius:50%;background:var(--accent);color:#fff;border:none;font-size:20px;line-height:1;cursor:pointer;box-shadow:0 3px 12px rgba(0,0,0,.28);opacity:0;pointer-events:none;transition:opacity .2s;z-index:40}}
.totop.show{{opacity:1;pointer-events:auto}}
@media(prefers-reduced-motion:reduce){{html{{scroll-behavior:auto}}}}
/* Mobile: compact the top so the tab bar is reachable near the top of the page */
@media(max-width:760px){{
  header.hero{{padding:14px 0 8px;margin-bottom:10px}}
  h1{{font-size:22px}}
  .lede{{display:none}} .meta{{display:none}}
  .banner{{font-size:12px;padding:10px 12px;margin:10px 0}}
  .pricebasis{{display:none}}   /* keep the search box; the main banner already covers the price caveat */
  .lookup input{{font-size:14px;padding:10px 12px}}
}}
.lookup{{margin:6px 0 4px}}
.lookup input{{width:100%;padding:14px 16px;font-size:15px;font-family:var(--sans);border:1px solid var(--line2);border-radius:10px;background:var(--panel);color:var(--ink);box-shadow:var(--shadow)}}
.lookup input:focus{{outline:none;border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-soft)}}
.lookup input::placeholder{{color:var(--muted)}}
.pricebasis{{font-size:12px;color:var(--muted);background:var(--accent-soft);border:1px solid var(--line);border-radius:8px;padding:8px 12px;margin-top:8px}}
/* ---- Compare tab ---- */
#cmpq{{width:100%;padding:12px 14px;font-size:15px;border:1px solid var(--line);border-radius:10px;background:var(--panel);color:var(--ink)}}
#cmpq:focus{{outline:none;border-color:var(--accent)}}
#cmpSuggest{{margin-top:6px;display:flex;flex-wrap:wrap;gap:4px}}
.cmpadd{{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:6px 10px;font-size:13px;cursor:pointer;color:var(--ink)}}
.cmpadd:hover{{background:var(--accent-soft);border-color:var(--accent)}}
.cmpchips{{display:flex;flex-wrap:wrap;gap:6px;margin:12px 0}}
.cmpchip{{background:var(--accent-soft);border:1px solid var(--line);border-radius:999px;padding:5px 12px;font-size:13px;font-weight:600}}
.cmpchip b{{cursor:pointer;color:var(--muted);margin-left:6px}}
.cmpchip b:hover{{color:var(--accent)}}
.cmptable{{border-collapse:collapse;min-width:520px}}
.cmptable th,.cmptable td{{border:1px solid var(--line);padding:7px 10px;font-size:13px;text-align:left;vertical-align:top}}
.cmptable thead th{{background:var(--panel);font-weight:700}}
.cmptable td a{{color:var(--accent)}}
.cmpmetric{{color:var(--muted);font-weight:600;white-space:nowrap}}
.cmpbest{{background:var(--accent-soft);font-weight:700}}
.vgline{{background:var(--accent-soft);border-radius:6px;padding:4px 8px}}
.vgcard{{border-left:3px solid var(--accent)}}
#lookupResults{{margin-top:10px}}
.lucard{{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin-bottom:10px;box-shadow:var(--shadow);border-left:3px solid var(--accent)}}
.lucard h4{{margin:0 0 8px;font-family:var(--serif);font-weight:600;font-size:16.5px}}
.lugrid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(155px,1fr));gap:7px 16px;font-size:12.5px}}
.lugrid div span{{color:var(--muted)}}
.lugrid b{{font-variant-numeric:tabular-nums}}
.ind{{display:flex;justify-content:space-between;gap:8px;font-size:12px;padding:1px 0}} .indpct{{color:var(--muted);font-variant-numeric:tabular-nums}}
.chart-grid{{display:grid;grid-template-columns:1fr 1fr;gap:14px}} @media(max-width:820px){{.chart-grid{{grid-template-columns:1fr}}}}
.chart{{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px 16px}}
.chart-t{{font-size:14px;font-weight:700;margin-bottom:2px}} .chart-s{{font-size:12px;color:var(--muted);margin-bottom:8px}}
.chart svg{{width:100%;height:auto}}
.legend{{display:flex;flex-wrap:wrap;gap:10px;margin-top:8px}} .lg{{font-size:11px;color:var(--muted);display:flex;align-items:center;gap:4px}}
.lg i{{width:10px;height:10px;border-radius:2px;display:inline-block}}
.jump{{background:var(--accent-soft);border:1px solid var(--line);border-radius:6px;padding:2px 8px;font-family:var(--mono);font-size:10.5px;color:var(--accent);cursor:pointer;font-weight:500}}
.jump:hover{{border-color:var(--accent)}}
.sersi{{background:linear-gradient(180deg,var(--accent-soft),transparent);border:1px solid var(--accent);border-radius:12px;padding:16px 18px;margin:10px 0 18px;box-shadow:var(--shadow)}}
.sersi-h{{font-family:var(--mono);font-size:12.5px;font-weight:600;letter-spacing:.04em;text-transform:uppercase;color:var(--accent);margin-bottom:8px}}
.newslist{{display:flex;flex-direction:column;gap:2px;margin-bottom:16px}}
.newsitem{{padding:9px 12px;border:1px solid var(--line);border-radius:9px;background:var(--panel)}}
.newsitem a{{font-size:14px;font-weight:600}}
.newsmeta{{display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin-top:3px;font-size:11px;color:var(--muted)}}
.strat-tabs{{display:flex;gap:8px;flex-wrap:wrap;margin:16px 0}}
.strat-btn{{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:11px 18px;font-size:14.5px;font-weight:600;color:var(--muted);cursor:pointer;box-shadow:var(--shadow)}}
.strat-btn:hover{{border-color:var(--line2)}}
.strat-btn.active{{color:var(--accent);background:var(--accent-soft);border-color:var(--accent)}}
.strat{{display:none}} .strat.active{{display:block}}
.tabs{{display:flex;gap:2px;flex-wrap:wrap;margin:10px 0 16px;border-bottom:1px solid var(--line);padding-bottom:0}}
.tabbtn{{background:transparent;border:none;border-bottom:2px solid transparent;border-radius:0;padding:9px 14px;font-family:var(--mono);font-size:12px;font-weight:500;letter-spacing:.02em;color:var(--muted);cursor:pointer;margin-bottom:-1px}}
.tabbtn:hover{{color:var(--ink)}}
.tabbtn.active{{color:var(--accent);border-bottom-color:var(--accent);font-weight:600}}
.tabpanel{{display:none}} .tabpanel.active{{display:block}}
.panel-note{{color:var(--muted);font-size:13.5px;margin:6px 0 12px}}
.tablewrap{{overflow-x:auto;border:1px solid var(--line);border-radius:12px;background:var(--panel);box-shadow:var(--shadow)}}
table{{width:100%;border-collapse:collapse;font-size:13.5px;min-width:860px}}
th,td{{text-align:left;padding:11px 13px;border-bottom:1px solid var(--line);white-space:nowrap}}
th{{color:var(--muted);font-family:var(--mono);font-weight:500;font-size:10.5px;text-transform:uppercase;letter-spacing:.06em;position:sticky;top:0;background:var(--panel2);border-bottom:1px solid var(--line2)}}
tbody td{{font-variant-numeric:tabular-nums}}
tbody td:first-child{{font-family:var(--mono);color:var(--muted);font-size:12px}}
td.num,th.num{{text-align:right;font-variant-numeric:tabular-nums}}
tbody tr{{transition:background .1s}} tbody tr:hover{{background:var(--accent-soft)}}
tbody tr:last-child td{{border-bottom:none}}
.sub2{{color:var(--muted);font-size:11px}} .hit{{color:var(--good);font-weight:600}} .warn-flag{{color:var(--warn);font-weight:600}}
.badge{{border-radius:5px;padding:2px 7px;font-family:var(--mono);font-size:10.5px;font-weight:500;letter-spacing:.02em}}
.b-early{{background:rgba(26,127,90,.16);color:var(--good)}} .b-mid{{background:rgba(180,105,14,.16);color:var(--warn)}}
.b-late{{background:rgba(179,38,30,.14);color:var(--bad)}} .b-flat{{background:var(--chip);color:var(--muted)}}
.pgrid{{display:grid;grid-template-columns:1fr 1fr;gap:12px}} @media(max-width:760px){{.pgrid{{grid-template-columns:1fr}}}}
.pcard{{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:16px 18px;box-shadow:var(--shadow)}}
.pcard-h{{display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:8px}}
.ploc{{font-family:var(--mono);font-size:10.5px;font-weight:600;color:var(--accent);text-transform:uppercase;letter-spacing:.08em}}
.m-name{{font-family:var(--serif);font-size:16.5px;font-weight:600;margin:2px 0;line-height:1.25}} .pstatus{{font-size:12px;color:var(--muted);margin:0 0 8px}}
.m-detail{{font-size:13.5px;margin:6px 0 0}} .invest{{color:var(--ink);background:var(--accent-soft);border-radius:8px;padding:8px 10px;margin-top:8px}}
.src{{font-size:12px;margin-top:8px}} a{{color:var(--accent);text-decoration:none}} a:hover{{text-decoration:underline}}
ul.tight{{margin:0;padding-left:18px}} ul.tight li{{margin:6px 0;font-size:14px}}
.foot{{margin-top:38px;padding-top:16px;border-top:1px solid var(--line);color:var(--muted);font-size:12.5px}}
.theme-toggle{{position:fixed;top:14px;right:14px;background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:6px 10px;font-family:var(--mono);font-size:11px;color:var(--muted);cursor:pointer;z-index:10;box-shadow:var(--shadow)}}
.theme-toggle:hover{{color:var(--accent);border-color:var(--accent)}}
details{{margin-top:10px}} summary{{cursor:pointer;color:var(--accent);font-size:13.5px}}
</style></head><body>
<button class="theme-toggle" onclick="var r=document.documentElement,d=r.getAttribute('data-theme')||(matchMedia('(prefers-color-scheme:dark)').matches?'dark':'light');r.setAttribute('data-theme',d==='dark'?'light':'dark')">◐ theme</button>
<button class="totop" id="toTop" onclick="window.scrollTo({{top:0,behavior:'smooth'}})" aria-label="Back to top" title="Back to top">↑</button>
<div class="wrap">
  <header class="hero">
    <div class="kicker">🛰 Sersi · Australian property research agent</div>
    <h1>Where Australia's next capital growth is being built</h1>
    <p class="lede"><b>Sersi is an automated agent</b> that scores every Australian suburb on the fundamentals that drive capital growth <b>before price moves</b> — refreshed daily from public ABS data, the government funding/jobs pipeline and live news. Start with the shortlist below, or jump to any tab.</p>
    <div class="meta">🔄 Last refreshed {built} · public data only · not financial advice — see footer</div>
  </header>


  <div class="lookup">
    <input id="q" type="text" placeholder="🔎 Look up any suburb — type a name (e.g. Geelong, Lara, Broadmeadows)…" oninput="doLookup(this.value)" autocomplete="off">
    <div id="lookupResults"></div>
  </div>

  <div class="layout">
  <nav class="sidebar">
    <button class="ptab active" onclick="showPage('summary')">⭐ Summary</button>
    <button class="ptab" onclick="showPage('shortlist')">📊 Shortlist</button>
    <button class="ptab" onclick="showPage('compare')">⚖️ Compare</button>
    <button class="ptab" onclick="showPage('trends')">📈 Trends</button>
    <button class="ptab" onclick="showPage('outlook')">🔮 10yr Outlook</button>
    <button class="ptab" onclick="showPage('economy')">🏭 Economy</button>
    <button class="ptab" onclick="showPage('scenario')">🔗 Scenario &amp; Ripple</button>
    <button class="ptab" onclick="showPage('news')">📰 News</button>
    <button class="ptab" onclick="showPage('catalysts')">🎯 Catalysts</button>
    <button class="ptab" onclick="showPage('policy')">🏛 Policy</button>
    <button class="ptab" onclick="showPage('context')">🧭 Context</button>
    <button class="ptab" onclick="showPage('method')">📐 Method</button>
  </nav>
  <div class="content">

  <div class="page active" id="page-summary">{summary}</div>

  <div class="page" id="page-shortlist">
  <h2>Shortlist by strategy &amp; state</h2>
  <p class="sub"><b>Growth-fundamentals ranking — no prices shown.</b> Composite score (0–100) per asset = yield 15 · <b>gentrification 12</b> · population growth 12 · net migration 10 · <b>ripple 10</b> · affordability 9 · industry diversity 9 · supply scarcity 8 · runway (not-already-run) 6 · economic health 4 · liquidity 5. Yield and affordability are still scoring inputs, but the site leads purely with fundamentals, so we show the <b>Yield</b> gate as <span class="hit">✓</span> (clears ~4.5%+ market yield) rather than a dollar figure. <b>Ripple</b> = % below similar-income neighbours within 10km (arbitrage upside). <b>Econ base</b> = industry mix (Anchor / Mixed / Commodity — hover for top-3). <b>SES</b> = socio-economic decile (1 = most disadvantaged): <span class="hit">▲ gentrifying</span> · <span class="warn-flag">▼ trap</span>. <b>Cycle</b>: <span class="badge b-early">Early</span> · <span class="badge b-mid">Mid</span> · <span class="badge b-late">Late</span>. <b>Houses</b> = land play; <b>Townhouses/villas</b> = lower entry, higher yield.</p>
  <div class="strat-tabs">{stratnav}</div>
  {stratblocks}
  </div>

  <div class="page" id="page-compare">
  <h2>Compare suburbs side by side</h2>
  <p class="sub">Add any suburbs — from the shortlist or the full 1,873 — and line them up across every growth-fundamentals metric at once. This is the head-to-head the per-suburb lookup can't give you; the <span class="hit">best value in each row is highlighted</span>. Hazard and selling-agent links are in the last rows. No dollar figures — this is a fundamentals-only comparison.</p>
  <input id="cmpq" type="text" placeholder="➕ Add a suburb to compare — type a name (e.g. Gladstone, Spearwood)…" oninput="cmpSuggest(this.value)" autocomplete="off">
  <div id="cmpSuggest"></div>
  <div id="cmpChips" class="cmpchips"></div>
  <div id="cmpTable" class="tablewrap"></div>
  </div>

  <div class="page" id="page-trends">{trends}</div>
  <div class="page" id="page-outlook">{outlook}</div>
  <div class="page" id="page-economy">{economy}</div>
  <div class="page" id="page-scenario">{scenario}</div>
  <div class="page" id="page-news">{news}</div>
  <div class="page" id="page-catalysts">{catalyst}</div>
  <div class="page" id="page-policy">{policy}</div>
  <div class="page" id="page-context">{context}</div>

  <div class="page" id="page-method">
  <h2>Method &amp; limitations</h2>
  <div class="panel">
    <p class="m-detail"><b>All data is real and public:</b> median house &amp; attached (townhouse/villa) prices (state valuers-general via ABS Data by Region), median rent, net internal + overseas migration, dwelling approvals &amp; stock, income, unemployment, Census industry-of-employment — joined to ABS population by SA2.</p>
    <p class="m-detail" style="margin-top:8px"><b>No dollar figures are displayed.</b> This is a pure growth-<i>fundamentals</i> view. The composite score still uses price internally — for <b>rental yield</b> (15%) and <b>housing affordability</b> (9%, price-to-income) — so those price-derived signals shape the ranking, but the underlying dollar amounts aren't shown. Where yield matters to a decision we surface it non-numerically: a <span class="hit">✓</span> means the suburb clears the ~4.5%+ market-yield gate. For actual sold prices and agent price points, use the <b>RateMyAgent</b> link on each suburb.</p>
    <p class="m-detail" style="margin-top:8px"><b>Two strategies, scored separately:</b> "Houses" uses established-house medians &amp; house-based yield; "Townhouses/Villas" uses attached-dwelling medians &amp; attached yield. Shared macro signals (population, migration, supply, diversity, jobs) apply to both. Apartment-dominated SA2s (house median &lt; 0.85× unit) are dropped from the <i>house</i> list only.</p>
    <p class="m-detail" style="margin-top:8px"><b>Industry-diversity / single-industry risk</b> from Census shares across 19 ANZSIC industries; rewards spread, penalises commodity (mining+agri) exposure. <b>Trap-aware:</b> a 5,000-population floor keeps thin, illiquid towns off the shortlist.</p>
    <p class="m-detail" style="margin-top:8px"><b>Supply rule (committed influx ≠ developable land):</b> building approvals are a <i>known, committed</i> supply influx — distinct from developable land, which is uncertain and long-term (councils can change plans). A large influx drowns capital growth, so a suburb is <b>ruled out</b> if dwelling approvals exceed <b>8% of dwelling stock</b> either in the suburb itself or across its <b>5km catchment</b> (centroids from ABS ASGS boundaries; {n_ruled} suburbs removed — mostly greenfield estates like Ripley, Munno Para West, Alkimos). The "Supply 5km" column shows how close a survivor sits to that limit.</p>
    <p class="m-detail" style="margin-top:8px"><b>Gentrification potential (socio-economic):</b> from ABS SEIFA 2021 (IRSAD). Low socio-economic areas are among the biggest capital-growth drivers — but only when they're <i>improving</i>. The signal = <b>disadvantage × momentum</b> (people moving in + price growth), so a cheap, disadvantaged suburb with strong inflow (Munno Para West, Redbank Plains, Yarrabilba) scores high, while an equally cheap suburb that's losing people (Corio, Norlane) is flagged a <b>trap</b>, not a buy. The unemployment penalty was reduced so the model doesn't double-count disadvantage.</p>
    <details><summary>Known limitations</summary><p class="m-detail" style="margin-top:8px">Census rent understates market rent (an uplift is applied) and isn't split by dwelling type, so attached yield reuses the all-dwelling median rent. The price inputs behind the yield/affordability scores are 2024 ABS SA2-area medians nowcast to ~present (a capped trend estimate) — good enough for relative ranking, which is why they inform the score but aren't shown as figures. Apartment-heavy SA2s (house median &lt; 0.85× unit) are dropped from the house list to avoid a thin house sample.</p></details>
  </div>

  {framework}
  </div>
  </div><!--content-->
  </div><!--layout-->

  <div class="foot">
    <p><b>What Sersi is:</b> an automated research agent that ranks Australian suburbs on the macro fundamentals that drive capital growth, rebuilt daily from 100% free public data (ABS + state valuers-general) — no listings, Domain-free.</p>
    <p><b>Not financial advice.</b> General information only, not personal financial or investment advice. Do your own research and seek licensed advice before acting.</p>
    <p><b>Why no prices?</b> Sersi deliberately shows <b>no dollar figures</b> — it ranks suburbs on the growth <i>fundamentals</i> that move before price does, so you compare potential without anchoring on a headline median. Price still feeds the score internally (rental yield + affordability), surfaced only as a <b>✓ yield gate</b>. For actual sold prices, price points and who's selling, open the <b>RateMyAgent</b> link on any suburb — it ranks agents by sales volume and shows each one's median sale price.</p>
    <p class="refreshline"><b>🔄 Last refreshed:</b> {built}. Sersi re-runs the full pipeline automatically every morning (~7am AEST). <b>Data vintage:</b> {generated} — ABS "Data by Region" only updates a few times a year, so on most days there's <b>no material change</b> and the daily update above will say so; the page is still rebuilt and re-verified each morning.</p>
    <p>Population &amp; regional data © Australian Bureau of Statistics (ABS Data API, "Data by Region"). Policy &amp; catalyst figures from cited government/industry sources.</p>
  </div>
</div>
<!-- Compact change-signature: the next day's run reads this from the live page to
     detect what (if anything) moved — no server state or workflow change needed. -->
<script id="sersi-sig" type="application/json">{sigdata}</script>
<script>
var LOOKUP = {lookupdata};
var CMP_DEFAULT = {cmpdefault};
// asset = [price(hidden→null), score, band(hidden→null), cycle, market_yield, gross_yield].
// No dollar figures are shown — yield is rendered only as a ✓/— gate at ~4.5% market yield.
function yldGate(a){{ return a && a[4]>=4.5; }}
function yldCell(a){{ return a? (yldGate(a)?'<b class="hit">✓</b> <span class="sub2">clears 4.5%+ yield gate</span>':'<span class="sub2">— below 4.5% yield gate</span>') : '—'; }}
function fmtAsset(label, a){{
  if(!a) return '<div><span>'+label+':</span> —</div>';
  return '<div><span>'+label+':</span> score <b>'+a[1]+'</b> · '+a[3]+' · '+yldCell(a)+'</div>';
}}
// suburb name before the SA2 " - " compound, for cleaner external links
function coreName(n){{ return (n||'').split(' - ')[0]; }}
function gsearch(s, terms){{ return 'https://www.google.com/search?q='+encodeURIComponent(coreName(s.n)+' '+s.st+' '+terms); }}
// RateMyAgent ranks agents by sales volume in a suburb and shows each agent's median sale
// price — so it's how you find who sells the most there (and who sells at cheaper price points).
// Their page needs a postcode we don't hold, so we deep-link via a site-scoped search that
// lands on the suburb's agent ranking. Free, no scraping.
function raLink(s){{ var nm=coreName(s.n).replace(/\\s*\\([^)]*\\)/g,'').trim(); return 'https://www.google.com/search?q='+encodeURIComponent('site:ratemyagent.com.au '+nm+' '+s.st+' agents'); }}
// State-correct hazard viewers. Zoning is parcel-level (8 different state schemas),
// so we can't auto-flag or filter at suburb level on free data — these open the
// authoritative map where you check the exact address. Google fallback always resolves.
var HAZ={{
  NSW:{{f:'https://www.planningportal.nsw.gov.au/spatialviewer/', b:'https://www.planningportal.nsw.gov.au/spatialviewer/'}},
  VIC:{{f:'https://mapshare.vic.gov.au/vicplan/', b:'https://mapshare.vic.gov.au/vicplan/'}},
  QLD:{{f:'https://floodcheck.information.qld.gov.au/', b:null}},
  SA:{{f:'https://sappa.plan.sa.gov.au/', b:'https://sappa.plan.sa.gov.au/'}},
  WA:{{f:'https://maps.water.wa.gov.au/floodmap/', b:'https://maps.slip.wa.gov.au/landgate/bushfireprone/'}},
  TAS:{{f:'https://maps.thelist.tas.gov.au/listmap/app/list/map', b:'https://maps.thelist.tas.gov.au/listmap/app/list/map'}},
  ACT:{{f:'https://www.actmapi.act.gov.au/', b:'https://www.actmapi.act.gov.au/'}}
}};
function hazLink(s, kind, label){{
  var h=HAZ[s.st]||{{}};
  var url=(kind==='flood'?h.f:h.b) || gsearch(s, kind==='flood'?'flood map':'bushfire prone land map');
  return '<a target="_blank" rel="noopener" href="'+url+'">'+label+'</a>';
}}
function doLookup(q){{
  var box=document.getElementById('lookupResults');
  q=(q||'').trim().toLowerCase();
  if(q.length<2){{box.innerHTML='';return;}}
  var hits=LOOKUP.filter(function(s){{return s.n.toLowerCase().indexOf(q)>=0;}}).slice(0,25);
  if(!hits.length){{box.innerHTML='<div class="panel-note">No suburb matches "'+q+'".</div>';return;}}
  box.innerHTML=hits.map(function(s){{
    var g = s.gf==='Gentrifying' ? ('▲ Gentrifying'+(s.ic?' ✓ (income confirming)':'')) : (s.gf==='Trap'?'▼ Trap':'');
    return '<div class="lucard"><h4>'+(s.hs?'🔥 ':'')+s.n+' <span style="color:var(--muted);font-weight:600">· '+s.st+'</span></h4>'+
      '<div class="lugrid">'+
      fmtAsset('House', s.h)+fmtAsset('Townhouse/villa', s.t)+
      '<div style="grid-column:1/-1"><span>Housing mix:</span> '+(s.dh!=null?
        s.dh+'% house · '+s.dt+'% townhouse · '+s.df+'% flat'+
        (s.dh<60?' <span class="sub2">(flat-heavy — the house median rests on a thin sample)</span>':''):'—')+'</div>'+
      '<div><span>Ripple:</span> '+(s.rp!=null?(s.rp>=15?'+'+s.rp+'% below peers':s.rp+'%'):'—')+'</div>'+
      '<div><span>Socio-economic:</span> decile '+(s.ses||'—')+' '+g+'</div>'+
      '<div><span>Income vs state:</span> '+(s.ivs!=null?(s.ivs>0?'+':'')+s.ivs+'pp/yr':'—')+'</div>'+
      '<div><span>Net migration/1k:</span> '+(s.mig!=null?s.mig:'—')+'</div>'+
      '<div><span>Pop growth (recent):</span> '+(s.pg!=null?s.pg+'%/yr':'—')+'</div>'+
      '<div><span>Projected 10yr:</span> '+(s.pj!=null?'+'+s.pj+'% to 2034 (est)':'—')+'</div>'+
      '<div><span>Supply 5km:</span> '+(s.sup!=null?s.sup+'%':'—')+'</div>'+
      '<div><span>Economy:</span> '+(s.eb||'—')+'</div>'+
      '<div><span>Owner-occupiers:</span> '+(s.oo!=null? '<b>'+s.oo+'%</b> own'+
        (s.od!=null?' <span class="sub2">('+(s.od>0?'+':'')+s.od+'pp vs 2016'+(s.od>0?', owners moving in':(s.od<0?', investors gaining':''))+')</span>':'') : '—')+'</div>'+
      '<div style="grid-column:1/-1"><span>Top industries:</span> '+(s.i3&&s.i3.length?s.i3.join(' · '):'—')+'</div>'+
      '<div style="grid-column:1/-1;margin-top:4px"><span>⚠️ Check hazards:</span> '+
        hazLink(s,'flood','🌊 Flood map')+' · '+hazLink(s,'bush','🔥 Bushfire map')+
        '<span class="sub2"> — zoning is per-property; a suburb can be only partly affected. Confirm the exact address before buying.</span></div>'+
      '<div style="grid-column:1/-1;margin-top:4px"><span>🧑‍💼 Selling agents:</span> '+
        '<a target="_blank" rel="noopener" href="'+raLink(s)+'">Top-selling agents · RateMyAgent →</a>'+
        '<span class="sub2"> — ranks agents by sales volume in this suburb and shows each agent\\'s median sale price, so you can see who sells the most and who transacts at cheaper price points.</span></div>'+
      '</div></div>';
  }}).join('');
}}
// ---- Compare tab: side-by-side across every metric ----
var CMP=[];
function cmpSuggest(q){{
  var box=document.getElementById('cmpSuggest');
  q=(q||'').trim().toLowerCase();
  if(q.length<2){{box.innerHTML='';return;}}
  var hits=LOOKUP.filter(function(s){{return s.n.toLowerCase().indexOf(q)>=0 && CMP.indexOf(s)<0;}}).slice(0,8);
  box.innerHTML=hits.map(function(s){{
    return '<button class="cmpadd" onclick="cmpAdd('+JSON.stringify(s.n)+')">+ '+s.n+' <span class="sub2">'+s.st+'</span></button>';
  }}).join('');
}}
function cmpAdd(name){{
  var s=LOOKUP.filter(function(x){{return x.n===name;}})[0];
  if(s && CMP.indexOf(s)<0) CMP.push(s);
  document.getElementById('cmpq').value=''; document.getElementById('cmpSuggest').innerHTML='';
  renderCompare();
}}
function cmpRemove(name){{ CMP=CMP.filter(function(s){{return s.n!==name;}}); renderCompare(); }}
// Yield gate (✓/—) for the compare grid — a scoring input surfaced non-numerically.
function cmpGate(a){{ return a? (a[4]>=4.5?1:0) : null; }}
function fmtGate(v){{ return v==null?'—':(v?'✓':'—'); }}
var CMP_ROWS=[
  {{l:'State', g:function(s){{return s.st;}}}},
  {{l:'House score /100', dir:'high', g:function(s){{return s.h?s.h[1]:null;}}}},
  {{l:'House cycle', g:function(s){{return s.h?s.h[3]:'—';}}}},
  {{l:'House yield gate (4.5%+)', dir:'high', g:function(s){{return cmpGate(s.h);}}, f:fmtGate}},
  {{l:'Townhouse score /100', dir:'high', g:function(s){{return s.t?s.t[1]:null;}}}},
  {{l:'Townhouse yield gate (4.5%+)', dir:'high', g:function(s){{return cmpGate(s.t);}}, f:fmtGate}},
  {{l:'Projected 10yr growth', dir:'high', g:function(s){{return s.pj;}}, f:function(v){{return v==null?'—':'+'+v+'%';}}}},
  {{l:'Pop growth /yr', dir:'high', g:function(s){{return s.pg;}}, f:function(v){{return v==null?'—':v+'%';}}}},
  {{l:'Net migration /1k', dir:'high', g:function(s){{return s.mig;}}}},
  {{l:'Ripple (below peers)', dir:'high', g:function(s){{return s.rp;}}, f:function(v){{return v==null?'—':v+'%';}}}},
  {{l:'Owner-occupier %', dir:'high', g:function(s){{return s.oo;}}, f:function(v){{return v==null?'—':v+'%';}}}},
  {{l:'OO traction vs 2016', dir:'high', g:function(s){{return s.od;}}, f:function(v){{return v==null?'—':(v>0?'+':'')+v+'pp';}}}},
  {{l:'Housing mix H/T/F', g:function(s){{return s.dh==null?'—':(s.dh+'/'+s.dt+'/'+s.df+'%');}}}},
  {{l:'Supply 5km (low=better)', dir:'low', g:function(s){{return s.sup;}}, f:function(v){{return v==null?'—':v+'%';}}}},
  {{l:'Socio-economic', g:function(s){{return s.ses==null?'—':('decile '+s.ses+(s.gf==='Gentrifying'?' ▲':''));}}}},
  {{l:'Economy', g:function(s){{return s.eb||'—';}}}},
  {{l:'Top industries', g:function(s){{return (s.i3&&s.i3.length)?s.i3.join(', '):'—';}}}},
  {{l:'⚠️ Hazard check', g:function(s){{return hazLink(s,'flood','🌊 Flood')+' · '+hazLink(s,'bush','🔥 Bushfire');}}}},
  {{l:'🧑‍💼 Selling agents', g:function(s){{return '<a target="_blank" rel="noopener" href="'+raLink(s)+'">RateMyAgent →</a>';}}}}
];
function renderCompare(){{
  var chips=document.getElementById('cmpChips'), tbl=document.getElementById('cmpTable');
  chips.innerHTML=CMP.map(function(s){{
    return '<span class="cmpchip">'+s.n+' · '+s.st+' <b onclick="cmpRemove('+JSON.stringify(s.n)+')">×</b></span>';
  }}).join('');
  if(!CMP.length){{tbl.innerHTML='<p class="panel-note">No suburbs added yet — search above to add two or more.</p>';return;}}
  var html='<table class="cmptable"><thead><tr><th>Metric</th>'+
    CMP.map(function(s){{return '<th>'+s.n+'<br><span class="sub2">'+s.st+'</span></th>';}}).join('')+'</tr></thead><tbody>';
  CMP_ROWS.forEach(function(r){{
    var vals=CMP.map(r.g), best=null;
    if(r.dir){{
      var nums=vals.filter(function(v){{return typeof v==='number';}});
      if(nums.length>1) best=(r.dir==='high'?Math.max.apply(null,nums):Math.min.apply(null,nums));
    }}
    html+='<tr><td class="cmpmetric">'+r.l+'</td>'+vals.map(function(v){{
      var disp=r.f?r.f(v):(v==null?'—':v);
      var cls=(best!=null&&v===best)?' class="cmpbest"':'';
      return '<td'+cls+'>'+disp+'</td>';
    }}).join('')+'</tr>';
  }});
  tbl.innerHTML=html+'</tbody></table>';
}}
try{{ (CMP_DEFAULT||[]).forEach(cmpAdd); }}catch(e){{}}
function showPage(id){{
  document.querySelectorAll('.page').forEach(function(p){{p.classList.toggle('active',p.id==='page-'+id)}});
  document.querySelectorAll('.ptab').forEach(function(b){{b.classList.toggle('active',b.getAttribute('onclick').indexOf("'"+id+"'")>=0)}});
  // land on the content: bring the layout (sidebar + content) to the top
  var lay=document.querySelector('.layout');
  if(lay){{var y=lay.getBoundingClientRect().top+window.pageYOffset-8;window.scrollTo(0,Math.max(0,y));}}
}}
function showStrategy(a){{
  document.querySelectorAll('.strat').forEach(function(s){{s.classList.toggle('active',s.id==='strat-'+a)}});
  document.querySelectorAll('.strat-btn').forEach(function(b){{b.classList.toggle('active',b.getAttribute('data-strat')===a)}});
}}
function showBand(a,b){{
  var strat=document.getElementById('strat-'+a);
  strat.querySelectorAll('.tabpanel').forEach(function(p){{p.classList.toggle('active',p.id==='panel-'+a+'-'+b)}});
  strat.querySelectorAll('.tabbtn').forEach(function(x){{x.classList.toggle('active',x.getAttribute('data-band')===a+'-'+b)}});
}}
// show the back-to-top button once you've scrolled a bit
window.addEventListener('scroll',function(){{
  var b=document.getElementById('toTop'); if(b) b.classList.toggle('show', window.pageYOffset>350);
}},{{passive:true}});
</script>
</body></html>"""


if __name__ == "__main__":
    print("Wrote", build())
