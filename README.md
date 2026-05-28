# sec-holdings

![Python](https://img.shields.io/badge/Python-3.13-blue)
![SQLite](https://img.shields.io/badge/Database-SQLite-003B57?logo=sqlite&logoColor=white)
![SEC](https://img.shields.io/badge/Data-SEC%20EDGAR-red)
![License](https://img.shields.io/badge/License-MIT-green)
![CI](https://github.com/mrspatbile/sec-holdings/actions/workflows/ci.yml/badge.svg)

Real fund positions from SEC EDGAR (N-PORT + 13F), enriched with daily prices and a configurable rolling derivatives overlay. Input layer for portfolio risk analysis.

---

## What this is

A tool for obtaining real long equity and bond positions from SEC public filings, enriching them with daily market prices, and persisting the result to a clean SQLite database for use in a pricing engine and a risk management framework.

N-PORT filings (mutual funds, ETFs) provide a complete monthly portfolio snapshot — positions, weights, NAV, and enough data to compute a cash residual. 13F filings (hedge funds) provide quarterly long equity positions only — no NAV, no cash, no shorts, no derivatives. These two sources have different scopes and are used differently downstream.

This is not a reconstruction of what these funds actually hold at any given moment. It is a structured baseline — carry-forward positions from public filings, priced daily, with explicit flags for what can and cannot be priced.

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

| Source | Filing | Frequency | What you get | Scope |
|---|---|---|---|---|
| SEC EDGAR N-PORT | Mutual funds / ETFs | Monthly | Full portfolio: equities, bonds, weights, CUSIPs, NAV | Complete portfolio |
| SEC EDGAR 13F | Hedge funds | Quarterly | Long equity positions only | Position tracker |
| yfinance | All | Daily | Adjusted close prices per ticker | Prices only |

Fetching is handled by [edgartools](https://github.com/dgunning/edgartools). No API key required.

---

## Rolling derivatives overlay

The overlay is experimental. It adds instrument specs to the DB for downstream consumption by the pricing project. It does not compute fair values, does not affect cash, and is not part of the core N-PORT or 13F workflow. Use it for scenario construction, not for production portfolio modeling.

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

**No derivative positions from EDGAR.**
N-PORT reports derivative notional and fair value at month-end only — no Greeks, no delta, no underlying exposure. 13F reports long equity only — no shorts, no bonds, no derivatives. Both are structural limitations of SEC reporting rules.
What this project does about it: a YAML overlay allows adding rolling derivative instrument specs on top of the base portfolio for experimental scenario construction. This is not part of the core workflow and does not affect cash or portfolio-level metrics.

**Carry-forward assumption.**
Positions are frozen between filing dates — monthly for N-PORT, quarterly for 13F. This is wrong in reality and intentional by design. The honest baseline is: we know what the fund held at each reporting date, and we assume it did not change until the next one.
What this project does about it: daily prices from yfinance are applied to the carry-forward positions, giving a daily portfolio valuation series for each period. This is adequate for concentrated, low-turnover funds. It breaks down for high-frequency rebalancers.

**Positions without prices.**
Not all securities in a filing have a resolvable yfinance ticker. Bonds, private securities, foreign names not covered by yfinance, and delisted stocks will have no price series. A broken price series is worse than a missing position — it silently distorts every downstream metric.
What this project does about it: positions are flagged with a `pricing_status` field — `priced`, `excluded` (no price available), or `partial` (delisted mid-period). Downstream projects filter on `pricing_status = 'priced'` to ensure clean time series.

**Objective of this project.**
This is not a tool to reconstruct exactly what these funds hold. It is a tool to obtain real long equity and bond positions from public filings, enrich them with daily market prices. The output is a clean, queryable portfolio dataset for use in a pricing engine and a risk management framework. Completeness is not the goal. Consistency and explicitness about what is and is not included are.

**Equity and plain bond pricing only.**
Derivative positions from the overlay are passed as instrument specs to the pricing project. This layer does not price them.

**Suitable funds.**
Works well for concentrated, low-turnover equity funds (Fairholme, Sequoia, Longleaf, Pershing Square) and plain vanilla bond funds. Not suitable for money market funds, derivatives-heavy strategies, or illiquid credit.

**Prices fetched incrementally.**
Only missing dates per ticker are downloaded on each run. A same-day re-run skips yfinance entirely for up-to-date tickers.

**Fund metadata.**
`reg_name`, `net_assets`, and `total_assets` are populated for N-PORT filings. 13F does not report net assets by regulation — `reg_name` is sourced from the company name.

**Cash positions.**
For N-PORT funds, cash is computed as the residual between `net_assets` (from the filing) and the sum of all priced positions at each filing date. Cash is held constant between filing dates — it does not move with daily price changes, only with the next filing snapshot. This is not yet implemented — `net_assets` is stored in the DB and the computation is planned after `pricing_status` is complete.

For 13F funds, `net_assets` is not available in the filing by regulation. 13F is therefore treated as a long equity position tracker only, not a complete portfolio, and no cash position is computed.

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
  test_database.py
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