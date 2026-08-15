# PropIntel — Australian Property Investment Intelligence

A local, free-data engine that ranks Australian **cities and suburbs on the
fundamentals that drive capital growth**, then (later in the cycle) pulls live
listings for the top shortlist so you can act.

Built around one principle: **rank on fundamentals first, look at listings last.**
Listings tell you what's *buyable*; population, migration, jobs, supply and yield
tell you *where to buy*. The expensive, fragile part (listing portals behind bot
protection) is deferred until you've already decided where to look.

> **Not financial advice.** This is analytical tooling to support your own
> research. It does not constitute personal financial or investment advice.

---

## What it does today

- **100% free public data, Domain-free.** Pulls ABS "Data by Region" +
  population — median house & townhouse/villa price (state valuers-general),
  median rent, net internal + overseas migration, dwelling approvals & stock,
  income, unemployment, tenure — for every SA2 suburb in Australia.
- **Real gross yield, Domain-free** (median rent ÷ median price), plus a stated
  market-rent uplift for the ~4.5%+ target.
- **Composite capital-growth score** per suburb from 9 macro signals (incl. an
  **industry-diversity / single-industry-risk** index), with a **cycle flag**
  (Early / Mid / Late) so you find growth *before* price runs.
- **Two strategy modes, scored separately** — *Houses* (land / capital-growth)
  and *Townhouses & Villas* (lower entry, higher yield, far more sub-$600k stock,
  incl. inner-city) — each with price-band tabs (<$400k · $400–500k · $500–600k).
- Prices are the 2024 ABS vintage **nowcast to ~present** (shown with the raw ’24
  figure); houses vs units kept **separate** with a house-to-unit variance ratio.
- A **government-policy layer** (Help to Buy, First Home Guarantee, NSW TOD, VIC
  activity centres, QLD FHB duty) and a structural **catalyst map**.
- Local **SQLite** DB with full history; every refresh snapshotted.
- A **Domain API client** is included but **optional/dormant** — the model does
  not depend on it.

## Investment thesis (edit `config.yaml`)

- Property types: House, Townhouse, Villa, Duplex, Semi-detached
- Price ceiling: **$600,000**
- Target: **cities with 4.5%+ rental yield** (current or achievable in 1–2 years)
  — *yield scoring activates once a rent/price source is wired; see Roadmap.*

---

## Setup

```bash
pip install -r requirements.txt
```

Secrets live in `.env` (gitignored, `chmod 600`) — already created with your
Domain API credentials. Nothing secret is ever written to the database.

## Usage

**Flagship (Domain-free, pure macro fundamentals):**

```bash
python -m propintel init-db     # create the database
python -m propintel analyze     # pull all ABS data + score every suburb (~40s)
python -m propintel report      # build reports/emerging-growth-report.html
```

`analyze` pulls ABS "Data by Region" (median house/attached price, median rent,
net migration, dwelling approvals & stock, income, unemployment, tenure) + ERP
population, computes a composite capital-growth score per SA2 suburb, and writes
`data/suburb_analysis.json`. `report` renders the tabbed HTML (price-band tabs +
government-policy layer + catalyst map).

**City-level view & utilities:**

```bash
python -m propintel refresh-macro    # ABS population by SA4/SA2
python -m propintel rank --level SA4 # score cities
python -m propintel top --n 10       # top cities in the terminal
python -m propintel status           # DB + last-run summary
```

## Sersi — daily auto-refresh + change digest (macOS)

**Sersi** is the agent that keeps this current. A LaunchAgent
(`com.sersi.dailyupdate`) runs `refresh.sh` **every day at 7:00 AM**:
snapshots yesterday's data → pulls fresh ABS data → **writes a plain-English
"what changed" digest** → rebuilds the report. Already installed and loaded.

**You don't have to browse the tabs** — Sersi's update appears:
- at the **top of the Summary tab** in the report, and
- in **`data/latest_update.md`** (plus a running **`data/changelog.md`**).

Most days it says *"No material changes"* (ABS only re-releases a few times a
year); when data moves, it lists new Top-10 entrants, new 🔥 hotspots, new
gentrifiers and the biggest score moves.

```bash
./refresh.sh                       # run a refresh manually any time
tail -f data/refresh.log           # watch progress / history
launchctl list | grep propintel    # confirm it's scheduled

# change the time: edit ~/Library/LaunchAgents/com.propintel.refresh.plist
#   (StartCalendarInterval Hour/Minute), then reload:
launchctl unload ~/Library/LaunchAgents/com.propintel.refresh.plist
launchctl load  -w ~/Library/LaunchAgents/com.propintel.refresh.plist

# stop the daily refresh entirely:
launchctl unload ~/Library/LaunchAgents/com.propintel.refresh.plist
```

Notes: ABS "Data by Region" only updates a few times a year, so the numbers
won't change daily — the schedule keeps the report fresh and picks up new ABS
releases automatically without you remembering to run it. The Mac must be awake
at the scheduled time (missed runs fire when it next wakes).

