"""
fetcher.py
----------
Wraps edgartools to fetch N-PORT and 13F filings from SEC EDGAR.
Returns normalised holdings dicts regardless of filing type.

All EDGAR HTTP, rate limiting, XML parsing, and pagination is
handled by edgartools. This module normalises and structures the
output for persistence.

Normalised holding schema
-------------------------
Both N-PORT and 13F positions are returned as dicts with the
following fields. Fields not available for a given filing type
are set to None.

    name            str     Security name
    cusip           str     CUSIP identifier
    isin            str     ISIN identifier (N-PORT only)
    ticker          str     Ticker symbol
    value_usd       float   Fair value in USD
    pct_val         float   Percentage of net assets (N-PORT) or
                            percentage of total portfolio value (13F)
    shares          float   Number of shares / principal amount
    units           str     'SH' (shares) or 'PRN' (principal amount)
    asset_category  str     Asset class code (N-PORT only)
                            EC=equity, DBT=debt, RC=registered inv. co.
    country         str     Country of investment (N-PORT only)
    payoff_profile  str     'Long' or 'Short' (N-PORT only)
    maturity_date   str     Debt maturity date (N-PORT only)
    coupon_rate     float   Annualised coupon rate in percent (N-PORT only)
    source          str     'nport' or '13f'
    period          str     Reporting period end date (YYYY-MM-DD)
    filing_date     str     Date filed with SEC (YYYY-MM-DD)
    accession       str     EDGAR accession number
"""

from __future__ import annotations

import logging
from typing import Optional

import edgar
import pandas as pd
from edgar import Company

from sec_holdings.config import Config

log = logging.getLogger(__name__)


