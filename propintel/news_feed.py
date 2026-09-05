"""Live news & project announcements from public RSS feeds.

Pulls government + property/infrastructure feeds daily (machine-pullable, so it
runs in Sersi's local refresh — no Claude needed), filters for property-relevant
items, geo-tags them to states/suburbs, dedupes across runs and keeps a rolling
window. Surfaced in the report's News tab; Sersi's digest reports new items.

Boundary (deliberate): feeds bring *headlines*; deciding a project's status/jobs
stays curated in catalysts.json. A headline "govt announces X" ≠ X is funded.

Land releases (the supply side) have no feeds — state agencies publish HTML
pages — so they're a future piece, not a brittle scraper in the daily job.
"""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime

from curl_cffi import requests as cf

from .config import ROOT

NEWS_FILE = ROOT / "data" / "news_feed.json"
MAX_ITEMS = 90

FEEDS = [
    ("PM & Cabinet", "https://www.pm.gov.au/rss.xml"),
    ("Treasury", "https://ministers.treasury.gov.au/rss.xml"),
    ("Infrastructure Magazine", "https://www.infrastructuremagazine.com.au/feed/"),
    ("RenewEconomy", "https://reneweconomy.com.au/feed/"),
    ("Sourceable", "https://sourceable.net/feed/"),
    ("realestate.com.au News", "https://www.realestate.com.au/news/feed/"),
]

# an item is kept only if it looks property/infrastructure/jobs-relevant
RELEVANCE = re.compile(
    r"\b(infrastructur|invest|billion|million|fund|grant|rezon|planning|land release|"
    r"housing|dwelling|home build|rail|metro|highway|road|hospital|health|universit|campus|"
    r"defence|aukus|hydrogen|renewable|wind|solar|battery|energy zone|airport|port|precinct|"
    r"project|jobs|employ|migrat|population|suburb|property|price|rent|vacanc|development|stamp duty|"
    r"first home|interest rate|construction)\b", re.I)

# PM & Cabinet and Treasury are all-of-government feeds — they carry sport, foreign
# affairs, ceremonies and arts alongside the occasional real housing/infra announcement.
# RELEVANCE alone lets the junk through (a Pacific-sport presser that says "funding" and
# "jobs"), so items from these BROAD feeds must additionally clear a domestic-property gate:
# a concrete property/infrastructure term, OR an Australian location + a relevance hit.
_BROAD_FEEDS = {"PM & Cabinet", "Treasury"}
_NAV_TITLES = {"home", "news", "media", "media releases", "media centre", "media center",
               "latest news", "newsroom", "media release", "press releases", "speeches"}
_STRICT = re.compile(
    r"\b(housing|dwelling\w*|homes?|new homes|home build\w*|social housing|public housing|"
    r"build[- ]to[- ]rent|rezon\w*|planning reform|land release|stamp duty|first[- ]home|"
    r"dwelling approvals?|building approvals?|subdivision|greenfield|"
    r"rail line|light rail|metro|motorway|freeway|highway|hospital|airport|seaport|precinct|"
    r"desalinat\w*|renewable energy zone|\bREZ\b|hydrogen (hub|plant|precinct)|pumped hydro|"
    r"defence precinct|road upgrade|interchange|infrastructure (pipeline|investment|fund|package|project|program)|"
    r"construction (sector|industry|pipeline)|property market|rental market|median (house|price))\b", re.I)

# Low-signal items to drop even if they mention a relevant keyword — pure transcripts,
# press conferences, interviews and photo-ops carry no fundamentals signal to track.
NOISE = re.compile(
    r"^\s*(press conference|doorstop|door stop|transcript|radio interview|"
    r"television interview|tv interview|interview with|press statement|"
    r"joint (press|doorstop|statement)|readout|remarks|op-?ed|opinion[: ]|speech[: ]|"
    r"address to|photo|passing of|obituary|condolence|death of)\b", re.I)

STATES = {
    "NSW": ["nsw", "new south wales", "sydney", "newcastle", "hunter", "wollongong", "parramatta", "dubbo", "central-west orana"],
    "VIC": ["vic", "victoria", "victorian", "melbourne", "geelong", "ballarat", "bendigo", "gippsland", "latrobe", "suburban rail loop"],
    "QLD": ["qld", "queensland", "brisbane", "townsville", "gold coast", "sunshine coast", "cairns", "gladstone", "ipswich", "logan", "toowoomba", "rockhampton", "mackay"],
    "SA": ["south australia", "adelaide", "whyalla", "osborne", "port augusta"],
    "WA": ["western australia", "perth", "pilbara", "kwinana", "henderson", "port hedland", "karratha", "bunbury"],
    "TAS": ["tasmania", "tasmanian", "hobart", "launceston", "burnie", "devonport", "marinus"],
    "NT": ["northern territory", "darwin", "palmerston", "middle arm"],
    "ACT": ["canberra"],
}
# word-boundary matchers so "SA"/"NT" don't match inside 'passage' / 'investment'
_STATE_RE = {st: re.compile(r"\b(" + "|".join(re.escape(k) for k in kws) + r")\b", re.I)
             for st, kws in STATES.items()}


