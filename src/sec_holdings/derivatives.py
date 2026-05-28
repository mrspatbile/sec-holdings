"""
derivatives.py
--------------
Loads a YAML derivatives overlay and generates a time series of
rolling contract legs from each instrument definition.

Overlay design
--------------
The YAML file defines instruments, not contracts. This module
turns each instrument definition into a sequence of individual
contract legs, each with a precise open date, close date, and
expiry date.

Rolling convention
------------------
Positions are closed before expiry (close_days_before_expiry) and
a new contract is opened on the same date. This mirrors standard
practice -- contracts are rarely held to expiry.

Example: 3M SPX put, close 7 days before expiry, starting 2020-01-01

    Leg 1: open 2020-01-01  close 2020-03-25  expiry 2020-04-01
    Leg 2: open 2020-03-25  close 2020-06-17  expiry 2020-06-24
    Leg 3: open 2020-06-17  ...               expiry ...

Market rates
------------
Where fixed_rate or spread_bps is set to 'market' in the YAML,
the field is stored as None in the contract dict. The pricing
project is responsible for resolving market rates at each
open_date using the yield curves it builds.

CDS spreads are always provided explicitly in the YAML -- there
is no free market data source for CDS spreads.

Supported instrument types
--------------------------
    option              Put or call on any underlying
    interest_rate_swap  Vanilla fixed-float IRS
    fx_forward          FX outright forward
    cds                 Credit default swap

Contract schema
---------------
Each contract leg is returned as a dict with:

    instrument_id       str     Unique ID from YAML (auto-assigned if absent)
    leg_id              int     Sequential leg number for this instrument
    type                str     Instrument type
    put_call            str     'put' or 'call' (options only)
    underlying          str     Underlying ticker or index
    underlying_type     str     'equity_index', 'single_stock', etc.
    notional            float   Notional in USD
    strike_type         str     'atm', 'otm_Xpct', 'absolute'
    strike_value        float   Only set when strike_type is 'absolute'
    tenor               str     Original tenor string e.g. '3M', '5Y'
    currency            str     Currency (IRS and FX forward)
    pay_leg             str     'fixed' or 'float' (IRS only)
    fixed_rate          float   Fixed rate in percent, or None if market
    spread_bps          float   CDS spread in bps, or None if market
    pair                str     Currency pair e.g. 'EURUSD' (FX forward)
    direction           str     'buy' or 'sell' (FX forward)
    open_date           str     Date this leg was opened (YYYY-MM-DD)
    close_date          str     Date this leg was / will be closed
    expiry_date         str     Contractual expiry date
    status              str     'open' or 'closed'
    close_days_before_expiry  int
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, timedelta
from dateutil.relativedelta import relativedelta
from pathlib import Path
from typing import Optional

import yaml

from sec_holdings.config import Config

log = logging.getLogger(__name__)

_SUPPORTED_TYPES = frozenset({"option", "interest_rate_swap", "fx_forward", "cds"})


class DerivativesLoader:
    """
    Loads a YAML overlay file and generates rolling contract time series.

    Parameters
    ----------
    config : Config
        Runtime configuration. overlay_path must be set.
    """

    def __init__(self, config: Config) -> None:
        self.config = config

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def load(self) -> list[dict]:
        """
        Load the overlay YAML and generate all contract legs.

        Returns an empty list if overlay_path is None or the file
        defines no rolling instruments.

        Returns
        -------
        list[dict]
            All contract legs, sorted by instrument_id then open_date.
        """
        if self.config.overlay_path is None:
            log.info("No overlay path configured -- skipping derivatives")
            return []

        path = Path(self.config.overlay_path)
        if not path.exists():
            log.warning("Overlay file not found: %s", path)
            return []

        with open(path) as f:
            data = yaml.safe_load(f)

        instruments = (data or {}).get("rolling", [])
        if not instruments:
            log.info("Overlay file has no rolling instruments: %s", path)
            return []

        all_legs: list[dict] = []
        for i, spec in enumerate(instruments):
            instrument_id = spec.get("id") or f"instr_{i+1:03d}"
            try:
                legs = self._generate_legs(spec, instrument_id)
                all_legs.extend(legs)
                log.info(
                    "Instrument %s (%s): %d contract legs generated",
                    instrument_id, spec.get("type"), len(legs),
                )
            except Exception as exc:
                log.error("Failed to generate legs for instrument %s: %s", instrument_id, exc)
                continue

        all_legs.sort(key=lambda x: (x["instrument_id"], x["open_date"]))
        return all_legs

    def contracts_active_on(self, target_date: date, legs: list[dict]) -> list[dict]:
        """
        Filter contract legs to those active on a given date.

        A contract is active on date D when:
            open_date <= D < close_date

        Parameters
        ----------
        target_date : date
            The date to filter for.
        legs : list[dict]
            Full list of legs as returned by load().

        Returns
        -------
        list[dict]
            Contract legs active on target_date.
        """
        d = target_date.isoformat()
        return [
            leg for leg in legs
            if leg["open_date"] <= d < leg["close_date"]
        ]

    # ------------------------------------------------------------------ #
    # Contract generation                                                  #
    # ------------------------------------------------------------------ #

    def _generate_legs(self, spec: dict, instrument_id: str) -> list[dict]:
        """Generate all rolling legs for a single instrument definition."""
        instr_type = spec.get("type", "").lower()
        if instr_type not in _SUPPORTED_TYPES:
            raise ValueError(
                f"Unsupported instrument type '{instr_type}'. "
                f"Supported: {sorted(_SUPPORTED_TYPES)}"
            )

        start_date = _parse_date(spec.get("start_date"))
        if start_date is None:
            raise ValueError(f"Instrument {instrument_id} is missing start_date")

        tenor = spec.get("tenor")
        if not tenor:
            raise ValueError(f"Instrument {instrument_id} is missing tenor")

        close_days = int(spec.get("close_days_before_expiry", 7))
        today = date.today()

        legs: list[dict] = []
        leg_num = 1
        open_date = start_date

        while open_date <= today:
            expiry_date = open_date + _parse_tenor(tenor)
            close_date = expiry_date - timedelta(days=close_days)

            # close_date must be after open_date
            if close_date <= open_date:
                close_date = open_date + timedelta(days=1)

            status = "closed" if close_date <= today else "open"

            leg = self._build_leg(
                spec=spec,
                instrument_id=instrument_id,
                leg_num=leg_num,
                open_date=open_date,
                close_date=close_date,
                expiry_date=expiry_date,
                status=status,
            )
            legs.append(leg)

            if status == "open":
                break

            # Roll: next leg opens on the close date of this one
            open_date = close_date
            leg_num += 1

        return legs

    def _build_leg(
        self,
        spec: dict,
        instrument_id: str,
        leg_num: int,
        open_date: date,
        close_date: date,
        expiry_date: date,
        status: str,
    ) -> dict:
        """Assemble a single contract leg dict from the instrument spec."""
        instr_type = spec.get("type", "").lower()

        # Fixed rate: None means the pricing project resolves from curves
        fixed_rate_raw = spec.get("fixed_rate")
        fixed_rate = (
            None
            if fixed_rate_raw in (None, "market")
            else float(fixed_rate_raw)
        )

        # CDS spread: always explicit, no market resolution
        spread_raw = spec.get("spread_bps")
        spread_bps = None if spread_raw in (None, "market") else float(spread_raw)

        return {
            "instrument_id": instrument_id,
            "leg_id": leg_num,
            "type": instr_type,
            "put_call": spec.get("put_call"),
            "underlying": spec.get("underlying"),
            "underlying_type": spec.get("underlying_type"),
            "notional": float(spec["notional"]) if spec.get("notional") else None,
            "strike_type": _parse_strike_type(spec.get("strike")),
            "strike_value": _parse_strike_value(spec.get("strike")),
            "tenor": spec.get("tenor"),
            "currency": spec.get("currency", "USD"),
            "pay_leg": spec.get("pay_leg"),
            "fixed_rate": fixed_rate,
            "spread_bps": spread_bps,
            "pair": spec.get("pair"),
            "direction": spec.get("direction"),
            "open_date": open_date.isoformat(),
            "close_date": close_date.isoformat(),
            "expiry_date": expiry_date.isoformat(),
            "status": status,
            "close_days_before_expiry": int(spec.get("close_days_before_expiry", 7)),
        }


# ------------------------------------------------------------------ #
# Tenor and date helpers                                              #
# ------------------------------------------------------------------ #

def _parse_tenor(tenor: str) -> relativedelta:
    """
    Parse a tenor string into a relativedelta.

    Parameters
    ----------
    tenor : str
        Tenor string. Supported formats: '3M', '6M', '1Y', '5Y', '1W'.
        M = months, Y = years, W = weeks, D = days.

    Raises
    ------
    ValueError
        If the tenor string cannot be parsed.
    """
    tenor = tenor.strip().upper()
    if tenor.endswith("M"):
        return relativedelta(months=int(tenor[:-1]))
    if tenor.endswith("Y"):
        return relativedelta(years=int(tenor[:-1]))
    if tenor.endswith("W"):
        return relativedelta(weeks=int(tenor[:-1]))
    if tenor.endswith("D"):
        return relativedelta(days=int(tenor[:-1]))
    raise ValueError(f"Cannot parse tenor '{tenor}'. Use e.g. '3M', '5Y', '1W'.")


def _parse_date(value) -> Optional[date]:
    """Parse a YAML date value to a Python date object."""
    if value is None:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _parse_strike_type(strike) -> Optional[str]:
    """
    Normalise the strike field to a type string.

    Examples
    --------
    'atm'        -> 'atm'
    'otm_5pct'   -> 'otm_5pct'
    'absolute:4500' -> 'absolute'
    4500         -> 'absolute'
    """
    if strike is None:
        return None
    s = str(strike).strip().lower()
    if s == "atm":
        return "atm"
    if s.startswith("otm_"):
        return s
    if s.startswith("absolute:"):
        return "absolute"
    try:
        float(s)
        return "absolute"
    except ValueError:
        return s


def _parse_strike_value(strike) -> Optional[float]:
    """
    Extract the numeric strike value if strike_type is 'absolute'.
    Returns None for 'atm' and relative strikes.
    """
    if strike is None:
        return None
    s = str(strike).strip().lower()
    if s.startswith("absolute:"):
        try:
            return float(s.split(":")[1])
        except (IndexError, ValueError):
            return None
    try:
        return float(s)
    except ValueError:
        return None