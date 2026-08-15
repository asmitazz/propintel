"""PropIntel command-line interface.

Usage:
    python -m propintel init-db
    python -m propintel refresh-macro          # pull free ABS growth-driver data
    python -m propintel rank --level SA4        # score cities on fundamentals
    python -m propintel top --n 10              # show top cities
    python -m propintel status                  # DB + last-run summary

Domain (later-cycle, optional):
    python -m propintel domain-check            # verify which API packages are live
"""
from __future__ import annotations

import argparse
import sys

from tabulate import tabulate

from . import abs_client, ingest, ranking
from .config import settings
from .db import connect, finish_run, init_db, now_iso, start_run


def cmd_init_db(args) -> None:
    init_db()
    print(f"Initialised database at {settings.db_path}")


def cmd_refresh_macro(args) -> None:
    conn = connect()
    run_id = start_run(conn, "macro")
    rows = 0
    try:
        for level in ("SA4", "SA2"):
            print(f"Pulling ABS population ({level}) ...", flush=True)
            pop = abs_client.pull_population(region_type=level)
            ingest.seed_geography(conn, pop, region_type=level)
            rows += ingest.write_population(conn, pop)
            print(f"  {len(pop)} {level} regions, national pop "
                  f"{sum(d['population'] for d in pop.values()):,.0f}")
        finish_run(conn, run_id, rows, 0, "ok", "population + growth")
        print(f"Done. {rows} macro rows written.")
    except Exception as e:
        finish_run(conn, run_id, rows, 0, "error", str(e)[:200])
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()


def cmd_rank(args) -> None:
    conn = connect()
    run_id = start_run(conn, "rank")
    try:
        results = ranking.compute_scores(conn, level=args.level)
        ranking.save_scores(conn, results)
        finish_run(conn, run_id, len(results), 0, "ok", f"level={args.level}")
        print(f"Ranked {len(results)} {args.level} regions. "
              f"Run 'top' to view. Components used: "
              f"{', '.join(c for c in ranking.COMPONENTS if any(r['components'][c] is not None for r in results))}")
    except Exception as e:
        finish_run(conn, run_id, 0, 0, "error", str(e)[:200])
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()


def cmd_top(args) -> None:
    conn = connect()
    rows = conn.execute(
        """SELECT s.rank, g.name, g.state, s.score,
                  s.c_population_growth, s.c_net_migration, s.c_supply_pressure,
                  s.c_rental_yield, s.c_price_momentum, s.c_affordability,
                  (SELECT value FROM macro WHERE sa2_code=s.sa2_code AND metric='population'
                     ORDER BY observed_at DESC LIMIT 1) AS population,
                  (SELECT value FROM macro WHERE sa2_code=s.sa2_code AND metric='pop_growth_pct'
                     ORDER BY observed_at DESC LIMIT 1) AS pop_growth
           FROM suburb_scores s JOIN geography g ON g.region_code=s.sa2_code
           WHERE s.computed_at=(SELECT MAX(computed_at) FROM suburb_scores)
           ORDER BY s.rank LIMIT ?""",
        (args.n,),
    ).fetchall()
    conn.close()
    if not rows:
        print("No scores yet. Run 'refresh-macro' then 'rank'.")
        return
    table = []
    for r in rows:
        table.append([
            r["rank"], r["name"], r["state"],
            f"{r['score']:.1f}",
            f"{r['population']:,.0f}" if r["population"] else "-",
            f"{r['pop_growth']:.1f}%" if r["pop_growth"] is not None else "-",
            f"{r['c_rental_yield']:.2f}" if r["c_rental_yield"] is not None else "—",
        ])
    print(tabulate(
        table,
        headers=["#", "City / Region", "St", "Score", "Population", "Pop g/yr", "Yield*"],
        tablefmt="simple",
    ))
    print("\n* Yield component populates once a rent/price source is wired "
          "(currently ranked on available growth drivers only).")


