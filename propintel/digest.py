"""Sersi — daily change digest.

Compares today's analysis to the previous snapshot and writes a short plain-English
update (data/latest_update.md, appended to data/changelog.md) so you can read
"what changed" in 20 seconds instead of browsing every tab.

Because ABS "Data by Region" only re-releases a few times a year, most days there
is genuinely nothing to report — the digest says so plainly, so you know the
rankings are unchanged without checking.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime

from .config import ROOT


def _today_aest() -> str:
    """Australian date. The daily run fires at 21:00 UTC = 07:00 AEST *next* day, so a
    UTC date would always read a day behind for an Australian reader."""
    try:
        from zoneinfo import ZoneInfo
        return str(datetime.now(ZoneInfo("Australia/Sydney")).date())
    except Exception:
        return str(date.today())

CURR = ROOT / "data" / "suburb_analysis.json"
PREV = ROOT / "data" / "suburb_analysis.prev.json"
LATEST = ROOT / "data" / "latest_update.md"
CHANGELOG = ROOT / "data" / "changelog.md"

# In the cloud (GitHub Actions) the workspace is wiped between runs, so a local prev
# snapshot never survives — without this the digest would say "first update — baseline
# captured" EVERY day. The live page embeds a compact change-signature (<script
# id="sersi-sig">); we read yesterday's straight off the deployed site and rebuild a
# minimal prev from it, so day-to-day change detection works with no server state and
# no workflow change. Locally, refresh.sh writes PREV itself, so this never fires.
LIVE_PAGE_URL = "https://asmitazz.github.io/propintel/"


def _ensure_prev() -> None:
    """If no local previous snapshot exists, rebuild one from the live page signature."""
    if PREV.exists():
        return
    try:
        import re
        import urllib.request
        req = urllib.request.Request(LIVE_PAGE_URL, headers={"User-Agent": "sersi-digest"})
        html = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "ignore")
        m = re.search(r'<script id="sersi-sig" type="application/json">(.*?)</script>', html, re.S)
        if not m:
            return
        sig = json.loads(m.group(1))
        subs = []
        for code, v in sig.get("s", {}).items():
            name, st, hs, hr, ts, tr, hot, gf = v
            rec = {"code": code, "name": name, "state": st,
                   "hotspot": bool(hot), "gentrify_flag": gf}
            if hs is not None:
                rec["house"] = {"score": hs, "rank": hr}
            if ts is not None:
                rec["townhouse"] = {"score": ts, "rank": tr}
            subs.append(rec)
        PREV.parent.mkdir(parents=True, exist_ok=True)
        PREV.write_text(json.dumps({"generated": sig.get("generated", ""), "suburbs": subs}))
    except Exception:
        pass   # first deploy with the signature, or site not up yet — show baseline msg


def _by_code(path):
    return {s["code"]: s for s in json.loads(path.read_text())["suburbs"]}


def _topn(m, asset, n=10):
    el = [s for s in m.values() if s.get(asset)]
    el.sort(key=lambda s: s[asset]["rank"])
    return el[:n]


def build_digest() -> str:
    today = _today_aest()
    if not CURR.exists():
        return _write(today, "No analysis found — run `analyze` first.")
    curr = _by_code(CURR)

    _ensure_prev()   # cloud runs have no local prev — pull the deployed snapshot
    if not PREV.exists():
        lines = ["First update — baseline captured. From tomorrow I'll report only what changes.",
                 "", f"Tracking **{len(curr):,}** suburbs. "
                 f"Top house pick: **{_topn(curr,'house')[0]['name']}**; "
                 f"top townhouse pick: **{_topn(curr,'townhouse')[0]['name']}**."]
        return _write(today, "\n".join(lines))

    prev = _by_code(PREV)

    # Did the underlying ABS data actually change? Scores are deterministic from the
    # ABS inputs, so identical scores => no release. Uses only fields carried in the
    # embedded signature, so a page-rebuilt prev compares identically to a full one.
    def sig(s):
        h, t = s.get("house") or {}, s.get("townhouse") or {}
        return (h.get("score"), t.get("score"), bool(s.get("hotspot")), s.get("gentrify_flag") or "")
    changed_codes = [c for c in curr if c in prev and sig(curr[c]) != sig(prev[c])]
    added = [c for c in curr if c not in prev]
    removed = [c for c in prev if c not in curr]

    if not changed_codes and not added and not removed:
        prev_date = json.loads(PREV.read_text()).get("generated", "")[:10]
        return _write(today,
            f"**No material changes** since {prev_date}. ABS data hasn't been re-released, "
            f"so every ranking, score and price is unchanged. Report was refreshed and verified. "
            f"(ABS 'Data by Region' updates a few times a year — I'll flag the day it moves.)")

    # Something moved — summarise it.
    parts = ["**ABS data refreshed — here's what moved:**", ""]
    for asset, label in (("house", "houses"), ("townhouse", "townhouses/villas")):
        prev_top = [s["code"] for s in _topn(prev, asset)]
        curr_top = _topn(curr, asset)
        curr_codes = [s["code"] for s in curr_top]
        entrants = [s for s in curr_top if s["code"] not in prev_top]
        dropped = [prev[c]["name"] for c in prev_top if c not in curr_codes]
        if entrants:
            parts.append(f"- **New in Top 10 {label}:** " +
                         ", ".join(f'{s["name"]} ({s["state"]}, #{s[asset]["rank"]})' for s in entrants))
        if dropped:
            parts.append(f"- **Dropped out of Top 10 {label}:** " + ", ".join(dropped))

    new_hot = [curr[c]["name"] for c in changed_codes if curr[c].get("hotspot") and not prev[c].get("hotspot")]
    if new_hot:
        parts.append(f"- **New 🔥 hotspots:** " + ", ".join(new_hot[:12]))
    new_gent = [curr[c]["name"] for c in changed_codes
                if curr[c].get("gentrify_flag") == "Gentrifying" and prev[c].get("gentrify_flag") != "Gentrifying"]
    if new_gent:
        parts.append(f"- **New ▲ gentrifiers:** " + ", ".join(new_gent[:12]))

    # biggest score moves (house)
    moves = []
    for c in changed_codes:
        hp, hc = (prev[c].get("house") or {}).get("score"), (curr[c].get("house") or {}).get("score")
        if hp is not None and hc is not None and abs(hc - hp) >= 2:
            moves.append((curr[c]["name"], curr[c]["state"], round(hc - hp, 1)))
    moves.sort(key=lambda x: -abs(x[2]))
    if moves:
        parts.append("- **Biggest score moves (houses):** " +
                     ", ".join(f'{n} ({st}) {"+" if d>0 else ""}{d}' for n, st, d in moves[:8]))
    if added:
        parts.append(f"- **{len(added)} suburb(s) newly in scope**, **{len(removed)} left scope.**")
    parts += ["", f"_{len(changed_codes)} suburbs changed. Open the report for the detail._"]
    return _write(today, "\n".join(parts))


def _catalyst_freshness() -> str:
    """Warn if the government funding/jobs data is aging (research-sourced, not an API)."""
    p = ROOT / "data" / "catalysts.json"
    if not p.exists():
        return ""
    try:
        reviewed = json.loads(p.read_text()).get("last_reviewed", "")
        d = date.fromisoformat(reviewed)
        age = (date.today() - d).days
        if age > 90:
            return (f"\n\n⚠️ **Government funding/jobs data is {age} days old** "
                    f"(last reviewed {reviewed}). It's research-sourced, not an API — "
                    f"ask me to refresh it (best at budget times: May federal, ~June states).")
    except Exception:
        pass
    return ""


# Theme buckets in priority order. The first four are the CORE property/infra/funding
# themes shown in the daily "what to check" list; "Jobs/industry" (index 4) and the
# General fallback are intentionally OUTSIDE the core so broad diplomacy / defence-
# cooperation / photo-op items don't leak into the property digest.
_NEWS_THEMES = [
    ("💰 Funding", re.compile(r"(\bbillion\b|\bmillion\b|\$[0-9]|\bfund(ing|ed)?\b|\bgrant\b|\bbudget\b|\binvest(ment|ing|ed)?\b|\bfinanc)", re.I)),
    ("🏗 Infrastructure", re.compile(r"\b(rail|metro|light rail|highway|motorway|freeway|road|airport|seaport|port|hospital|precinct|tunnel|construction|upgrade|interchange|bridge|station|infrastructure|pipeline|desalinat\w*|water treatment)\b", re.I)),
    ("🏛 Policy/planning", re.compile(r"\b(rezon\w*|planning|stamp duty|first[- ]home|interest rate|land release|dwelling|zoning|approval|planning reform|housing (target|strateg\w*|supply|reform|policy|accord|plan))\b", re.I)),
    ("🏠 Housing/property", re.compile(r"\b(housing|homes?|home build|new homes|apartment\w*|townhouse\w*|subdivision|estate|greenfield|development|\bsupply\b|property|rent|rents|vacanc\w*|median|auction|price|prices|sales|listings|build[- ]to[- ]rent)\b", re.I)),
    ("👷 Jobs/industry", re.compile(r"\b(jobs|employ\w*|defence|aukus|hydrogen|renewable|solar|wind|battery|energy|manufactur\w*|universit\w*|campus|mine|mining|gigafactory)\b", re.I)),
]
_CORE_THEMES = 4   # indices 0-3 are property/infra/funding; 4+ are excluded from the digest


def _classify_news(it: dict):
    text = f"{it.get('title','')} {it.get('desc','')}"
    for i, (label, rx) in enumerate(_NEWS_THEMES):
        if rx.search(text):
            return i, label
    return len(_NEWS_THEMES), "📰 General"


def _news_line() -> str:
    """Surface the notable NEW headlines (not just a count) so the reader knows what to
    actually track — tagged by theme and state, top items that move fundamentals first."""
    p = ROOT / "data" / "news_feed.json"
    if not p.exists():
        return ""
    try:
        items = json.loads(p.read_text()).get("items", [])
        seens = [it.get("first_seen") for it in items if it.get("first_seen")]
        if not seens:
            return ""
        newest = max(seens)                                  # today's batch (TZ-independent)
        fresh = [it for it in items if it.get("first_seen") == newest]
        if not fresh:
            return ""

        def esc(s: str) -> str:
            return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("*", "").replace("_", "")

        ranked = []
        for it in fresh:
            ti, label = _classify_news(it)
            if ti >= _CORE_THEMES:                           # property/infra/funding only
                continue
            has_geo = 0 if it.get("tags") else 1             # geo-tagged items rank first
            ranked.append((ti, has_geo, label, it))
        ranked.sort(key=lambda x: (x[0], x[1]))
        if not ranked:
            return ("\n\n📰 **What's new to check** — no major property, infrastructure or "
                    "funding headlines in today's pull. See the News tab for the full feed.")

        # Diversify: lead with the top item from each theme so the reader sees the full
        # spread (funding / infrastructure / policy / jobs / market), not 5 of one bucket.
        by_theme: dict[int, list] = {}
        for ti, _hg, label, it in ranked:
            by_theme.setdefault(ti, []).append((label, it))
        picked, seen = [], set()
        for ti in sorted(by_theme):
            label, it = by_theme[ti][0]
            picked.append((label, it)); seen.add(id(it))
        for ti, _hg, label, it in ranked:                    # fill spare slots by priority
            if len(picked) >= 5:
                break
            if id(it) not in seen:
                picked.append((label, it)); seen.add(id(it))
        picked = picked[:5]

        lines = [f"\n\n📰 **What's new to check** — {len(ranked)} property, infrastructure "
                 f"&amp; funding headline(s) in today's pull; the ones most worth a look:"]
        for label, it in picked:
            geo = ", ".join(it.get("tags") or []) or "National"
            title = esc((it.get("title") or "").strip())[:110]
            lines.append(f"- {label} · **{geo}** — {title} _({esc(it.get('source',''))})_")
        lines.append("Open the **News** tab for the rest and the source links.")
        return "\n".join(lines)
    except Exception:
        return ""


def _write(today: str, body: str) -> str:
    body = body + _news_line() + _catalyst_freshness()
    md = f"# 🛰 Sersi — Daily Update · {today}\n\n{body}\n"
    LATEST.write_text(md)
    with open(CHANGELOG, "a") as fh:
        fh.write(md + "\n---\n\n")
    return md


if __name__ == "__main__":
    print(build_digest())
