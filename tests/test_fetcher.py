"""
tests/test_fetcher.py
---------------------
Unit tests for Fetcher normalisation logic.
No EDGAR calls -- edgartools Company and filing objects are mocked.

What is tested
--------------
- _normalise_13f_row: field mapping, value passthrough, pct_val calculation
- _normalise_nport_row: field mapping, value passthrough, pct_val fallback,
                        reg_name / net_assets / total_assets passthrough
- _safe_str: None handling, whitespace stripping, NaN handling
- _safe_float: type coercion, NaN handling, invalid strings
- fetch() routing: source=13f calls _fetch_13f, source=nport calls _fetch_nport
- _filing_meta_from_holdings: reads reg_name / net_assets / total_assets
"""

from __future__ import annotations

import pandas as pd
import pytest
from pathlib import Path
from unittest.mock import patch

from sec_holdings.fetcher import Fetcher, _safe_str, _safe_float
from sec_holdings.main import _filing_meta_from_holdings


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

    def _call(self, row, **kwargs):
        return self.fetcher._normalise_13f_row(
            row, self.period, self.filing_date,
            self.accession, self.total_value, **kwargs
        )

    def test_name_mapped_from_issuer(self):
        assert self._call(self._make_row())["name"] == "Alphabet Inc"

    def test_cusip_mapped(self):
        assert self._call(self._make_row())["cusip"] == "02079K305"

    def test_ticker_mapped(self):
        assert self._call(self._make_row())["ticker"] == "GOOGL"

    def test_value_usd_passthrough(self):
        assert self._call(self._make_row(Value=2_000_000.0))["value_usd"] == 2_000_000.0

    def test_pct_val_calculated(self):
        assert self._call(self._make_row(Value=2_000_000.0))["pct_val"] == pytest.approx(20.0)

    def test_source_is_13f(self):
        assert self._call(self._make_row())["source"] == "13f"

    def test_payoff_profile_is_long(self):
        assert self._call(self._make_row())["payoff_profile"] == "Long"

    def test_isin_is_none(self):
        assert self._call(self._make_row())["isin"] is None

    def test_period_and_accession_stored(self):
        result = self._call(self._make_row())
        assert result["period"] == self.period
        assert result["accession"] == self.accession

    def test_reg_name_stored(self):
        result = self._call(self._make_row(), reg_name="Pershing Square Capital")
        assert result["reg_name"] == "Pershing Square Capital"

    def test_net_assets_is_none_for_13f(self):
        """13F does not report net assets by regulation."""
        result = self._call(self._make_row())
        assert result["net_assets"] is None

    def test_total_assets_is_none_for_13f(self):
        """13F does not report total assets by regulation."""
        result = self._call(self._make_row())
        assert result["total_assets"] is None


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

    def _call(self, row, **kwargs):
        return self.fetcher._normalise_nport_row(
            row, self.period, self.filing_date,
            self.accession, self.total_value, **kwargs
        )

    def test_name_mapped(self):
        assert self._call(self._make_row())["name"] == "Apple Inc"

    def test_isin_mapped(self):
        assert self._call(self._make_row())["isin"] == "US0378331005"

    def test_pct_val_from_pct_value(self):
        assert self._call(self._make_row(pct_value=15.0))["pct_val"] == pytest.approx(15.0)

    def test_pct_val_fallback_calculation(self):
        row = self._make_row(pct_value=None, value_usd=100_000_000.0)
        result = self.fetcher._normalise_nport_row(
            row, self.period, self.filing_date,
            self.accession, 1_000_000_000.0
        )
        assert result["pct_val"] == pytest.approx(10.0)

    def test_asset_category_mapped(self):
        assert self._call(self._make_row(asset_category="EC"))["asset_category"] == "EC"

    def test_country_mapped(self):
        assert self._call(self._make_row(investment_country="US"))["country"] == "US"

    def test_coupon_rate_mapped(self):
        assert self._call(self._make_row(annualized_rate=4.25))["coupon_rate"] == pytest.approx(4.25)

    def test_source_is_nport(self):
        assert self._call(self._make_row())["source"] == "nport"

    def test_reg_name_stored(self):
        result = self._call(self._make_row(), reg_name="Fairholme Funds Inc")
        assert result["reg_name"] == "Fairholme Funds Inc"

    def test_net_assets_stored(self):
        result = self._call(self._make_row(), net_assets=1_451_766_475.80)
        assert result["net_assets"] == pytest.approx(1_451_766_475.80)

    def test_total_assets_stored(self):
        result = self._call(self._make_row(), total_assets=1_453_094_324.22)
        assert result["total_assets"] == pytest.approx(1_453_094_324.22)

    def test_defaults_to_none_without_fund_data(self):
        """Calling without fund metadata should store None for all three fields."""
        result = self._call(self._make_row())
        assert result["reg_name"] is None
        assert result["net_assets"] is None
        assert result["total_assets"] is None


# ------------------------------------------------------------------ #
# _filing_meta_from_holdings                                          #
# ------------------------------------------------------------------ #

class TestFilingMetaFromHoldings:
    def _make_holding(self, source="13f", **kwargs) -> dict:
        base = {
            "source": source,
            "accession": "0001234567-24-000001",
            "filing_date": "2024-05-15",
            "period": "2024-03-31",
            "reg_name": None,
            "net_assets": None,
            "total_assets": None,
        }
        base.update(kwargs)
        return base

    def test_reg_name_read_from_holding(self):
        holdings = [self._make_holding(reg_name="Pershing Square Capital")]
        meta = _filing_meta_from_holdings(holdings, "0001336528")
        assert meta["reg_name"] == "Pershing Square Capital"

    def test_net_assets_read_from_holding(self):
        holdings = [self._make_holding(source="nport", net_assets=1_451_766_475.80)]
        meta = _filing_meta_from_holdings(holdings, "0001096344")
        assert meta["net_assets"] == pytest.approx(1_451_766_475.80)

    def test_total_assets_read_from_holding(self):
        holdings = [self._make_holding(source="nport", total_assets=1_453_094_324.22)]
        meta = _filing_meta_from_holdings(holdings, "0001096344")
        assert meta["total_assets"] == pytest.approx(1_453_094_324.22)

    def test_none_when_not_provided(self):
        holdings = [self._make_holding()]
        meta = _filing_meta_from_holdings(holdings, "0001336528")
        assert meta["reg_name"] is None
        assert meta["net_assets"] is None
        assert meta["total_assets"] is None

    def test_form_type_nport(self):
        holdings = [self._make_holding(source="nport")]
        meta = _filing_meta_from_holdings(holdings, "0001096344")
        assert meta["form_type"] == "N-PORT"

    def test_form_type_13f(self):
        holdings = [self._make_holding(source="13f")]
        meta = _filing_meta_from_holdings(holdings, "0001336528")
        assert meta["form_type"] == "13F-HR"


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