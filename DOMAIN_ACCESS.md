# Getting accurate current suburb prices — Domain API access

## Bottom line (confirmed from the portal)

**Nothing on Domain's API is free anymore for our purpose.** In the developer
portal, both products show pricing gates:

- **Agents & Listings** (residential *search*) → **"Contact for Price · Request
  access"** — paid/quote.
- **Properties & Locations** (suburb median/rent/yield) → **Business plan** — paid.
- **Listings Management** (what you have) → manage your *own* listings, sandbox
  only — useless for market prices.

So current prices from Domain = **paid, quote-based**. There is no free tier.
The rest of this file (how to request access, draft message) still applies if
you decide to pay; otherwise use the free fallback at the bottom.

## The reality (corrected)

Your ABS-based prices read low because ABS medians are a **2024 sold-transfer
median for the whole SA2 area**, not a current named-suburb figure. To fix the
*absolute* numbers you need a live source. The cleanest is Domain — **but the
suburb median data is NOT on the free tier.** Per Domain's current docs:

- **Free tier** (what your key has): listing *search* (Agents & Listings) — live
  for-sale listings, i.e. current **asking** prices. Your project currently has
  no packages attached, so even this returns `403 Operation not permitted on
  project` until packages are added.
- **Business plan (paid):** the **suburb performance statistics** (median sold
  house/unit price, median rent, gross yield), **demographics**, and **property
  price estimates** — exactly the current medians you want.

So: median suburb prices = **Domain Business plan** (or another paid source like
Cotality/CoreLogic). This is the honest answer — it isn't free.

## Important: "Listings Management" is the WRONG product

Domain has separate products, and the names are confusing:

- **Listings Management** (you have this, sandbox) — lets an **agency create /
  update its *own* listings** and manage leads. It's write-side; the sandbox only
  holds *test* data. **It cannot search the market**, so it can't give suburb
  medians.
- **Residential listings *Search*** (what we need) — read/search *all* for-sale
  listings market-wide: `POST /v1/listings/residential/_search`. Part of the
  **"Agents & Listings" / Search** product on **production**.
- **Properties & Locations (Business plan)** — ready-made suburb median/rent/yield
  stats (paid).

Tested outcome: even with every scope granted, your **project** returns
`403 Operation not permitted on project` for search, agencies AND suburb stats —
i.e. your project has **no read product attached**, only Listings Management.

## What to do

1. Go to **https://developer.domain.com.au** → sign in → **Projects**.
2. Open your project and check **Packages/Plan**. Add **Properties & Locations**
   and **Agents & Listings** if not present.
3. The suburb-performance endpoints need the **Business plan** — there's usually
   no self-serve upgrade; you **contact Domain** and describe your use case.
   Look for "Contact us" / "Talk to sales" / API support on the developer portal,
   or email the Domain API team.

## Draft message to Domain (copy/paste, edit as needed)

> Subject: Business API access — suburb performance & price data
>
> Hi Domain API team,
>
> I'm building a personal property-investment analytics tool (and exploring
> turning it into a small business/service) that ranks Australian suburbs on
> capital-growth fundamentals. I currently use free ABS data, but I need
> **current suburb median prices, median rents, gross yields and demographics**
> to make the analysis accurate and actionable.
>
> Could you tell me:
> 1. Which plan/package gives access to `suburbPerformanceStatistics`,
>    `demographics` and property price estimates (I understand this is the
>    Business plan)?
> 2. Pricing and any volume limits (I'd start at a low call volume — a few
>    hundred suburbs refreshed weekly).
> 3. How to enable it on my existing project (Client ID available on request).
>
> Use case: read-only analytics for my own investment decisions; potentially a
> subscription tool for other investors later. Happy to share more detail.
>
> Thanks!

## Meanwhile (no paid source)

- Every suburb in the tool's **lookup** has a one-click **"verify live median →
  realestate.com.au"** link — use it to confirm the real number before acting.
- The tool's **fundamentals ranking** (gentrification, ripple, supply-demand,
  migration, projections, jobs) does **not** depend on absolute price accuracy —
  it's the reliable part. Prices are only used for the budget filter.
- If you enable just the **free Domain listings** package, I can add an adapter
  that computes a rough current median from live for-sale listings per suburb
  (asking prices) — less precise than Business-plan medians but free and current.
