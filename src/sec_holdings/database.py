"""
database.py
-----------
SQLite persistence layer for sec-holdings.

Four tables
-----------
filings             One row per N-PORT or 13F filing
holdings            One row per position per filing
daily_prices        Daily adjusted close per ticker
derivative_contracts  One row per rolling contract leg

All writes are batched. Schema is created on first init.
Raw sqlite3 -- no ORM.

Usage
-----
    from sec_holdings.database import Database
    from sec_holdings.config import Config

    config = Config.from_env()
    with Database(config.db_path) as db:
        db.insert_holdings(holdings)
        db.insert_prices(prices)
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_DDL = """
CREATE TABLE IF NOT EXISTS filings (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    source              TEXT    NOT NULL,
    cik                 TEXT    NOT NULL,
    accession           TEXT    NOT NULL UNIQUE,
    form_type           TEXT,
    filing_date         TEXT,
    period_of_report    TEXT,
    reg_name            TEXT,
    series_name         TEXT,
    total_assets        REAL,
    net_assets          REAL
);

CREATE TABLE IF NOT EXISTS holdings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    filing_id       INTEGER NOT NULL REFERENCES filings(id),
    name            TEXT,
    cusip           TEXT,
    isin            TEXT,
    ticker          TEXT,
    value_usd       REAL,
    pct_val         REAL,
    shares          REAL,
    units           TEXT,
    asset_category  TEXT,
    country         TEXT,
    payoff_profile  TEXT,
    maturity_date   TEXT,
    coupon_rate     REAL,
    source          TEXT,
    period          TEXT,
    filing_date     TEXT,
    accession       TEXT
);

CREATE TABLE IF NOT EXISTS daily_prices (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker      TEXT    NOT NULL,
    date        TEXT    NOT NULL,
    adj_close   REAL    NOT NULL,
    volume      REAL,
    UNIQUE (ticker, date)
);

CREATE TABLE IF NOT EXISTS derivative_contracts (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    instrument_id               TEXT    NOT NULL,
    leg_id                      INTEGER NOT NULL,
    type                        TEXT    NOT NULL,
    put_call                    TEXT,
    underlying                  TEXT,
    underlying_type             TEXT,
    notional                    REAL,
    strike_type                 TEXT,
    strike_value                REAL,
    tenor                       TEXT,
    currency                    TEXT,
    pay_leg                     TEXT,
    fixed_rate                  REAL,
    spread_bps                  REAL,
    pair                        TEXT,
    direction                   TEXT,
    open_date                   TEXT    NOT NULL,
    close_date                  TEXT    NOT NULL,
    expiry_date                 TEXT    NOT NULL,
    status                      TEXT    NOT NULL,
    close_days_before_expiry    INTEGER,
    UNIQUE (instrument_id, leg_id)
);

