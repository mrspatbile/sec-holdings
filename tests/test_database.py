"""
tests/test_database.py
----------------------
Unit tests for the Database persistence layer.
Uses an in-memory SQLite DB -- no file I/O.

What is tested
--------------
- Schema creation
- filing_exists / insert_filing
- insert_holdings / get_holdings_for_period / get_holdings_for_period_and_cik
- insert_prices / get_prices_for_ticker
- insert_contracts / get_active_contracts_on / get_all_contracts
- get_all_periods / get_all_periods_for_cik
- get_latest_filing_date
- get_stale_tickers
- get_position_history
"""

from __future__ import annotations

import pytest
from datetime import date
from pathlib import Path

from sec_holdings.database import Database


# ------------------------------------------------------------------ #
# Fixtures                                                            #
# ------------------------------------------------------------------ #

@pytest.fixture
def db():
    """In-memory SQLite DB, fresh for each test."""
    with Database(Path(":memory:")) as database:
        yield database


def _filing(cik="0001336528", accession="ACC-001", source="13f",
            period="2024-03-31", filing_date="2024-05-15",
            reg_name="Test Fund", net_assets=1_000_000.0,
            total_assets=1_100_000.0) -> dict:
    return {
        "source": source,
        "cik": cik,
        "accession": accession,
        "form_type": "13F-HR",
        "filing_date": filing_date,
        "period_of_report": period,
        "reg_name": reg_name,
        "series_name": None,
        "net_assets": net_assets,
        "total_assets": total_assets,
    }


def _holding(filing_id: int, ticker="AAPL", name="Apple Inc",
             cusip="037833100", value_usd=150_000.0, pct_val=15.0,
             period="2024-03-31", source="13f",
             accession="ACC-001") -> dict:
    return {
        "name": name, "cusip": cusip, "isin": None,
        "ticker": ticker, "value_usd": value_usd, "pct_val": pct_val,
        "shares": 1000.0, "units": "SH", "asset_category": "EC",
        "country": "US", "payoff_profile": "Long",
        "maturity_date": None, "coupon_rate": None,
        "source": source, "period": period,
        "filing_date": "2024-05-15", "accession": accession,
    }


def _price(ticker="AAPL", date_str="2024-01-02",
           adj_close=185.0, volume=22_000_000.0) -> dict:
    return {"ticker": ticker, "date": date_str,
            "adj_close": adj_close, "volume": volume}


def _contract(instrument_id="instr_001", leg_id=1,
              open_date="2024-01-01", close_date="2024-03-25",
              expiry_date="2024-04-01", status="closed") -> dict:
    return {
        "instrument_id": instrument_id, "leg_id": leg_id,
        "type": "option", "put_call": "put",
        "underlying": "SPX", "underlying_type": "equity_index",
        "notional": 50_000_000.0, "strike_type": "atm",
        "strike_value": None, "tenor": "3M", "currency": "USD",
        "pay_leg": None, "fixed_rate": None, "spread_bps": None,
        "pair": None, "direction": None,
        "open_date": open_date, "close_date": close_date,
        "expiry_date": expiry_date, "status": status,
        "close_days_before_expiry": 7,
    }


# ------------------------------------------------------------------ #
# Schema                                                              #
# ------------------------------------------------------------------ #

class TestSchema:
    def test_tables_created(self, db):
        cur = db._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = {r[0] for r in cur.fetchall()}
        assert {"filings", "holdings", "daily_prices", "derivative_contracts"}.issubset(tables)


# ------------------------------------------------------------------ #
# Filings                                                             #
# ------------------------------------------------------------------ #

class TestFilings:
    def test_insert_returns_id(self, db):
        fid = db.insert_filing(_filing())
        assert isinstance(fid, int)
        assert fid > 0

    def test_filing_exists_true(self, db):
        db.insert_filing(_filing(accession="ACC-001"))
        assert db.filing_exists("ACC-001")

    def test_filing_exists_false(self, db):
        assert not db.filing_exists("NOT-THERE")

    def test_insert_or_ignore_duplicate(self, db):
        id1 = db.insert_filing(_filing(accession="ACC-001"))
        id2 = db.insert_filing(_filing(accession="ACC-001"))
        assert id1 == id2

    def test_reg_name_stored(self, db):
        db.insert_filing(_filing(reg_name="Pershing Square Capital"))
        cur = db._conn.execute("SELECT reg_name FROM filings LIMIT 1")
        assert cur.fetchone()[0] == "Pershing Square Capital"

    def test_net_assets_stored(self, db):
        db.insert_filing(_filing(net_assets=1_451_766_475.80))
        cur = db._conn.execute("SELECT net_assets FROM filings LIMIT 1")
        assert cur.fetchone()[0] == pytest.approx(1_451_766_475.80)

    def test_total_assets_stored(self, db):
        db.insert_filing(_filing(total_assets=1_453_094_324.22))
        cur = db._conn.execute("SELECT total_assets FROM filings LIMIT 1")
        assert cur.fetchone()[0] == pytest.approx(1_453_094_324.22)

    def test_get_latest_filing_date(self, db):
        db.insert_filing(_filing(accession="ACC-001", filing_date="2024-02-15"))
        db.insert_filing(_filing(accession="ACC-002", filing_date="2024-05-15"))
        assert db.get_latest_filing_date("0001336528") == "2024-05-15"

    def test_get_latest_filing_date_no_filings(self, db):
        assert db.get_latest_filing_date("0001336528") is None

    def test_get_latest_filing_date_wrong_cik(self, db):
        db.insert_filing(_filing(cik="0001336528", accession="ACC-001"))
        assert db.get_latest_filing_date("9999999999") is None


