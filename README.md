# sec-holdings

![Python](https://img.shields.io/badge/Python-3.13-blue)
![SQLite](https://img.shields.io/badge/Database-SQLite-003B57?logo=sqlite&logoColor=white)
![SEC](https://img.shields.io/badge/Data-SEC%20EDGAR-red)
![License](https://img.shields.io/badge/License-MIT-green)
![CI](https://github.com/mrspatbile/sec-holdings/actions/workflows/ci.yml/badge.svg)

Real fund positions from SEC EDGAR (N-PORT + 13F), enriched with daily prices and a configurable rolling derivatives overlay. Input layer for portfolio risk analysis.

---

## What this is

A data sourcing and portfolio construction layer that fetches real holdings from SEC EDGAR, enriches them with daily market prices, and allows overlaying rolling derivative positions defined in a YAML file. The combined portfolio is persisted to SQLite and consumed downstream by a pricing engine and a risk management framework.

This is not a risk system. It does not compute VaR, leverage, or exposure metrics. It builds the portfolio that those systems consume.

---

## What it connects to

```
sec-holdings              pricing project           manco-risk-mngmt
─────────────────         ──────────────────        ──────────────────────
N-PORT positions    →     price derivatives   →     VaR, leverage
13F positions             build curves              concentration
daily prices              compute Greeks            AIFM / UCITS metrics
rolling derivatives       delta equivalents         Annex IV / VI
```

---

## Data sources

| Source | Filing | Frequency | What you get |
|---|---|---|---|
| SEC EDGAR N-PORT | Mutual funds / ETFs | Monthly | Full portfolio: equities, bonds, weights, CUSIPs |
| SEC EDGAR 13F | Hedge funds | Quarterly | Long equity positions only |
| yfinance | All | Daily | Adjusted close prices per ticker |

Fetching is handled by [edgartools](https://github.com/dgunning/edgartools). No API key required.

---

## Rolling derivatives overlay

Defined in a YAML file. Additive only — the base SEC portfolio is never modified. Instruments roll automatically: the old contract is closed before expiry and a new one opened on the same date.

```yaml
# overlays/example_pershing.yaml

base:
  source: 13f
  cik: "0001336528"     # Pershing Square
  period: latest

rolling:
  - type: option
    put_call: put
    underlying: SPX
    notional: 50_000_000
    strike: atm
    tenor: 3M
    start_date: "2020-01-01"
    close_days_before_expiry: 7

  - type: interest_rate_swap
    pay_leg: fixed
    fixed_rate: market
    tenor: 5Y
    notional: 10_000_000
    currency: USD
    start_date: "2021-06-01"
    close_days_before_expiry: 30
```

Supported instrument types: `option`, `interest_rate_swap`, `fx_forward`, `cds`.

Rates and strikes resolve from market data at each roll date where possible. CDS spreads require manual input — no free data source exists.

---

## Known constraints

**No derivative positions from EDGAR.** N-PORT reports derivative notional and fair value at month-end only. No Greeks, no underlying exposure, no delta. 13F reports long equity only — no shorts, no bonds, no derivatives. Both are structural limitations of the SEC reporting rules, not of this project.

**Carry-forward assumption.** Positions are held constant between filing dates — monthly for N-PORT, quarterly for 13F. This is wrong in reality and is documented explicitly as a simplification. It is adequate for concentrated, low-turnover funds. It breaks down for high-frequency rebalancers.

**Equity and plain bond pricing only.** Derivative positions from the overlay are passed as instrument specs to the pricing project. This layer does not price them.

**Suitable funds.** Works well for equity long-only funds (Fairholme, Sequoia, Longleaf, Pershing Square) and plain vanilla bond funds. Not suitable for money market funds, derivatives-heavy strategies, or illiquid credit.

---

## Project structure

```
src/sec_holdings/
  config.py            Config dataclass, reads from .env
  fetcher.py           edgartools wrapper — N-PORT and 13F
  prices.py            yfinance daily prices per ticker
  derivatives.py       YAML overlay loader and rolling contract logic
  database.py          SQLite schema and persistence layer
  main.py              CLI entry point

overlays/              YAML overlay files — gitignored
tests/
  fixtures/            Static XML and YAML files for unit tests
  test_fetcher.py
  test_derivatives.py
  test_prices.py
```

---

## Database schema

| Table | Description |
|---|---|
| `filings` | One row per N-PORT or 13F filing |
| `holdings` | Point-in-time positions per filing |
| `daily_prices` | Daily adjusted close per ticker |
| `derivative_contracts` | Each individual contract leg with open/close dates |

---

## Example funds

| Fund | Type | CIK | Positions |
|---|---|---|---|
| Fairholme Fund (FAIRX) | N-PORT | 0001096344 | 5-15 |
| Sequoia Fund (SEQUX) | N-PORT | 0000089043 | ~25 |
| Longleaf Partners (LLPFX) | N-PORT | 0000806636 | ~20 |
| Pershing Square | 13F | 0001336528 | 8-12 |

---

## Getting started

```bash
git clone https://github.com/mrspatbile/sec-holdings
cd sec-holdings
python -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
```

Run against Pershing Square 13F:

```bash
python -m sec_holdings.main --cik 0001336528 --source 13f --overlay overlays/example_pershing.yaml
```

---

## Stack

- Python 3.13
- SQLite — lightweight persistence, no server required
- [edgartools](https://github.com/dgunning/edgartools) — SEC EDGAR N-PORT and 13F parsing
- yfinance — daily market prices
- PyYAML — overlay definition
- pandas / numpy — data handling
- Jupyter / JupyterLab — example notebooks

---

> Built by [Patricia Cruz](https://github.com/mrspatbile) — CFA, PhD Finance, Luxembourg