CREATE INDEX IF NOT EXISTS idx_holdings_filing   ON holdings(filing_id);
CREATE INDEX IF NOT EXISTS idx_holdings_ticker   ON holdings(ticker);
CREATE INDEX IF NOT EXISTS idx_holdings_cusip    ON holdings(cusip);
CREATE INDEX IF NOT EXISTS idx_holdings_period   ON holdings(period);
CREATE INDEX IF NOT EXISTS idx_prices_ticker     ON daily_prices(ticker);
CREATE INDEX IF NOT EXISTS idx_prices_date       ON daily_prices(date);
CREATE INDEX IF NOT EXISTS idx_contracts_open    ON derivative_contracts(open_date);
CREATE INDEX IF NOT EXISTS idx_contracts_close   ON derivative_contracts(close_date);
CREATE INDEX IF NOT EXISTS idx_filings_period    ON filings(period_of_report);
"""


class Database:
    """
    SQLite persistence layer.

    Use as a context manager to ensure the connection is closed cleanly.

        with Database(path) as db:
            db.insert_holdings(holdings)

    Parameters
    ----------
    db_path : Path
        Path to the SQLite file. Created on first use if absent.
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None

    def __enter__(self) -> Database:
        self._conn = sqlite3.connect(self.db_path)
        self._conn.executescript(_DDL)
        self._conn.commit()
        log.info("Database ready at %s", self.db_path)
        return self

    def __exit__(self, *_) -> None:
        if self._conn:
            self._conn.close()

    # ------------------------------------------------------------------ #
    # Filings                                                              #
    # ------------------------------------------------------------------ #

    def filing_exists(self, accession: str) -> bool:
        """Return True if a filing with this accession number is already stored."""
        cur = self._conn.execute(
            "SELECT 1 FROM filings WHERE accession = ?", (accession,)
        )
        return cur.fetchone() is not None

    def insert_filing(self, filing_meta: dict) -> int:
        """
        Insert a filing metadata row.

        Parameters
        ----------
        filing_meta : dict
            Keys: source, cik, accession, form_type, filing_date,
            period_of_report, reg_name, series_name,
            total_assets, net_assets.

        Returns
        -------
        int
            Row id of the inserted (or existing) filing.
        """
        cur = self._conn.execute(
            """
            INSERT OR IGNORE INTO filings
                (source, cik, accession, form_type, filing_date,
                 period_of_report, reg_name, series_name,
                 total_assets, net_assets)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                filing_meta.get("source"),
                filing_meta.get("cik"),
                filing_meta["accession"],
                filing_meta.get("form_type"),
                filing_meta.get("filing_date"),
                filing_meta.get("period_of_report"),
                filing_meta.get("reg_name"),
                filing_meta.get("series_name"),
                filing_meta.get("total_assets"),
                filing_meta.get("net_assets"),
            ),
        )
        self._conn.commit()
        if cur.lastrowid:
            return cur.lastrowid
        cur = self._conn.execute(
            "SELECT id FROM filings WHERE accession = ?", (filing_meta["accession"],)
        )
        return cur.fetchone()[0]

    # ------------------------------------------------------------------ #
    # Holdings                                                             #
    # ------------------------------------------------------------------ #

    def insert_holdings(self, holdings: list[dict], filing_id: int) -> None:
        """
        Batch insert holdings for a single filing.

        Parameters
        ----------
        holdings : list[dict]
            Normalised holding dicts as returned by Fetcher.
        filing_id : int
            FK to the filings table row for this period.
        """
        self._conn.executemany(
            """
            INSERT INTO holdings
                (filing_id, name, cusip, isin, ticker,
                 value_usd, pct_val, shares, units,
                 asset_category, country, payoff_profile,
                 maturity_date, coupon_rate,
                 source, period, filing_date, accession)
            VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    filing_id,
                    h.get("name"), h.get("cusip"), h.get("isin"), h.get("ticker"),
                    h.get("value_usd"), h.get("pct_val"), h.get("shares"), h.get("units"),
                    h.get("asset_category"), h.get("country"), h.get("payoff_profile"),
                    h.get("maturity_date"), h.get("coupon_rate"),
                    h.get("source"), h.get("period"), h.get("filing_date"), h.get("accession"),
                )
                for h in holdings
            ],
        )
        self._conn.commit()

    # ------------------------------------------------------------------ #
    # Daily prices                                                         #
    # ------------------------------------------------------------------ #

    def insert_prices(self, prices: list[dict]) -> None:
        """
        Batch insert daily prices. Duplicate (ticker, date) rows are ignored.

        Parameters
        ----------
        prices : list[dict]
            Price records as returned by PriceFetcher.
            Each dict: ticker, date, adj_close, volume.
        """
        self._conn.executemany(
            """
            INSERT OR IGNORE INTO daily_prices (ticker, date, adj_close, volume)
            VALUES (?, ?, ?, ?)
            """,
            [
                (p["ticker"], p["date"], p["adj_close"], p.get("volume"))
                for p in prices
            ],
        )
        self._conn.commit()
        log.info("Inserted %d price records", len(prices))

    # ------------------------------------------------------------------ #
    # Derivative contracts                                                 #
    # ------------------------------------------------------------------ #

    def insert_contracts(self, contracts: list[dict]) -> None:
        """
        Batch insert derivative contract legs.
        Duplicate (instrument_id, leg_id) rows are ignored.

        Parameters
        ----------
        contracts : list[dict]
            Contract legs as returned by DerivativesLoader.load().
        """
        self._conn.executemany(
            """
            INSERT OR IGNORE INTO derivative_contracts
                (instrument_id, leg_id, type, put_call,
                 underlying, underlying_type, notional,
                 strike_type, strike_value, tenor, currency,
                 pay_leg, fixed_rate, spread_bps,
                 pair, direction,
                 open_date, close_date, expiry_date,
                 status, close_days_before_expiry)
            VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    c["instrument_id"], c["leg_id"], c["type"], c.get("put_call"),
                    c.get("underlying"), c.get("underlying_type"), c.get("notional"),
                    c.get("strike_type"), c.get("strike_value"), c.get("tenor"),
                    c.get("currency"), c.get("pay_leg"), c.get("fixed_rate"),
                    c.get("spread_bps"), c.get("pair"), c.get("direction"),
                    c["open_date"], c["close_date"], c["expiry_date"],
                    c["status"], c.get("close_days_before_expiry"),
                )
                for c in contracts
            ],
        )
        self._conn.commit()
        log.info("Inserted %d derivative contract legs", len(contracts))

    # ------------------------------------------------------------------ #
    # Read helpers                                                         #
    # ------------------------------------------------------------------ #

    def get_all_periods(self) -> list[str]:
        """Return sorted list of all reporting periods in the DB."""
        cur = self._conn.execute(
            "SELECT DISTINCT period_of_report FROM filings "
            "WHERE period_of_report IS NOT NULL ORDER BY period_of_report"
        )
        return [r[0] for r in cur.fetchall()]

    def get_all_periods_for_cik(self, cik: str) -> list[str]:
        """Return sorted list of reporting periods for a specific CIK."""
        cur = self._conn.execute(
            "SELECT DISTINCT period_of_report FROM filings "
            "WHERE cik = ? AND period_of_report IS NOT NULL "
            "ORDER BY period_of_report",
            (cik,),
        )
        return [r[0] for r in cur.fetchall()]

    def get_holdings_for_period(self, period: str) -> list[dict]:
        """
        Return all holdings for a given reporting period.

        Parameters
        ----------
        period : str
            Reporting period end date in YYYY-MM-DD format.

        Returns
        -------
        list[dict]
            Holdings sorted by pct_val descending.
        """
        cur = self._conn.execute(
            """
            SELECT h.*
            FROM holdings h
            JOIN filings f ON h.filing_id = f.id
            WHERE f.period_of_report = ?
            ORDER BY h.pct_val DESC NULLS LAST
            """,
            (period,),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def get_holdings_for_period_and_cik(self, period: str, cik: str) -> list[dict]:
        """
        Return all holdings for a given reporting period and CIK.

        Parameters
        ----------
        period : str
            Reporting period end date in YYYY-MM-DD format.
        cik : str
            Fund CIK number.

        Returns
        -------
        list[dict]
            Holdings sorted by pct_val descending.
        """
        cur = self._conn.execute(
            """
            SELECT h.* FROM holdings h
            JOIN filings f ON h.filing_id = f.id
            WHERE f.period_of_report = ? AND f.cik = ?
            ORDER BY h.pct_val DESC NULLS LAST
            """,
            (period, cik),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def get_position_history(self, ticker: str) -> list[dict]:
        """
        Return the weight and value time series for a single ticker.

        Parameters
        ----------
        ticker : str
            Ticker symbol.

        Returns
        -------
        list[dict]
            One row per filing period, sorted ascending.
        """
        cur = self._conn.execute(
            """
            SELECT f.period_of_report, h.name, h.ticker,
                   h.value_usd, h.pct_val, h.shares
            FROM holdings h
            JOIN filings f ON h.filing_id = f.id
            WHERE h.ticker = ?
            ORDER BY f.period_of_report
            """,
            (ticker,),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def get_prices_for_ticker(self, ticker: str) -> list[dict]:
        """
        Return the full daily price history for a ticker.

        Parameters
        ----------
        ticker : str
            Ticker symbol.

        Returns
        -------
        list[dict]
            One row per trading day, sorted ascending.
        """
        cur = self._conn.execute(
            "SELECT ticker, date, adj_close, volume "
            "FROM daily_prices WHERE ticker = ? ORDER BY date",
            (ticker,),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def get_active_contracts_on(self, target_date: str) -> list[dict]:
        """
        Return all derivative contract legs active on a given date.

        A contract is active when: open_date <= target_date < close_date.
        This is the primary consumption method for the pricing project.

        Parameters
        ----------
        target_date : str
            Date in YYYY-MM-DD format.

        Returns
        -------
        list[dict]
            Active contract legs with all columns.
        """
        cur = self._conn.execute(
            """
            SELECT * FROM derivative_contracts
            WHERE open_date <= ? AND close_date > ?
            ORDER BY instrument_id, open_date
            """,
            (target_date, target_date),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def get_all_contracts(self) -> list[dict]:
        """Return all derivative contract legs, sorted by instrument and open date."""
        cur = self._conn.execute(
            "SELECT * FROM derivative_contracts ORDER BY instrument_id, open_date"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    
    def get_latest_filing_date(self, cik: str) -> Optional[str]:
        """
        Return the most recent filing_date for a CIK already in the DB.
        Used by the fetcher to skip filings already stored.

        Returns
        -------
        str
            Latest filing date in YYYY-MM-DD format, or None if CIK not in DB.
        """
        cur = self._conn.execute(
            "SELECT MAX(filing_date) FROM filings WHERE cik = ?",
            (cik,),
        )
        row = cur.fetchone()
        return row[0] if row and row[0] else None

    def get_stale_tickers(self, tickers: list[str], as_of: str) -> dict[str, Optional[str]]:
        """
        Return tickers that need a price update, mapped to their last price date.

        A ticker needs updating when:
        - It has no prices in the DB at all (last_date = None)
        - Its latest price date is before as_of

        Parameters
        ----------
        tickers : list[str]
            All tickers to check.
        as_of : str
            Target date in YYYY-MM-DD format. Tickers with prices up to
            this date are considered fresh and excluded from the result.

        Returns
        -------
        dict[str, Optional[str]]
            Keys are stale tickers. Values are the last price date in DB,
            or None if the ticker has no prices at all.
        """
        stale: dict[str, Optional[str]] = {}
        for ticker in tickers:
            cur = self._conn.execute(
                "SELECT MAX(date) FROM daily_prices WHERE ticker = ?",
                (ticker,),
            )
            row = cur.fetchone()
            last_date = row[0] if row and row[0] else None
            if last_date is None or last_date < as_of:
                stale[ticker] = last_date
        return stale