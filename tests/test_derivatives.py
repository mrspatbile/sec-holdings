"""
tests/test_derivatives.py
--------------------------
Unit tests for the derivatives overlay loader and rolling contract logic.
No EDGAR calls. No file I/O except reading the fixture YAML.
"""

from __future__ import annotations

import pytest
from datetime import date
from pathlib import Path

from sec_holdings.derivatives import (
    DerivativesLoader,
    _parse_tenor,
    _parse_date,
    _parse_strike_type,
    _parse_strike_value,
)


# ------------------------------------------------------------------ #
# Fixtures                                                            #
# ------------------------------------------------------------------ #

FIXTURE_YAML = Path(__file__).parent / "fixtures" / "example_overlay.yaml"


class MockConfig:
    overlay_path = FIXTURE_YAML
    start_date = date(2020, 1, 1)


# ------------------------------------------------------------------ #
# Tenor parsing                                                       #
# ------------------------------------------------------------------ #

class TestParseTenor:
    def test_months(self):
        assert _parse_tenor("3M").months == 3

    def test_years(self):
        assert _parse_tenor("5Y").years == 5

    def test_weeks(self):
        assert _parse_tenor("1W").weeks == 1

    def test_case_insensitive(self):
        assert _parse_tenor("3m").months == 3
        assert _parse_tenor("5y").years == 5

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            _parse_tenor("3X")


# ------------------------------------------------------------------ #
# Strike parsing                                                      #
# ------------------------------------------------------------------ #

class TestParseStrike:
    def test_atm_type(self):
        assert _parse_strike_type("atm") == "atm"

    def test_otm_type(self):
        assert _parse_strike_type("otm_5pct") == "otm_5pct"

    def test_absolute_with_colon(self):
        assert _parse_strike_type("absolute:4500") == "absolute"
        assert _parse_strike_value("absolute:4500") == 4500.0

    def test_numeric_absolute(self):
        assert _parse_strike_type("4500") == "absolute"
        assert _parse_strike_value("4500") == 4500.0

    def test_atm_value_is_none(self):
        assert _parse_strike_value("atm") is None

    def test_otm_value_is_none(self):
        assert _parse_strike_value("otm_5pct") is None


# ------------------------------------------------------------------ #
# Date parsing                                                        #
# ------------------------------------------------------------------ #

class TestParseDate:
    def test_iso_string(self):
        assert _parse_date("2020-01-01") == date(2020, 1, 1)

    def test_date_object_passthrough(self):
        d = date(2020, 6, 15)
        assert _parse_date(d) == d

    def test_none_returns_none(self):
        assert _parse_date(None) is None


# ------------------------------------------------------------------ #
# Contract generation                                                 #
# ------------------------------------------------------------------ #

class TestContractGeneration:
    def setup_method(self):
        self.loader = DerivativesLoader(MockConfig())
        self.legs = self.loader.load()

    def test_loads_without_error(self):
        assert isinstance(self.legs, list)
        assert len(self.legs) > 0

    def test_all_legs_have_required_fields(self):
        required = {"instrument_id", "leg_id", "type", "open_date", "close_date",
                    "expiry_date", "status"}
        for leg in self.legs:
            assert required.issubset(leg.keys()), f"Missing fields in leg: {leg}"

    def test_close_date_before_expiry(self):
        for leg in self.legs:
            assert leg["close_date"] < leg["expiry_date"], (
                f"close_date {leg['close_date']} >= expiry_date {leg['expiry_date']}"
            )

    def test_open_date_before_close_date(self):
        for leg in self.legs:
            assert leg["open_date"] < leg["close_date"], (
                f"open_date {leg['open_date']} >= close_date {leg['close_date']}"
            )

    def test_legs_sorted_by_instrument_then_date(self):
        ids = [(l["instrument_id"], l["open_date"]) for l in self.legs]
        assert ids == sorted(ids)

    def test_roll_continuity(self):
        """Each leg opens on the close date of the previous leg."""
        by_instrument: dict[str, list[dict]] = {}
        for leg in self.legs:
            by_instrument.setdefault(leg["instrument_id"], []).append(leg)

        for instrument_id, legs in by_instrument.items():
            for i in range(1, len(legs)):
                assert legs[i]["open_date"] == legs[i - 1]["close_date"], (
                    f"Roll gap in {instrument_id}: "
                    f"leg {i} opens {legs[i]['open_date']} "
                    f"but leg {i-1} closes {legs[i-1]['close_date']}"
                )

    def test_only_last_leg_is_open(self):
        """All legs except the last per instrument should be closed."""
        by_instrument: dict[str, list[dict]] = {}
        for leg in self.legs:
            by_instrument.setdefault(leg["instrument_id"], []).append(leg)

        for instrument_id, legs in by_instrument.items():
            for leg in legs[:-1]:
                assert leg["status"] == "closed", (
                    f"Non-final leg is open in {instrument_id}: {leg}"
                )
            assert legs[-1]["status"] == "open"

    def test_market_rate_stored_as_none(self):
        """fixed_rate: market in YAML should be stored as None."""
        irs_legs = [l for l in self.legs if l["type"] == "interest_rate_swap"]
        if irs_legs:
            assert irs_legs[0]["fixed_rate"] is None

    def test_option_fields(self):
        option_legs = [l for l in self.legs if l["type"] == "option"]
        if option_legs:
            leg = option_legs[0]
            assert leg["put_call"] in ("put", "call")
            assert leg["underlying"] is not None
            assert leg["notional"] is not None


# ------------------------------------------------------------------ #
# contracts_active_on                                                 #
# ------------------------------------------------------------------ #

class TestContractsActiveOn:
    def setup_method(self):
        self.loader = DerivativesLoader(MockConfig())
        self.legs = self.loader.load()

    def test_returns_only_active_legs(self):
        target = date(2022, 6, 15)
        active = self.loader.contracts_active_on(target, self.legs)
        for leg in active:
            assert leg["open_date"] <= target.isoformat() < leg["close_date"]

    def test_closed_legs_excluded(self):
        closed = [l for l in self.legs if l["status"] == "closed"]
        if closed:
            # pick a date after all legs are closed
            last_close = max(l["close_date"] for l in closed)
            future = date(2099, 1, 1)
            active = self.loader.contracts_active_on(future, self.legs)
            # only the currently open leg should show
            assert all(l["status"] == "open" for l in active)

    def test_no_contracts_before_start(self):
        before_start = date(2019, 1, 1)
        active = self.loader.contracts_active_on(before_start, self.legs)
        assert active == []


# ------------------------------------------------------------------ #
# No overlay configured                                               #
# ------------------------------------------------------------------ #

class TestNoOverlay:
    def test_returns_empty_when_no_path(self):
        class NoOverlayConfig:
            overlay_path = None

        loader = DerivativesLoader(NoOverlayConfig())
        assert loader.load() == []

    def test_returns_empty_when_file_missing(self):
        class MissingConfig:
            overlay_path = Path("/tmp/does_not_exist.yaml")

        loader = DerivativesLoader(MissingConfig())
        assert loader.load() == []