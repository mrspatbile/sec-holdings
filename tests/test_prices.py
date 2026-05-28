"""
tests/test_prices.py
--------------------
Unit tests for PriceFetcher.
No network calls -- yfinance download is mocked throughout.
"""

from __future__ import annotations

import pandas as pd
import pytest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

from sec_holdings.prices import PriceFetcher, _safe_float


# ------------------------------------------------------------------ #
# Mock config                                                         #
# ------------------------------------------------------------------ #

class MockConfig:
    start_date = date(2024, 1, 1)
    db_path = Path("/tmp/test.db")


# ------------------------------------------------------------------ #
# Helpers                                                             #
# ------------------------------------------------------------------ #

def _make_single_ticker_df(ticker: str, dates: list[str], prices: list[float]) -> pd.DataFrame:
    """Build a single-ticker yfinance-style DataFrame."""
    return pd.DataFrame(
        {"Close": prices, "Volume": [1_000_000] * len(dates)},
        index=pd.to_datetime(dates),
    )


def _make_multi_ticker_df(data: dict[str, list[float]], dates: list[str]) -> pd.DataFrame:
    """Build a multi-ticker yfinance-style MultiIndex DataFrame."""
    arrays = []
    for field in ("Close", "Volume"):
        for ticker in data:
            arrays.append((field, ticker))

    index = pd.MultiIndex.from_tuples(arrays)
    rows = []
    for i, _ in enumerate(dates):
        row = []
        for field in ("Close", "Volume"):
            for ticker, prices in data.items():
                row.append(prices[i] if field == "Close" else 1_000_000)
        rows.append(row)

    df = pd.DataFrame(rows, columns=index, index=pd.to_datetime(dates))
    return df


# ------------------------------------------------------------------ #
# _safe_float                                                         #
# ------------------------------------------------------------------ #

class TestSafeFloat:
    def test_normal_value(self):
        assert _safe_float(123.45) == 123.45

    def test_none_returns_none(self):
        assert _safe_float(None) is None

    def test_nan_returns_none(self):
        import math
        assert _safe_float(float("nan")) is None

    def test_string_number(self):
        assert _safe_float("99.9") == 99.9

    def test_invalid_string(self):
        assert _safe_float("abc") is None


# ------------------------------------------------------------------ #
# Ticker cleaning                                                     #
# ------------------------------------------------------------------ #

class TestCleanTickers:
    def test_removes_none(self):
        result = PriceFetcher._clean_tickers([None, "AAPL", None])
        assert result == ["AAPL"]

    def test_removes_empty_strings(self):
        result = PriceFetcher._clean_tickers(["", "MSFT", "  "])
        assert result == ["MSFT"]

    def test_deduplicates(self):
        result = PriceFetcher._clean_tickers(["AAPL", "AAPL", "MSFT"])
        assert result == ["AAPL", "MSFT"]

    def test_uppercases(self):
        result = PriceFetcher._clean_tickers(["aapl", "msft"])
        assert result == ["AAPL", "MSFT"]

    def test_empty_input(self):
        assert PriceFetcher._clean_tickers([]) == []


# ------------------------------------------------------------------ #
# Chunking                                                            #
# ------------------------------------------------------------------ #

class TestChunk:
    def test_exact_size(self):
        chunks = list(PriceFetcher._chunk([1, 2, 3, 4], 2))
        assert chunks == [[1, 2], [3, 4]]

    def test_remainder(self):
        chunks = list(PriceFetcher._chunk([1, 2, 3], 2))
        assert chunks == [[1, 2], [3]]

    def test_single_chunk(self):
        chunks = list(PriceFetcher._chunk([1, 2], 10))
        assert chunks == [[1, 2]]


# ------------------------------------------------------------------ #
# fetch_for_holdings                                                  #
# ------------------------------------------------------------------ #

class TestFetchForHoldings:
    def setup_method(self):
        self.fetcher = PriceFetcher(MockConfig())

    def test_extracts_unique_tickers(self):
        holdings = [
            {"ticker": "AAPL", "name": "Apple"},
            {"ticker": "AAPL", "name": "Apple"},   # duplicate
            {"ticker": "MSFT", "name": "Microsoft"},
            {"ticker": None,   "name": "US Treasury"},  # no ticker
        ]
        with patch.object(self.fetcher, "fetch", return_value=[]) as mock_fetch:
            self.fetcher.fetch_for_holdings(holdings)
            called_tickers = set(mock_fetch.call_args[0][0])
            assert called_tickers == {"AAPL", "MSFT"}

    def test_no_ticker_positions_logged(self, caplog):
        holdings = [{"ticker": None, "name": "US Treasury Bill"}]
        with patch.object(self.fetcher, "fetch", return_value=[]):
            import logging
            with caplog.at_level(logging.WARNING, logger="sec_holdings.prices"):
                self.fetcher.fetch_for_holdings(holdings)
        assert "no ticker" in caplog.text.lower()


# ------------------------------------------------------------------ #
# Single ticker download                                              #
# ------------------------------------------------------------------ #

class TestSingleTickerDownload:
    def setup_method(self):
        self.fetcher = PriceFetcher(MockConfig())

    def test_returns_records(self):
        dates = ["2024-01-02", "2024-01-03"]
        mock_df = _make_single_ticker_df("AAPL", dates, [185.0, 186.5])

        with patch("yfinance.download", return_value=mock_df):
            records = self.fetcher.fetch(["AAPL"])

        assert len(records) == 2
        assert records[0]["ticker"] == "AAPL"
        assert records[0]["adj_close"] == 185.0
        assert records[0]["date"] == "2024-01-02"

    def test_sorted_by_date(self):
        dates = ["2024-01-03", "2024-01-02"]
        mock_df = _make_single_ticker_df("AAPL", dates, [186.5, 185.0])

        with patch("yfinance.download", return_value=mock_df):
            records = self.fetcher.fetch(["AAPL"])

        assert records[0]["date"] < records[1]["date"]

    def test_empty_df_returns_empty(self):
        with patch("yfinance.download", return_value=pd.DataFrame()):
            records = self.fetcher.fetch(["AAPL"])
        assert records == []

    def test_download_exception_returns_empty(self):
        with patch("yfinance.download", side_effect=Exception("network error")):
            records = self.fetcher.fetch(["AAPL"])
        assert records == []


# ------------------------------------------------------------------ #
# Empty and edge cases                                                #
# ------------------------------------------------------------------ #

class TestEdgeCases:
    def setup_method(self):
        self.fetcher = PriceFetcher(MockConfig())

    def test_no_tickers_returns_empty(self):
        records = self.fetcher.fetch([])
        assert records == []

    def test_all_none_tickers_returns_empty(self):
        records = self.fetcher.fetch([None, None])
        assert records == []