def _text(el):
    return (el.text or "").strip() if el is not None else ""


def _find(item, *suffixes):
    for ch in item.iter():
        tag = ch.tag.split("}")[-1].lower()
        if tag in suffixes:
            return ch
    return None


def _parse_feed(source: str, xml_text: str) -> list[dict]:
    out = []
    try:
        root = ET.fromstring(xml_text)
    except Exception:
        return out
    nodes = [n for n in root.iter() if n.tag.split("}")[-1].lower() in ("item", "entry")]
    for it in nodes:
        title = _text(_find(it, "title"))
        link = ""
        for ch in it.iter():
            if ch.tag.split("}")[-1].lower() == "link":
                link = (ch.get("href") or ch.text or "").strip()
                if link:
                    break
        desc = _text(_find(it, "description", "summary"))
        pub = _text(_find(it, "pubdate", "published", "updated", "date"))
        if title and link:
            out.append({"source": source, "title": re.sub(r"<[^>]+>", "", title),
                        "link": link, "desc": re.sub(r"<[^>]+>", "", desc)[:400], "pub": pub})
    return out


def _geo_tags(text: str) -> list[str]:
    return [st for st, rx in _STATE_RE.items() if rx.search(text)]


def _keep(source: str, title: str, desc: str) -> bool:
    """Decide whether a feed item is genuinely property/infrastructure-relevant.

    Broad all-of-government feeds (PM & Cabinet, Treasury) carry sport / foreign
    affairs / ceremonies that trip the loose RELEVANCE keywords, so they must clear a
    stricter gate: a concrete domestic property/infra term, OR an Australian location
    plus a relevance hit (which catches funding announcements like the Hunter's future)."""
    t = (title or "").strip()
    if len(t) < 12 or t.lower() in _NAV_TITLES:   # feed nav artifacts: "Home", "Media", "News"
        return False
    if NOISE.search(title):                       # transcripts / pressers / remarks / photo-ops
        return False
    if source in _BROAD_FEEDS:
        # Judge broad government feeds on the TITLE only — their press-release descriptions
        # are boilerplate ("the Government is investing … in <state> … homes …") that trips
        # the loose keywords on almost anything. The title states the actual subject.
        return bool(_STRICT.search(title) or (_geo_tags(title) and RELEVANCE.search(title)))
    return bool(RELEVANCE.search(f"{title} {desc}"))


def fetch_all() -> dict:
    """Pull all feeds, filter + geo-tag, merge with existing (dedupe), keep newest."""
    prev = {}
    if NEWS_FILE.exists():
        try:
            prev = {i["link"]: i for i in json.loads(NEWS_FILE.read_text()).get("items", [])}
        except Exception:
            prev = {}
    today = str(date.today())
    seen = dict(prev)
    new_count = 0
    for source, url in FEEDS:
        try:
            r = cf.get(url, impersonate="chrome", timeout=20)
            if r.status_code != 200:
                continue
            for item in _parse_feed(source, r.text):
                if not _keep(source, item["title"], item["desc"]):
                    continue
                if item["link"] in seen:
                    continue
                item["tags"] = _geo_tags(f'{item["title"]} {item["desc"]}')
                item["first_seen"] = today
                seen[item["link"]] = item
                new_count += 1
        except Exception:
            continue

    # re-apply the gate to anything stored before this filter existed (purges old noise,
    # e.g. PM & Cabinet foreign-affairs/sport items that predate the broad-feed gate)
    kept = [i for i in seen.values()
            if _keep(i.get("source", ""), i.get("title", ""), i.get("desc", ""))]
    items = sorted(kept, key=lambda i: i.get("first_seen", ""), reverse=True)[:MAX_ITEMS]
    NEWS_FILE.write_text(json.dumps({"generated": today, "new_today": new_count, "items": items}, indent=1))
    return {"new_today": new_count, "total": len(items)}


if __name__ == "__main__":
    res = fetch_all()
    print(f"News: {res['new_today']} new, {res['total']} kept -> {NEWS_FILE}")