def cmd_status(args) -> None:
    conn = connect()
    def count(t):
        return conn.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"]
    print("=== PropIntel status ===")
    for t in ("geography", "macro", "suburb_stats", "listings", "suburb_scores"):
        try:
            print(f"  {t:15} {count(t):>8,} rows")
        except Exception:
            print(f"  {t:15} (missing — run init-db)")
    print("\nLast runs:")
    for r in conn.execute("SELECT kind, started_at, status, rows_written, note FROM runs ORDER BY id DESC LIMIT 5"):
        print(f"  [{r['status']:>7}] {r['kind']:12} {r['started_at']}  rows={r['rows_written']}  {r['note'] or ''}")
    conn.close()


def cmd_domain_check(args) -> None:
    """Verify Domain auth and which API packages are attached to the project."""
    from .domain_client import DomainClient, PackageNotEnabled, DomainError
    try:
        c = DomainClient()
        c.token()
        print("Domain auth: OK (token acquired)")
    except DomainError as e:
        print(f"Domain auth FAILED: {e}")
        return
    checks = [
        ("Agents & Listings (listings search)",
         lambda: c.search_listings([{"state": "QLD"}], max_price=600000, page_size=1)),
        ("Properties & Locations (suburb performance)",
         lambda: c.suburb_performance("QLD", "Ipswich", "4305")),
        ("Properties & Locations (demographics)",
         lambda: c.demographics("QLD", "Ipswich", "4305")),
    ]
    for label, fn in checks:
        try:
            fn()
            print(f"  [LIVE]     {label}")
        except PackageNotEnabled:
            print(f"  [disabled] {label}  -> add the package in the Domain portal")
        except Exception as e:
            print(f"  [error]    {label}: {str(e)[:80]}")


def cmd_analyze(args) -> None:
    from . import analyze
    out = analyze.build_analysis()
    recs = out["records"]
    print(f"Analysed {len(recs)} suburbs -> {analyze.OUTPUT}")
    for asset in ("house", "townhouse"):
        top = sorted((r for r in recs if r.get(asset)), key=lambda r: r[asset]["rank"])[:5]
        print(f"  Top {asset}s:", ", ".join(f"{r['name']} ({r[asset]['score']})" for r in top))


def cmd_refresh_listings(args) -> None:
    from . import domain_listings
    print(f"Pulling live Domain listings for top {args.n} in-budget suburbs...")
    out = domain_listings.refresh_shortlist(top_n=args.n)
    got = sum(1 for v in out.values() if v.get("median_asking"))
    print(f"Done. Live median asking captured for {got}/{len(out)} suburbs -> {domain_listings.LIVE_PRICES}")
    print("Re-run 'report' to overlay live medians.")


def cmd_report(args) -> None:
    from . import report
    path = report.build()
    print(f"Wrote report: {path}")


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="propintel", description="Australian property investment intelligence")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init-db").set_defaults(func=cmd_init_db)
    sub.add_parser("refresh-macro").set_defaults(func=cmd_refresh_macro)

    pr = sub.add_parser("rank")
    pr.add_argument("--level", default="SA4", choices=["SA2", "SA4"])
    pr.set_defaults(func=cmd_rank)

    pt = sub.add_parser("top")
    pt.add_argument("--n", type=int, default=10)
    pt.add_argument("--level", default="SA4", choices=["SA2", "SA4"])
    pt.set_defaults(func=cmd_top)

    sub.add_parser("status").set_defaults(func=cmd_status)
    sub.add_parser("domain-check").set_defaults(func=cmd_domain_check)
    sub.add_parser("analyze").set_defaults(func=cmd_analyze)
    rl = sub.add_parser("refresh-listings")
    rl.add_argument("--n", type=int, default=25, help="top-N in-budget suburbs to price live")
    rl.set_defaults(func=cmd_refresh_listings)
    sub.add_parser("report").set_defaults(func=cmd_report)

    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
