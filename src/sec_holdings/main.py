"""
main.py
-------
CLI entry point. Orchestrates the full pipeline:

    1. Fetch holdings from SEC EDGAR (N-PORT or 13F) via edgartools
    2. Persist holdings to SQLite, grouped by filing period
    3. Fetch daily prices via yfinance for all resolved tickers
    4. Persist daily prices to SQLite
    5. Load rolling derivatives overlay from YAML
    6. Persist contract legs to SQLite

Usage
-----
    # using .env
    python -m sec_holdings.main

    # CLI overrides
    python -m sec_holdings.main --cik 0001336528 --source 13f
    python -m sec_holdings.main --cik 0000884394 --source nport --years 3
    python -m sec_holdings.main --overlay overlays/example_pershing.yaml

    # check what is in the DB without fetching
    python -m sec_holdings.main --info

Skipping
--------
Holdings for a filing period already in the DB are skipped.
Prices already in the DB are skipped (INSERT OR IGNORE).
Derivative contracts already in the DB are skipped (INSERT OR IGNORE).
Safe to re-run at any time.
"""

from __future__ import annotations

import argparse
import logging
import sys
from itertools import groupby
from pathlib import Path
from datetime import date, timedelta

from sec_holdings.config import Config
from sec_holdings.database import Database
from sec_holdings.derivatives import DerivativesLoader
from sec_holdings.fetcher import Fetcher
from sec_holdings.prices import PriceFetcher


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Fetch SEC EDGAR holdings and enrich with daily prices",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--cik", help="SEC CIK number (overrides SEC_HOLDINGS_CIK)")
    p.add_argument(
        "--source",
        choices=["nport", "13f"],
        help="Filing type: 'nport' for mutual funds, '13f' for hedge funds",
    )
    p.add_argument("--years", type=int, help="Years of history to fetch (default 5)")
    p.add_argument("--db", dest="db_path", help="SQLite output path")
    p.add_argument("--overlay", dest="overlay_path", help="Path to YAML derivatives overlay")
    p.add_argument(
        "--info",
        action="store_true",
        help="Print DB summary and exit without fetching",
    )
    p.add_argument("-v", "--verbose", action="store_true", help="Debug logging")
    return p.parse_args()


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    for noisy in ("urllib3", "yfinance", "peewee", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _print_info(db: Database, config: Config) -> None:
    """Print a summary of what is currently in the database."""
    periods = db.get_all_periods_for_cik(config.cik)
    if not periods:
        print("Database is empty.")
        return

    print(f"\n{'='*56}")
    print(f"  Reporting periods in DB: {len(periods)}")
    print(f"  Earliest : {periods[0]}")
    print(f"  Latest   : {periods[-1]}")

    latest = db.get_holdings_for_period(periods[-1])
    if latest:
        total = sum(h["value_usd"] for h in latest if h["value_usd"])
        print(f"\n  Latest period ({periods[-1]})")
        print(f"  Positions : {len(latest)}")
        print(f"  Total value : ${total:,.0f}")
        print(f"\n  Top 5 positions:")
        for h in latest[:5]:
            ticker = h.get("ticker") or "—"
            pct = h.get("pct_val") or 0
            name = (h.get("name") or "—")[:35]
            print(f"    {name:<35} {ticker:<8} {pct:.2f}%")


    contracts = db.get_all_contracts()
    if contracts:
        print(f"\n  Derivative contracts : {len(contracts)}")
        open_c = [c for c in contracts if c["status"] == "open"]
        print(f"  Currently open       : {len(open_c)}")

    print(f"{'='*56}\n")


def _group_holdings_by_filing(holdings: list[dict]) -> dict[str, list[dict]]:
    """
    Group a flat list of holdings by accession number.

    Returns
    -------
    dict[str, list[dict]]
        Keys are accession numbers, values are lists of holding dicts
        for that filing.
    """
    grouped: dict[str, list[dict]] = {}
    for h in holdings:
        acc = h["accession"]
        grouped.setdefault(acc, []).append(h)
    return grouped


def _filing_meta_from_holdings(holdings: list[dict], cik: str) -> dict:
    first = holdings[0]
    return {
        "source": first["source"],
        "cik": cik,
        "accession": first["accession"],
        "form_type": "N-PORT" if first["source"] == "nport" else "13F-HR",
        "filing_date": first["filing_date"],
        "period_of_report": first["period"],
        "reg_name": first.get("reg_name"),
        "series_name": None,
        "total_assets": first.get("total_assets"),
        "net_assets": first.get("net_assets"),
    }


def run() -> None:
    args = _parse_args()
    _setup_logging(args.verbose)
    log = logging.getLogger("sec_holdings.main")

    try:
        config = Config.from_env(
            **{k: v for k, v in vars(args).items()
               if v is not None and k not in ("verbose", "info")}
        )
    except KeyError:
        print(
            "ERROR: No CIK provided. Use --cik or set SEC_HOLDINGS_CIK in .env",
            file=sys.stderr,
        )
        sys.exit(1)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    with Database(config.db_path) as db:

        if args.info:
            _print_info(db, config)
            return

        # ------------------------------------------------------------ #
        # Step 1: fetch holdings                                         #
        # ------------------------------------------------------------ #
        log.info(
            "Fetching %s filings for CIK %s (%d years)",
            config.source.upper(), config.cik, config.years,
        )
        fetcher = Fetcher(config)
        all_holdings = fetcher.fetch()

        if not all_holdings:
            log.warning("No holdings returned. Check CIK and source type.")
            return

        log.info("Fetched %d total position rows across all filings", len(all_holdings))

        # ------------------------------------------------------------ #
        # Step 2: persist holdings grouped by filing                    #
        # ------------------------------------------------------------ #
        grouped = _group_holdings_by_filing(all_holdings)
        new_filings = 0
        skipped_filings = 0

        for accession, filing_holdings in grouped.items():
            if db.filing_exists(accession):
                skipped_filings += 1
                continue

            meta = _filing_meta_from_holdings(filing_holdings, config.cik)
            filing_id = db.insert_filing(meta)
            db.insert_holdings(filing_holdings, filing_id)

            log.info(
                "Persisted %s %s: %d positions (period %s)",
                config.source.upper(), accession,
                len(filing_holdings), meta["period_of_report"],
            )
            new_filings += 1

        log.info(
            "Holdings: %d new filings persisted, %d already in DB",
            new_filings, skipped_filings,
        )

        # ------------------------------------------------------------ #
        # Step 3: fetch and persist daily prices (incremental)         #
        # ------------------------------------------------------------ #
        price_fetcher = PriceFetcher(config)

        all_tickers = list({
            h["ticker"] for h in all_holdings if h.get("ticker")
        })

        as_of = (date.today() - timedelta(days=1)).isoformat()
        stale = db.get_stale_tickers(all_tickers, as_of)

        if stale:
            log.info(
                "%d tickers need price update (%d already fresh)",
                len(stale), len(all_tickers) - len(stale),
            )
            prices, ticker_status = price_fetcher.fetch_incremental(stale)
            if prices:
                db.insert_prices(prices)
            db.update_pricing_status(ticker_status)
        else:
            log.info("All prices fresh -- skipping yfinance")
            prices = []

        # Set excluded_no_ticker for positions with no ticker
        db.update_pricing_status({})

        
        # ------------------------------------------------------------ #
        # Step 4: load and persist derivatives overlay                  #
        # ------------------------------------------------------------ #
        loader = DerivativesLoader(config)
        contracts = loader.load()

        if contracts:
            db.insert_contracts(contracts)
            log.info("%d derivative contract legs persisted", len(contracts))
        else:
            log.info("No derivatives overlay applied")

        # ------------------------------------------------------------ #
        # Summary                                                        #
        # ------------------------------------------------------------ #
        periods = db.get_all_periods()
        print(f"\n{'='*56}")
        print(f"  Source     : {config.source.upper()}")
        print(f"  CIK        : {config.cik}")
        print(f"  New filings: {new_filings}  |  Skipped: {skipped_filings}")
        print(f"  Prices     : {len(prices)} daily records")
        print(f"  Contracts  : {len(contracts)} legs")
        print(f"  Periods in DB: {len(periods)}")
        if periods:
            print(f"  Range: {periods[0]}  →  {periods[-1]}")
        print(f"  DB: {config.db_path}")
        print(f"{'='*56}\n")


if __name__ == "__main__":
    run()