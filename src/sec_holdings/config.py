"""
config.py
---------
Single source of truth for all settings.
Reads from environment variables via python-dotenv.

Usage
-----
    from sec_holdings.config import Config
    config = Config.from_env()

Environment variables
---------------------
All variables are prefixed with SEC_HOLDINGS_.
Copy .env.example to .env and fill in your values.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv
import os

load_dotenv()


@dataclass(frozen=True)
class Config:
    """
    Runtime configuration for sec-holdings.

    Parameters
    ----------
    cik : str
        SEC EDGAR CIK number for the target fund.
        Leading zeros are optional -- normalised internally.
    source : str
        Filing type to fetch. One of: 'nport', '13f'.
    years : int
        Number of years of filing history to fetch.
        N-PORT: ~12 filings/year. 13F: ~4 filings/year.
    db_path : Path
        Path to the SQLite database file.
        Created on first run if it does not exist.
    overlay_path : Path | None
        Path to the YAML derivatives overlay file.
        If None, no overlay is applied.
    user_agent : str
        User-Agent string sent to SEC EDGAR.
        SEC fair-use policy requires a real contact string.
    """

    cik: str
    source: str
    years: int
    db_path: Path
    overlay_path: Path | None
    user_agent: str

    @classmethod
    def from_env(cls, **overrides) -> Config:
        """
        Build Config from environment variables with optional CLI overrides.
        Overrides take precedence over environment variables.

        Raises
        ------
        KeyError
            If SEC_HOLDINGS_CIK is not set and no cik override is provided.
        ValueError
            If source is not one of 'nport' or '13f'.
        """
        cik = overrides.get("cik") or os.environ["SEC_HOLDINGS_CIK"]

        source = overrides.get("source") or os.getenv("SEC_HOLDINGS_SOURCE", "13f")
        if source not in ("nport", "13f"):
            raise ValueError(f"source must be 'nport' or '13f', got '{source}'")

        overlay_raw = overrides.get("overlay_path") or os.getenv("SEC_HOLDINGS_OVERLAY_PATH")
        overlay_path = Path(overlay_raw) if overlay_raw else None

        return cls(
            cik=cik.strip().lstrip("0").zfill(10),
            source=source,
            years=int(overrides.get("years") or os.getenv("SEC_HOLDINGS_YEARS", 5)),
            db_path=Path(
                overrides.get("db_path") or os.getenv("SEC_HOLDINGS_DB_PATH", "sec_holdings.db")
            ),
            overlay_path=overlay_path,
            user_agent=overrides.get("user_agent") or os.getenv(
                "SEC_HOLDINGS_USER_AGENT",
                "sec-holdings mrspatbile@gmail.com",
            ),
        )

    @property
    def start_date(self) -> date:
        """Earliest filing date to fetch, based on configured years."""
        return date.today() - timedelta(days=365 * self.years)

    @property
    def cik_numeric(self) -> str:
        """CIK without leading zeros, as used in EDGAR archive URLs."""
        return self.cik.lstrip("0")