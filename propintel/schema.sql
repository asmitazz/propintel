-- PropIntel database schema.
--
-- Two design rules that are expensive to change later:
--   1. Geography is keyed on ABS ASGS codes (SA2), not suburb-name strings.
--      Suburb names repeat across states and don't map cleanly to ABS boundaries.
--   2. Listings and stats are SNAPSHOTTED per observation, never overwritten.
--      Days-on-market and price-reduction history are the strongest value signals
--      and only exist if we keep every observation.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- Canonical geography. Holds regions at multiple ABS levels (SA2 suburbs and
-- SA4 "cities"/large regions). Keyed on the ABS ASGS 2021 code.
CREATE TABLE IF NOT EXISTS geography (
    region_code  TEXT PRIMARY KEY,      -- ABS ASGS 2021 code (9-digit SA2, 3-digit SA4, ...)
    region_type  TEXT NOT NULL DEFAULT 'SA2',   -- SA2 | SA3 | SA4
    name         TEXT NOT NULL,
    state        TEXT NOT NULL,         -- derived from first digit of the code
    sa4_code     TEXT,                  -- parent city/region (first 3 digits of an SA2 code)
    postcode     TEXT,                  -- best-effort, for Domain joins
    updated_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_geography_type ON geography(region_type);

-- ABS macro indicators, long format. One row per (geo, metric, period, observation).
-- sa2_code here is a generic region code (may be an SA2 or an SA4).
CREATE TABLE IF NOT EXISTS macro (
    sa2_code     TEXT NOT NULL,
    metric       TEXT NOT NULL,         -- population | pop_growth_pct | net_migration | building_approvals | median_income
    period       TEXT NOT NULL,         -- e.g. '2024'
    value        REAL,
    source       TEXT NOT NULL,         -- e.g. 'ABS:ERP_ASGS2021'
    observed_at  TEXT NOT NULL,
    PRIMARY KEY (sa2_code, metric, period, source),
    FOREIGN KEY (sa2_code) REFERENCES geography(region_code)
);

-- Domain suburb performance snapshots (medians, growth, yield, days-on-market).
CREATE TABLE IF NOT EXISTS suburb_stats (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    sa2_code          TEXT,             -- matched from suburb+state (nullable if unmatched)
    suburb            TEXT NOT NULL,
    state             TEXT NOT NULL,
    postcode          TEXT,
    property_category TEXT NOT NULL,    -- House | Unit | Townhouse ...
    bedrooms          INTEGER,
    period            TEXT,             -- reporting period label
    median_price      REAL,
    num_sold          INTEGER,
    days_on_market    REAL,
    growth_pct        REAL,             -- period-on-period median growth
    rental_median     REAL,
    gross_yield_pct   REAL,
    source            TEXT NOT NULL DEFAULT 'Domain:suburbPerformance',
    observed_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_suburb_stats_geo ON suburb_stats(sa2_code, property_category);

-- Domain demographics snapshots.
CREATE TABLE IF NOT EXISTS demographics (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    sa2_code      TEXT,
    suburb        TEXT NOT NULL,
    state         TEXT NOT NULL,
    postcode      TEXT,
    metric        TEXT NOT NULL,        -- Population | MedianAge | Owner/Renter split ...
    value         TEXT,
    source        TEXT NOT NULL DEFAULT 'Domain:demographics',
    observed_at   TEXT NOT NULL
);

-- Live listings. SNAPSHOT semantics: one row per (listing_id, observed_at).
-- Never UPDATE a listing row; always INSERT a fresh observation.
CREATE TABLE IF NOT EXISTS listings (
    obs_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id     TEXT NOT NULL,
    sa2_code       TEXT,
    address        TEXT,
    suburb         TEXT,
    state          TEXT,
    postcode       TEXT,
    property_type  TEXT,
    price_display  TEXT,                -- raw display string ("Offers over $549k")
    price_numeric  REAL,               -- parsed guide price
    bedrooms       INTEGER,
    bathrooms      INTEGER,
    parking        INTEGER,
    land_area      REAL,
    agency         TEXT,
    listed_date    TEXT,
    listing_url    TEXT,
    observed_at    TEXT NOT NULL,
    raw_json       TEXT                 -- full API payload for future re-parsing
);
CREATE INDEX IF NOT EXISTS ix_listings_id ON listings(listing_id, observed_at);
CREATE INDEX IF NOT EXISTS ix_listings_geo ON listings(sa2_code);

-- Derived price history (only rows where the guide price changed).
CREATE TABLE IF NOT EXISTS listing_price_history (
    listing_id    TEXT NOT NULL,
    price_numeric REAL,
    observed_at   TEXT NOT NULL,
    PRIMARY KEY (listing_id, observed_at)
);

-- Ranking output. One row per SA2 per scoring run.
CREATE TABLE IF NOT EXISTS suburb_scores (
    sa2_code            TEXT NOT NULL,
    computed_at         TEXT NOT NULL,
    score               REAL,           -- 0..100 composite
    rank                INTEGER,
    c_population_growth REAL,
    c_net_migration     REAL,
    c_price_momentum    REAL,
    c_rental_yield      REAL,
    c_affordability     REAL,
    c_supply_pressure   REAL,
    detail_json         TEXT,           -- raw component inputs for transparency
    PRIMARY KEY (sa2_code, computed_at),
    FOREIGN KEY (sa2_code) REFERENCES geography(region_code)
);

-- Audit log of every data-refresh run.
CREATE TABLE IF NOT EXISTS runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    kind         TEXT NOT NULL,         -- macro | suburb_stats | listings | rank
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    rows_written INTEGER DEFAULT 0,
    api_calls    INTEGER DEFAULT 0,
    status       TEXT,                  -- ok | partial | error
    note         TEXT
);
