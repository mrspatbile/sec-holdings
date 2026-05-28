"""
sec-holdings
------------
SEC EDGAR holdings fetcher (N-PORT + 13F) with daily price enrichment
and rolling derivatives overlay. Input layer for portfolio risk analysis.

Primary classes
---------------
    Config              Runtime configuration, reads from .env
    Fetcher             Fetches and normalises N-PORT and 13F holdings
    PriceFetcher        Downloads daily adjusted close prices via yfinance
    DerivativesLoader   Loads YAML overlay and generates rolling contract legs
    Database            SQLite persistence layer

Typical usage
-------------
    from sec_holdings.config import Config
    from sec_holdings.database import Database
    from sec_holdings.fetcher import Fetcher
    from sec_holdings.prices import PriceFetcher
    from sec_holdings.derivatives import DerivativesLoader
"""

from sec_holdings.config import Config
from sec_holdings.database import Database
from sec_holdings.derivatives import DerivativesLoader
from sec_holdings.fetcher import Fetcher
from sec_holdings.prices import PriceFetcher

__all__ = [
    "Config",
    "Database",
    "DerivativesLoader",
    "Fetcher",
    "PriceFetcher",
]