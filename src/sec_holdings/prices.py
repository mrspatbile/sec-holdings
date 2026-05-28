"""
prices.py
---------
Fetches daily adjusted close prices for a list of tickers via yfinance.

Design
------
- Takes tickers extracted from fetched holdings
- Downloads adjusted close prices for the full configured date window
- Returns a flat list of daily price dicts ready for DB persistence
- Tickers that fail to download are logged and skipped -- never fatal
- CUSIP-only positions (no ticker resolved) are excluded with a warning

Carry-forward note
------------------
Prices are fetched once per ticker for the full date range.
The daily portfolio valuation layer applies them against the
carry-forward positions. This module does not know about positions.

Units
-----
All prices are adjusted close in USD (yfinance adj_close).
Volume is included for liquidity context but not used in risk metrics.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Optional

import pandas as pd
import yfinance as yf

from sec_holdings.config import Config

log = logging.getLogger(__name__)

# yfinance uses a single batch download -- tickers above this threshold
# are chunked to avoid silent failures on large requests
_CHUNK_SIZE = 50


class PriceFetcher:
    """
    Downloads daily adjusted close prices for a set of tickers.

    Parameters
    ----------
    config : Config
        Runtime configuration. start_date determines the earliest
        price date fetched.
    """

    def __init__(self, config: Config) -> None:
        self.config = config

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def fetch(self, tickers: list[str]) -> list[dict]:
        """
        Download daily prices for all tickers in the list.

        Parameters
        ----------
        tickers : list[str]
            Ticker symbols to fetch. None and empty strings are filtered out.

        Returns
        -------
        list[dict]
            Flat list of daily price records, one per ticker per trading day.
            Each dict has: ticker, date, adj_close, volume.
            Sorted by ticker then date ascending.
        """
        clean = self._clean_tickers(tickers)
        if not clean:
            log.warning("No valid tickers to fetch prices for")
            return []

        log.info(
            "Fetching daily prices for %d tickers from %s to %s",
            len(clean),
            self.config.start_date.isoformat(),
            date.today().isoformat(),
        )

        all_records: list[dict] = []
        for chunk in self._chunk(clean, _CHUNK_SIZE):
            records = self._download_chunk(chunk)
            all_records.extend(records)

        all_records.sort(key=lambda x: (x["ticker"], x["date"]))
        log.info("Downloaded %d daily price records", len(all_records))
        return all_records

    def fetch_for_holdings(self, holdings: list[dict]) -> list[dict]:
        """
        Convenience wrapper -- extracts unique tickers from a holdings
        list and fetches prices for all of them.

        Positions without a ticker are logged and skipped.

        Parameters
        ----------
        holdings : list[dict]
            Normalised holdings dicts as returned by Fetcher.fetch().

        Returns
        -------
        list[dict]
            Daily price records for all resolvable tickers.
        """
        tickers = list({h["ticker"] for h in holdings if h.get("ticker")})

        no_ticker = [h["name"] for h in holdings if not h.get("ticker")]
        if no_ticker:
            log.warning(
                "%d positions have no ticker -- prices unavailable: %s",
                len(no_ticker),
                ", ".join(no_ticker[:10]) + (" ..." if len(no_ticker) > 10 else ""),
            )

        return self.fetch(tickers)

    # ------------------------------------------------------------------ #
    # Internal                                                             #
    # ------------------------------------------------------------------ #

    def _download_chunk(self, tickers: list[str]) -> list[dict]:
        """
        Download one chunk of tickers via yfinance batch download.
        Failed tickers are logged and excluded -- never raises.
        """
        try:
            raw: pd.DataFrame = yf.download(
                tickers=tickers,
                start=self.config.start_date.isoformat(),
                end=date.today().isoformat(),
                auto_adjust=True,
                progress=False,
                threads=True,
            )
        except Exception as exc:
            log.error("yfinance download failed for chunk %s: %s", tickers, exc)
            return []

        if raw.empty:
            log.warning("yfinance returned empty DataFrame for chunk: %s", tickers)
            return []

        return self._flatten(raw, tickers)

    def _flatten(self, raw: pd.DataFrame, tickers: list[str]) -> list[dict]:
        """
        Flatten a yfinance multi-ticker DataFrame into a list of dicts.

        yfinance returns a MultiIndex DataFrame when multiple tickers
        are requested: columns are (field, ticker). Single ticker
        requests return a simple column index.
        """
        records: list[dict] = []

        # Single ticker -- simple column index
        if len(tickers) == 1:
            ticker = tickers[0]
            for dt, row in raw.iterrows():
                adj_close = _safe_float(row.get("Close"))
                if adj_close is None:
                    continue
                records.append({
                    "ticker": ticker,
                    "date": str(dt.date()),
                    "adj_close": adj_close,
                    "volume": _safe_float(row.get("Volume")),
                })
            return records

        # Multiple tickers -- MultiIndex columns (field, ticker)
        for ticker in tickers:
            try:
                close_col = ("Close", ticker)
                vol_col = ("Volume", ticker)

                if close_col not in raw.columns:
                    log.warning("No price data returned for %s", ticker)
                    continue

                series = raw[close_col].dropna()
                vol_series = raw[vol_col] if vol_col in raw.columns else None

                for dt, price in series.items():
                    adj_close = _safe_float(price)
                    if adj_close is None:
                        continue
                    volume = (
                        _safe_float(vol_series.get(dt)) if vol_series is not None else None
                    )
                    records.append({
                        "ticker": ticker,
                        "date": str(dt.date()),
                        "adj_close": adj_close,
                        "volume": volume,
                    })

            except Exception as exc:
                log.warning("Could not extract prices for %s: %s", ticker, exc)
                continue

        return records

    @staticmethod
    def _clean_tickers(tickers: list[str]) -> list[str]:
        """Remove None, empty strings, and duplicates. Preserves order."""
        seen: set[str] = set()
        clean: list[str] = []
        for t in tickers:
            if t and isinstance(t, str) and t.strip() and t not in seen:
                seen.add(t)
                clean.append(t.strip().upper())
        return clean

    @staticmethod
    def _chunk(items: list, size: int):
        """Yield successive chunks of a list."""
        for i in range(0, len(items), size):
            yield items[i: i + size]


# ------------------------------------------------------------------ #
# Helper                                                              #
# ------------------------------------------------------------------ #

def _safe_float(value) -> Optional[float]:
    if value is None:
        return None
    try:
        import math
        f = float(value)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None