## Government funding & jobs pipeline (`data/catalysts.json`)

The forward demand engine — **committed government + industry funding and the
jobs they'll create over 5–10 years** (AUKUS, Olympics, Suburban Rail Loop,
renewable energy zones, Henderson defence, hydrogen/green-metals, Inland Rail,
Marinus Link, etc.), geo-tagged to suburbs and shown in the **News**,
**Catalysts** and **10yr Outlook** tabs.

There is **no clean API** for this (Infrastructure Australia's list is a
transport-only PDF), so it's a **curated research dataset**. Each project carries
a **status** (Active / Under construction / Planned / Delayed / Uncertain /
Cancelled) — e.g. Whyalla's green-hydrogen plant shows *Uncertain* after its 2025
cancellation, so stale optimism can't mislead you.

Maintenance: refresh at **budget times** (May federal, ~June state) or when a
project's status changes. **Sersi flags it in the daily update once the data is
&gt;90 days old** — then ask me to re-research and I'll update the file.

## Current prices

Prices are ABS SA2-area sold medians (2024) escalated to ~now — good for
**ranking**, but 10–20% off any single suburb's live figure. Every suburb in the
lookup has a one-click **realestate.com.au** link to verify the live median.
Accurate automated current prices need a paid feed (Domain *Business* /
CoreLogic) — the listings adapter (`refresh-listings`) is built and switches on
once you have API access. See `DOMAIN_ACCESS.md`.

---

## How the score works

Each fundamental is normalised **0–1 by percentile rank** across all regions
(outlier-resistant — greenfield suburbs posting 40%/yr growth won't distort the
scale), then combined with the weights in `config.yaml`:

| Component            | Weight | Source                        | Status |
|----------------------|:------:|-------------------------------|--------|
| Population growth    | 0.25   | ABS ERP by SA2/SA4            | ✅ live |
| Net migration        | 0.15   | ABS regional migration        | 🔜 wiring |
| Price momentum       | 0.15   | rent/price source             | 🔜 needs source |
| Rental yield         | 0.15   | rent/price source             | 🔜 needs source |
| Affordability        | 0.15   | median price vs income        | 🔜 needs source |
| Supply pressure      | 0.15   | ABS building approvals (inv.) | 🔜 wiring |

**Missing components redistribute their weight** across the ones that have data,
so the ranking is sensible today and gets sharper as sources come online.

---

## Data model (SQLite)

Two rules that are expensive to reverse, so they're baked in from day one:

1. **Geography is keyed on ABS ASGS codes** (SA2 suburbs, SA4 cities), not
   suburb-name strings. Names repeat across states and don't map cleanly to
   boundaries.
2. **Listings & stats are snapshotted per observation, never overwritten.**
   Days-on-market and price-reduction history are among the strongest value
   signals and only exist if every observation is kept.

Tables: `geography`, `macro`, `suburb_stats`, `demographics`, `listings`,
`listing_price_history`, `suburb_scores`, `runs`.

---

## Enabling Domain (later-cycle)

Your Domain credentials authenticate, but the **project has no API packages
attached** yet, so data endpoints return `403 Operation not permitted on project`.
To activate (all free tiers):

1. Go to <https://developer.domain.com.au> → your project.
2. Add packages:
   - **Properties & Locations** → suburb median price, rent, yield, demographics.
   - **Agents & Listings** → residential listings search (for the shortlist pull).
3. Re-run `python -m propintel domain-check` — endpoints flip to `[LIVE]`.

The Domain client (`propintel/domain_client.py`) already targets the correct
routes and will work the moment the packages are attached.

---

## Decisions locked

- **"City" = ABS Significant Urban Area (SUA)** — named cities/towns >10k people
  (Toowoomba, Geelong, Bendigo, …). Since annual population *growth* only exists
  at ASGS levels (SA2/SA4), each SUA's growth is built by **aggregating its
  constituent SA2 population series**. SUA also unlocks free Census medians
  (weekly rent G40, income G02) as an interim yield input.
- **Yield source = Domain "Properties & Locations" package** (free, official API).
  Gives median price, median rent and yield per suburb in one call. Requires
  enabling the package on your Domain project (see above) — auth already works.

## Roadmap

- [ ] **SUA cities**: build SA2→SUA aggregation for population + growth; label
      the top-10 with real city names.
- [ ] **Yield**: pull Domain Properties & Locations (median price + rent + yield)
      per suburb; roll up to SUA; apply the **4.5%+** filter. Interim: Census G40 rent.
- [ ] Yield **projection** (current yield + rent-vs-price momentum → 1–2yr outlook).
- [ ] Wire ABS net-migration and building-approvals dataflows (demand + supply).
- [ ] Add income (ABS/Census) for affordability; jobs growth; infrastructure pipeline.
- [ ] Shortlist → pull live Domain listings for top-N cities under $600k.
- [ ] Scheduled weekly refresh + change alerts.
```