# ------------------------------------------------------------------ #
# Holdings                                                            #
# ------------------------------------------------------------------ #

class TestHoldings:
    def test_insert_and_retrieve(self, db):
        fid = db.insert_filing(_filing())
        db.insert_holdings([_holding(fid)], fid)
        holdings = db.get_holdings_for_period("2024-03-31")
        assert len(holdings) == 1
        assert holdings[0]["name"] == "Apple Inc"

    def test_get_holdings_for_period_and_cik(self, db):
        fid1 = db.insert_filing(_filing(cik="0001336528", accession="ACC-001"))
        fid2 = db.insert_filing(_filing(cik="0001096344", accession="ACC-002"))
        db.insert_holdings([_holding(fid1, ticker="AMZN")], fid1)
        db.insert_holdings([_holding(fid2, ticker="JOE")], fid2)

        result = db.get_holdings_for_period_and_cik("2024-03-31", "0001336528")
        assert len(result) == 1
        assert result[0]["ticker"] == "AMZN"

    def test_sorted_by_pct_val_desc(self, db):
        fid = db.insert_filing(_filing())
        db.insert_holdings([
            _holding(fid, ticker="AAPL", pct_val=30.0),
            _holding(fid, ticker="MSFT", pct_val=10.0),
            _holding(fid, ticker="AMZN", pct_val=20.0),
        ], fid)
        holdings = db.get_holdings_for_period("2024-03-31")
        pcts = [h["pct_val"] for h in holdings]
        assert pcts == sorted(pcts, reverse=True)

    def test_get_position_history(self, db):
        fid1 = db.insert_filing(_filing(accession="ACC-001", period="2023-12-31"))
        fid2 = db.insert_filing(_filing(accession="ACC-002", period="2024-03-31"))
        db.insert_holdings([_holding(fid1, period="2023-12-31")], fid1)
        db.insert_holdings([_holding(fid2, period="2024-03-31")], fid2)

        history = db.get_position_history("AAPL")
        assert len(history) == 2
        assert history[0]["period_of_report"] < history[1]["period_of_report"]


# ------------------------------------------------------------------ #
# Periods                                                             #
# ------------------------------------------------------------------ #

class TestPeriods:
    def test_get_all_periods(self, db):
        db.insert_filing(_filing(accession="ACC-001", period="2023-12-31"))
        db.insert_filing(_filing(accession="ACC-002", period="2024-03-31"))
        periods = db.get_all_periods()
        assert periods == ["2023-12-31", "2024-03-31"]

    def test_get_all_periods_for_cik(self, db):
        db.insert_filing(_filing(cik="0001336528", accession="ACC-001", period="2024-03-31"))
        db.insert_filing(_filing(cik="0001096344", accession="ACC-002", period="2024-02-28"))
        periods = db.get_all_periods_for_cik("0001336528")
        assert periods == ["2024-03-31"]
        assert "2024-02-28" not in periods


# ------------------------------------------------------------------ #
# Prices                                                              #
# ------------------------------------------------------------------ #

class TestPrices:
    def test_insert_and_retrieve(self, db):
        db.insert_prices([_price("AAPL", "2024-01-02", 185.0)])
        prices = db.get_prices_for_ticker("AAPL")
        assert len(prices) == 1
        assert prices[0]["adj_close"] == pytest.approx(185.0)

    def test_insert_or_ignore_duplicate(self, db):
        db.insert_prices([_price("AAPL", "2024-01-02", 185.0)])
        db.insert_prices([_price("AAPL", "2024-01-02", 190.0)])
        prices = db.get_prices_for_ticker("AAPL")
        assert len(prices) == 1
        assert prices[0]["adj_close"] == pytest.approx(185.0)

    def test_sorted_by_date(self, db):
        db.insert_prices([
            _price("AAPL", "2024-01-03", 186.0),
            _price("AAPL", "2024-01-02", 185.0),
        ])
        prices = db.get_prices_for_ticker("AAPL")
        assert prices[0]["date"] < prices[1]["date"]

    def test_empty_for_unknown_ticker(self, db):
        assert db.get_prices_for_ticker("UNKNOWN") == []


# ------------------------------------------------------------------ #
# get_stale_tickers                                                   #
# ------------------------------------------------------------------ #

