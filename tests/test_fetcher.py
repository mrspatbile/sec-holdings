"""
tests/test_fetcher.py
---------------------
Unit tests for Fetcher normalisation logic.
No EDGAR calls -- edgartools Company and filing objects are mocked.

What is tested
--------------
- _normalise_13f_row: field mapping, value passthrough, pct_val calculation
- _normalise_nport_row: field mapping, value passthrough, pct_val fallback
- _safe_str: None handling, whitespace stripping, NaN handling
- _safe_float: type coercion, NaN handling, invalid strings
- fetch() routing: source=13f calls _fetch_13f, source=nport calls _fetch_nport
"""

from __future__ import annotations

import math
import pandas as pd
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

from sec_holdings.fetcher import Fetcher, _safe_str, _safe_float


# ------------------------------------------------------------------ #
# Mock config                                                         #
# ------------------------------------------------------------------ #

class MockConfig:
    cik = "0001336528"
    source = "13f"
    user_agent = "sec-holdings test@example.com"
    db_path = Path("/tmp/test.db")
    years = 5

    @property
    def start_date(self):
        from datetime import date
        return date(2021, 1, 1)

    @property
    def cik_numeric(self):
        return self.cik.lstrip("0")


class MockNPortConfig(MockConfig):
    source = "nport"


# ------------------------------------------------------------------ #
# _safe_str                                                           #
# ------------------------------------------------------------------ #

class TestSafeStr:
    def test_normal_string(self):
        assert _safe_str("Apple Inc") == "Apple Inc"

    def test_strips_whitespace(self):
        assert _safe_str("  Apple Inc  ") == "Apple Inc"

    def test_none_returns_none(self):
        assert _safe_str(None) is None

    def test_nan_returns_none(self):
        assert _safe_str(float("nan")) is None

    def test_empty_string_returns_none(self):
        assert _safe_str("") is None

    def test_whitespace_only_returns_none(self):
        assert _safe_str("   ") is None

    def test_converts_non_string(self):
        assert _safe_str(12345) == "12345"


# ------------------------------------------------------------------ #
# _safe_float                                                         #
# ------------------------------------------------------------------ #

class TestSafeFloat:
    def test_normal_float(self):
        assert _safe_float(123.45) == 123.45

    def test_integer(self):
        assert _safe_float(100) == 100.0

    def test_string_number(self):
        assert _safe_float("99.9") == 99.9

    def test_none_returns_none(self):
        assert _safe_float(None) is None

    def test_nan_returns_none(self):
        assert _safe_float(float("nan")) is None

    def test_invalid_string_returns_none(self):
        assert _safe_float("n/a") is None

    def test_zero(self):
        assert _safe_float(0) == 0.0


# ------------------------------------------------------------------ #
# 13F row normalisation                                               #
# ------------------------------------------------------------------ #

class TestNormalise13FRow:
    def setup_method(self):
        self.fetcher = Fetcher(MockConfig())
        self.period = "2024-03-31"
        self.filing_date = "2024-05-15"
        self.accession = "0001234567-24-000001"
        self.total_value = 10_000_000.0

    def _make_row(self, **kwargs) -> pd.Series:
        defaults = {
            "Issuer": "Alphabet Inc",
            "Cusip": "02079K305",
            "Ticker": "GOOGL",
            "Value": 2_000_000.0,
            "SharesPrnAmount": 500_000.0,
            "Type": "SH",
        }
        defaults.update(kwargs)
        return pd.Series(defaults)

    def test_name_mapped_from_issuer(self):
        row = self._make_row()
        result = self.fetcher._normalise_13f_row(
            row, self.period, self.filing_date, self.accession, self.total_value
        )
        assert result["name"] == "Alphabet Inc"

    def test_cusip_mapped(self):
        row = self._make_row()
        result = self.fetcher._normalise_13f_row(
            row, self.period, self.filing_date, self.accession, self.total_value
        )
        assert result["cusip"] == "02079K305"

    def test_ticker_mapped(self):
        row = self._make_row()
        result = self.fetcher._normalise_13f_row(
            row, self.period, self.filing_date, self.accession, self.total_value
        )
        assert result["ticker"] == "GOOGL"

    def test_value_usd_passthrough(self):
        row = self._make_row(Value=2_000_000.0)
        result = self.fetcher._normalise_13f_row(
            row, self.period, self.filing_date, self.accession, self.total_value
        )
        assert result["value_usd"] == 2_000_000.0

    def test_pct_val_calculated(self):
        row = self._make_row(Value=2_000_000.0)
        result = self.fetcher._normalise_13f_row(
            row, self.period, self.filing_date, self.accession, self.total_value
        )
        assert result["pct_val"] == pytest.approx(20.0)

    def test_source_is_13f(self):
        row = self._make_row()
        result = self.fetcher._normalise_13f_row(
            row, self.period, self.filing_date, self.accession, self.total_value
        )
        assert result["source"] == "13f"

    def test_payoff_profile_is_long(self):
        row = self._make_row()
        result = self.fetcher._normalise_13f_row(
            row, self.period, self.filing_date, self.accession, self.total_value
        )
        assert result["payoff_profile"] == "Long"

    def test_isin_is_none(self):
        """13F does not report ISIN."""
        row = self._make_row()
        result = self.fetcher._normalise_13f_row(
            row, self.period, self.filing_date, self.accession, self.total_value
        )
        assert result["isin"] is None

    def test_period_and_accession_stored(self):
        row = self._make_row()
        result = self.fetcher._normalise_13f_row(
            row, self.period, self.filing_date, self.accession, self.total_value
        )
        assert result["period"] == self.period
        assert result["accession"] == self.accession