class Fetcher:
    """
    Fetches and normalises fund holdings from SEC EDGAR via edgartools.

    Parameters
    ----------
    config : Config
        Runtime configuration. source determines which filing type
        is fetched ('nport' or '13f').
    """

    def __init__(self, config: Config) -> None:
        self.config = config
        self._company: Optional[Company] = None
        edgar.set_identity(config.user_agent)   


    @property
    def company(self) -> Company:
        """Lazy-load the edgartools Company object."""
        if self._company is None:
            self._company = Company(self.config.cik)
        return self._company

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def fetch(self) -> list[dict]:
        """
        Fetch all holdings within the configured date window.

        Returns a flat list of normalised holding dicts, one per
        position per filing. Multiple filings produce multiple rows
        for the same security with different period dates.

        Returns
        -------
        list[dict]
            Normalised holdings sorted by period ascending.
        """
        if self.config.source == "nport":
            return self._fetch_nport()
        return self._fetch_13f()

    def fetch_filings_metadata(self) -> list[dict]:
        """
        Return filing-level metadata only (no position detail).
        Useful for checking what is available before a full fetch.

        Returns
        -------
        list[dict]
            Each dict has: accession, filing_date, period, form_type.
        """
        if self.config.source == "nport":
            form = "N-PORT"
        else:
            form = "13F-HR"

        filings = self.company.get_filings(form=form)
        cutoff = self.config.start_date.isoformat()

        result = []
        for f in filings:
            if f.filing_date < cutoff:
                continue
            result.append({
                "accession": f.accession_no,
                "filing_date": str(f.filing_date),
                "period": str(f.period_of_report),
                "form_type": f.form,
            })

        result.sort(key=lambda x: x["period"])
        log.info(
            "%d %s filings found for CIK %s from %s",
            len(result), form, self.config.cik, cutoff,
        )
        return result

    # ------------------------------------------------------------------ #
    # N-PORT                                                               #
    # ------------------------------------------------------------------ #

    def _fetch_nport(self) -> list[dict]:
        filings = self.company.get_filings(form="NPORT-P")
        cutoff = self.config.start_date.isoformat()
        holdings = []

        for filing in filings:
            if str(filing.filing_date) < cutoff:
                continue
            try:
                obj = filing.obj()
            except Exception as exc:
                log.warning("Could not parse NPORT-P %s: %s", filing.accession_no, exc)
                continue

            period = str(filing.period_of_report)
            filing_date = str(filing.filing_date)
            accession = filing.accession_no

            try:
                df = obj.investment_data()
            except Exception as exc:
                log.warning("No investment_data in %s: %s", accession, exc)
                continue

            if df is None or df.empty:
                log.warning("Empty investment_data in %s", accession)
                continue

            total_value = df["value_usd"].sum() if "value_usd" in df.columns else None

            for _, row in df.iterrows():
                holdings.append(
                    self._normalise_nport_row(
                        row, period, filing_date, accession, total_value
                    )
                )

            log.info("NPORT-P %s: %d positions (period %s)", accession, len(df), period)

        holdings.sort(key=lambda x: x["period"])
        return holdings

    def _normalise_nport_row(
        self,
        row: pd.Series,
        period: str,
        filing_date: str,
        accession: str,
        total_value: Optional[float],
    ) -> dict:
        value_usd = _safe_float(row.get("value_usd"))
        pct_val = (
            _safe_float(row.get("pct_value"))
            or (value_usd / total_value * 100 if value_usd and total_value else None)
        )

        return {
            "name": _safe_str(row.get("name")),
            "cusip": _safe_str(row.get("cusip")),
            "isin": _safe_str(row.get("isin")),
            "ticker": _safe_str(row.get("ticker")),
            "value_usd": value_usd,
            "pct_val": pct_val,
            "shares": _safe_float(row.get("balance")),
            "units": _safe_str(row.get("units")),
            "asset_category": _safe_str(row.get("asset_category")),
            "country": _safe_str(row.get("investment_country")),
            "payoff_profile": _safe_str(row.get("payoff_profile")),
            "maturity_date": _safe_str(row.get("maturity_date")),
            "coupon_rate": _safe_float(row.get("annualized_rate")),
            "source": "nport",
            "period": period,
            "filing_date": filing_date,
            "accession": accession,
        }

    # ------------------------------------------------------------------ #
    # 13F                                                                  #
    # ------------------------------------------------------------------ #

    def _fetch_13f(self) -> list[dict]:
        """Fetch and normalise all 13F-HR filings in the date window."""
        filings = self.company.get_filings(form="13F-HR")
        cutoff = self.config.start_date.isoformat()
        holdings = []

        for filing in filings:
            if str(filing.filing_date) < cutoff:
                continue

            try:
                obj = filing.obj()
            except Exception as exc:
                log.warning("Could not parse 13F %s: %s", filing.accession_no, exc)
                continue

            period = str(filing.period_of_report)
            filing_date = str(filing.filing_date)
            accession = filing.accession_no

            try:
                df: pd.DataFrame = obj.holdings

            except Exception as exc:
                log.warning("No holdings in %s: %s", accession, exc)
                continue

            if df is None or df.empty:
                log.warning("Empty holdings in %s", accession)
                continue

            total_value = df["Value"].sum() if "Value" in df.columns else None

            for _, row in df.iterrows():
                holdings.append(
                    self._normalise_13f_row(
                        row, period, filing_date, accession, total_value
                    )
                )
            
            log.info("13F %s: %d positions (period %s)", accession, len(df), period)

        holdings.sort(key=lambda x: x["period"])
        return holdings

    def _normalise_13f_row(
        self,
        row: pd.Series,
        period: str,
        filing_date: str,
        accession: str,
        total_value: Optional[float],
    ) -> dict:
        value_usd = _safe_float(row.get("Value"))
        pct_val = (
            value_usd / total_value * 100
            if value_usd and total_value
            else None
        )

        return {
            "name": _safe_str(row.get("Issuer")),
            "cusip": _safe_str(row.get("Cusip")),
            "isin": None,
            "ticker": _safe_str(row.get("Ticker")),
            "value_usd": value_usd,
            "pct_val": pct_val,
            "shares": _safe_float(row.get("SharesPrnAmount")),
            "units": _safe_str(row.get("Type")),
            "asset_category": None,
            "country": None,
            "payoff_profile": "Long",
            "maturity_date": None,
            "coupon_rate": None,
            "source": "13f",
            "period": period,
            "filing_date": filing_date,
            "accession": accession,
        }


# ------------------------------------------------------------------ #
# Helpers                                                             #
# ------------------------------------------------------------------ #

def _safe_str(value) -> Optional[str]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    return str(value).strip() or None


def _safe_float(value) -> Optional[float]:
    
    if value is None:
        return None
    try:
        f = float(value)
        return None if pd.isna(f) else f
    except (TypeError, ValueError):
        return None