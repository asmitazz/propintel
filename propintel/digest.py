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
from datetime import date

from .config import ROOT

CURR = ROOT / "data" / "suburb_analysis.json"
PREV = ROOT / "data" / "suburb_analysis.prev.json"
LATEST = ROOT / "data" / "latest_update.md"
CHANGELOG = ROOT / "data" / "changelog.md"

# The live snapshot the last successful run published. In the cloud (GitHub Actions)
# the workspace is wiped between runs, so a local prev never survives — without this
# the digest would say "first update — baseline captured" every single day. We pull
# the currently-deployed snapshot (i.e. yesterday's) so day-to-day change detection
# actually works. Locally, refresh.sh writes PREV itself, so this never fires.
LIVE_SNAPSHOT_URL = "https://asmitazz.github.io/propintel/suburb_analysis.json"


def _ensure_prev() -> None:
    """If no local previous snapshot exists, fetch the currently-deployed one."""
    if PREV.exists():
        return
    try:
        import urllib.request
        req = urllib.request.Request(LIVE_SNAPSHOT_URL, headers={"User-Agent": "sersi-digest"})
        raw = urllib.request.urlopen(req, timeout=30).read()
        json.loads(raw)                      # sanity-check it's the snapshot JSON
        PREV.parent.mkdir(parents=True, exist_ok=True)
        PREV.write_bytes(raw)
    except Exception:
        pass   # first deploy ever, or the file isn't live yet — fall back to baseline msg


def _by_code(path):
    return {s["code"]: s for s in json.loads(path.read_text())["suburbs"]}


def _topn(m, asset, n=10):
    el = [s for s in m.values() if s.get(asset)]
    el.sort(key=lambda s: s[asset]["rank"])
    return el[:n]


def build_digest() -> str:
    today = str(date.today())
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

    # Did the underlying ABS data actually change? (prices/scores identical => no release)
    def sig(s):
        h = s.get("house") or {}
        return (round(h.get("price_2024", 0)), (h.get("score")), s.get("hotspot"), s.get("gentrify_flag"))
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


def _news_line() -> str:
    """Report how many relevant news items Sersi pulled today."""
    p = ROOT / "data" / "news_feed.json"
    if not p.exists():
        return ""
    try:
        d = json.loads(p.read_text())
        n = d.get("new_today", 0)
        if n:
            return f"\n\n📰 **{n} new property/infrastructure headlines** pulled today — see the News tab."
    except Exception:
        pass
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