# ------------------------------------------------------------------ #
# N-PORT row normalisation                                            #
# ------------------------------------------------------------------ #

class TestNormaliseNPortRow:
    def setup_method(self):
        self.fetcher = Fetcher(MockNPortConfig())
        self.period = "2024-01-31"
        self.filing_date = "2024-02-28"
        self.accession = "0000940400-24-000001"
        self.total_value = 1_000_000_000.0

    def _make_row(self, **kwargs) -> pd.Series:
        defaults = {
            "name": "Apple Inc",
            "cusip": "037833100",
            "isin": "US0378331005",
            "ticker": "AAPL",
            "value_usd": 150_000_000.0,
            "pct_value": 15.0,
            "balance": 800_000.0,
            "units": "NS",
            "asset_category": "EC",
            "investment_country": "US",
            "payoff_profile": "Long",
            "maturity_date": None,
            "annualized_rate": None,
        }
        defaults.update(kwargs)
        return pd.Series(defaults)

    def test_name_mapped(self):
        row = self._make_row()
        result = self.fetcher._normalise_nport_row(
            row, self.period, self.filing_date, self.accession, self.total_value
        )
        assert result["name"] == "Apple Inc"

    def test_isin_mapped(self):
        row = self._make_row()
        result = self.fetcher._normalise_nport_row(
            row, self.period, self.filing_date, self.accession, self.total_value
        )
        assert result["isin"] == "US0378331005"

    def test_pct_val_from_pct_value(self):
        row = self._make_row(pct_value=15.0)
        result = self.fetcher._normalise_nport_row(
            row, self.period, self.filing_date, self.accession, self.total_value
        )
        assert result["pct_val"] == pytest.approx(15.0)

    def test_pct_val_fallback_calculation(self):
        """When pct_value is None, pct_val is calculated from value_usd / total."""
        row = self._make_row(pct_value=None, value_usd=100_000_000.0)
        result = self.fetcher._normalise_nport_row(
            row, self.period, self.filing_date, self.accession, 1_000_000_000.0
        )
        assert result["pct_val"] == pytest.approx(10.0)

    def test_asset_category_mapped(self):
        row = self._make_row(asset_category="EC")
        result = self.fetcher._normalise_nport_row(
            row, self.period, self.filing_date, self.accession, self.total_value
        )
        assert result["asset_category"] == "EC"

    def test_country_mapped(self):
        row = self._make_row(investment_country="US")
        result = self.fetcher._normalise_nport_row(
            row, self.period, self.filing_date, self.accession, self.total_value
        )
        assert result["country"] == "US"

    def test_coupon_rate_mapped(self):
        row = self._make_row(annualized_rate=4.25)
        result = self.fetcher._normalise_nport_row(
            row, self.period, self.filing_date, self.accession, self.total_value
        )
        assert result["coupon_rate"] == pytest.approx(4.25)

    def test_source_is_nport(self):
        row = self._make_row()
        result = self.fetcher._normalise_nport_row(
            row, self.period, self.filing_date, self.accession, self.total_value
        )
        assert result["source"] == "nport"


# ------------------------------------------------------------------ #
# fetch() routing                                                     #
# ------------------------------------------------------------------ #

class TestFetchRouting:
    def test_13f_source_calls_fetch_13f(self):
        config = MockConfig()
        config.source = "13f"
        fetcher = Fetcher(config)
        with patch.object(fetcher, "_fetch_13f", return_value=[]) as mock:
            fetcher.fetch()
            mock.assert_called_once()

    def test_nport_source_calls_fetch_nport(self):
        config = MockConfig()
        config.source = "nport"
        fetcher = Fetcher(config)
        with patch.object(fetcher, "_fetch_nport", return_value=[]) as mock:
            fetcher.fetch()
            mock.assert_called_once()