class TestGetStaleTickers:
    def test_ticker_not_in_db_is_stale(self, db):
        stale = db.get_stale_tickers(["AAPL"], "2024-01-10")
        assert "AAPL" in stale
        assert stale["AAPL"] is None

    def test_ticker_with_old_prices_is_stale(self, db):
        db.insert_prices([_price("AAPL", "2024-01-05", 185.0)])
        stale = db.get_stale_tickers(["AAPL"], "2024-01-10")
        assert "AAPL" in stale
        assert stale["AAPL"] == "2024-01-05"

    def test_ticker_with_fresh_prices_not_stale(self, db):
        db.insert_prices([_price("AAPL", "2024-01-10", 185.0)])
        stale = db.get_stale_tickers(["AAPL"], "2024-01-10")
        assert "AAPL" not in stale

    def test_mixed_tickers(self, db):
        db.insert_prices([_price("AAPL", "2024-01-10", 185.0)])
        stale = db.get_stale_tickers(["AAPL", "MSFT"], "2024-01-10")
        assert "AAPL" not in stale
        assert "MSFT" in stale

    def test_empty_tickers_returns_empty(self, db):
        assert db.get_stale_tickers([], "2024-01-10") == {}


# ------------------------------------------------------------------ #
# Derivative contracts                                                #
# ------------------------------------------------------------------ #

class TestDerivativeContracts:
    def test_insert_and_retrieve_all(self, db):
        db.insert_contracts([_contract()])
        contracts = db.get_all_contracts()
        assert len(contracts) == 1
        assert contracts[0]["instrument_id"] == "instr_001"

    def test_insert_or_ignore_duplicate(self, db):
        db.insert_contracts([_contract(instrument_id="X", leg_id=1)])
        db.insert_contracts([_contract(instrument_id="X", leg_id=1)])
        assert len(db.get_all_contracts()) == 1

    def test_get_active_contracts_on(self, db):
        db.insert_contracts([
            _contract(instrument_id="A", leg_id=1,
                      open_date="2024-01-01", close_date="2024-03-25",
                      expiry_date="2024-04-01", status="closed"),
            _contract(instrument_id="A", leg_id=2,
                      open_date="2024-03-25", close_date="2024-06-18",
                      expiry_date="2024-06-25", status="open"),
        ])
        active = db.get_active_contracts_on("2024-04-15")
        assert len(active) == 1
        assert active[0]["leg_id"] == 2

    def test_no_active_contracts_before_start(self, db):
        db.insert_contracts([_contract(open_date="2024-01-01",
                                       close_date="2024-03-25",
                                       expiry_date="2024-04-01")])
        active = db.get_active_contracts_on("2020-01-01")
        assert active == []

    def test_no_active_contracts_after_close(self, db):
        db.insert_contracts([_contract(open_date="2024-01-01",
                                       close_date="2024-03-25",
                                       expiry_date="2024-04-01",
                                       status="closed")])
        active = db.get_active_contracts_on("2024-04-01")
        assert active == []

class TestUpdatePricingStatus:
    def test_sets_priced(self, db):
        fid = db.insert_filing(_filing())
        db.insert_holdings([_holding(fid, ticker="AAPL")], fid)
        db.update_pricing_status({"AAPL": "priced"})
        cur = db._conn.execute("SELECT pricing_status FROM holdings WHERE ticker = 'AAPL'")
        assert cur.fetchone()[0] == "priced"

    def test_sets_excluded_no_prices(self, db):
        fid = db.insert_filing(_filing())
        db.insert_holdings([_holding(fid, ticker="HHC")], fid)
        db.update_pricing_status({"HHC": "excluded_no_prices"})
        cur = db._conn.execute("SELECT pricing_status FROM holdings WHERE ticker = 'HHC'")
        assert cur.fetchone()[0] == "excluded_no_prices"

    def test_sets_partial(self, db):
        fid = db.insert_filing(_filing())
        db.insert_holdings([_holding(fid, ticker="SEGRT")], fid)
        db.update_pricing_status({"SEGRT": "partial"})
        cur = db._conn.execute("SELECT pricing_status FROM holdings WHERE ticker = 'SEGRT'")
        assert cur.fetchone()[0] == "partial"

    def test_sets_excluded_no_ticker(self, db):
        fid = db.insert_filing(_filing())
        db.insert_holdings([_holding(fid, ticker=None, name="US Treasury Bill")], fid)
        db.update_pricing_status({})
        cur = db._conn.execute(
            "SELECT pricing_status FROM holdings WHERE ticker IS NULL"
        )
        assert cur.fetchone()[0] == "excluded_no_ticker"

    def test_does_not_overwrite_existing_status(self, db):
        fid = db.insert_filing(_filing())
        db.insert_holdings([_holding(fid, ticker="AAPL")], fid)
        db.update_pricing_status({"AAPL": "priced"})
        db.update_pricing_status({})
        cur = db._conn.execute("SELECT pricing_status FROM holdings WHERE ticker = 'AAPL'")
        assert cur.fetchone()[0] == "priced"