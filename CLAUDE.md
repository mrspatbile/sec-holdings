# CLAUDE.md

## What this project is

A data sourcing and portfolio construction layer that fetches real fund holdings from SEC EDGAR, enriches them with daily market prices, and applies a configurable rolling derivatives overlay. The output is a SQLite database consumed downstream by a pricing engine and a risk management framework.

This is not a risk system and not a pricing system. It builds the portfolio those systems consume. The carry-forward assumption — positions frozen between filing dates — is intentional, documented, and wrong by design. It is the honest baseline given the data available.

## What has been built

- Project structure, environment, `pyproject.toml`, `.gitignore`, `.gitattributes`
- `config.py` — frozen Config dataclass, reads from `.env` via python-dotenv, CLI overrides
- `fetcher.py` — edgartools wrapper for N-PORT (`NPORT-P`) and 13F (`13F-HR`); normalises both to a common holding schema; `edgar.set_identity()` required before first call
- `prices.py` — yfinance daily adjusted close prices; chunked batch download (50 tickers per chunk); tickers without prices logged and skipped
- `derivatives.py` — YAML overlay loader; generates rolling contract legs per instrument; `contracts_active_on(date)` for downstream consumption
- `database.py` — SQLite schema (4 tables: filings, holdings, daily_prices, derivative_contracts); context manager; full read/write API
- `main.py` — CLI entry point; `--info` flag for DB summary; 4-step pipeline: fetch → persist → prices → derivatives
- `overlays/example_pershing.yaml` — example overlay for Pershing Square with SPX put, IRS, FX forward
- `tests/test_derivatives.py` — 28 tests covering tenor/strike/date parsing, contract generation, roll continuity, active contract filtering
- `tests/test_prices.py` — price fetcher tests with mocked yfinance; ticker cleaning, chunking, single/multi ticker flattening
- `tests/test_fetcher.py` — normalisation tests for 13F and N-PORT rows; field mapping, pct_val calculation, routing
- `tests/fixtures/example_overlay.yaml` — minimal overlay fixture for tests
- `RUNBOOK.md` — operational procedures, troubleshooting, usage as package
- Incremental price fetching -- `get_stale_tickers` skips fresh tickers, appends only missing dates per ticker
- `reg_name`, `net_assets`, `total_assets` populated from edgartools for N-PORT; `reg_name` from company name for 13F
- `tests/test_database.py` -- 35 tests covering schema, filings, holdings, prices, contracts, stale tickers, latest filing date
- Updated `tests/test_fetcher.py` -- reg_name / net_assets / total_assets field mapping and `_filing_meta_from_holdings`
- Updated `tests/test_prices.py` -- `fetch_incremental` tests

## Known edgartools quirks

- Form type for N-PORT is `NPORT-P` not `N-PORT`
- Holdings accessed via `obj.investment_data()` (method call, not property)
- 13F columns: `Issuer`, `Cusip`, `Ticker`, `Value`, `SharesPrnAmount`, `Type`
- N-PORT columns: `name`, `cusip`, `isin`, `ticker`, `value_usd`, `pct_value`, `balance`, `units`, `asset_category`, `investment_country`, `payoff_profile`, `maturity_date`, `annualized_rate`
- `edgar.set_identity(config.user_agent)` must be called in `Fetcher.__init__` before any EDGAR request
- 13F `Value` is in full dollars as returned by edgartools (do not multiply by 1000)
- DeprecationWarnings from edgartools internals are suppressed in `conftest.py` — they are not our code to fix
- `obj.name` returns the fund name (e.g. `Fairholme Funds Inc - The Fairholme Fund`)
- `obj.fund_info.net_assets` and `obj.fund_info.total_assets` are available for N-PORT; not present in 13F by regulation
- `obj.general_info` returns a pydantic model with series name, CIK, LEI, fiscal year end, and reporting period
- yfinance returns MultiIndex columns `('Close', 'TICKER')` even for single-ticker downloads — `_flatten` handles both cases

## Architecture

```
sec-holdings              pricing project           manco-risk-mngmt
─────────────────         ──────────────────        ──────────────────────
N-PORT positions    →     price derivatives   →     VaR, leverage
13F positions             build curves              concentration
daily prices              compute Greeks            AIFM / UCITS metrics
rolling derivatives       delta equivalents         Annex IV / VI
```

## Project structure

```
src/sec_holdings/
  config.py            Config dataclass, reads from .env via python-dotenv
  fetcher.py           edgartools wrapper -- N-PORT and 13F
  prices.py            yfinance daily prices per ticker
  derivatives.py       YAML overlay loader, rolling contract logic, roll history
  database.py          SQLite schema and persistence layer
  main.py              CLI entry point

overlays/              YAML overlay files -- gitignored
  example_pershing.yaml

tests/
  conftest.py          Suppresses DeprecationWarnings from edgartools
  fixtures/            Static XML and YAML files -- no EDGAR calls in tests
    example_overlay.yaml
  test_fetcher.py      Normalisation tests -- no EDGAR calls; reg_name / net_assets / total_assets coverage
  test_derivatives.py  Rolling contract logic tests
  test_prices.py       Price fetcher tests -- yfinance mocked; fetch_incremental coverage
  test_database.py     Schema, filings, holdings, prices, contracts, stale tickers, latest filing date
```

## Stack

- Python 3.13
- SQLite — lightweight persistence, no server
- edgartools>=2.0,<6.0 — SEC EDGAR N-PORT and 13F parsing
- yfinance — daily market prices
- PyYAML — overlay definition
- pandas / numpy — data handling
- python-dotenv — config from .env
- Jupyter / JupyterLab — example notebooks

## How we work together

**Do not make changes without checking with me first.**

The preferred flow for every task:
1. Explain your understanding of the task and your proposed approach
2. Wait for my go-ahead before writing or changing any code
3. Make changes one logical step at a time — not everything at once
4. After each step, explain what you did and why
5. After each step, when I consider code done, I will ask you for a commit message. The message you pass to me should include the commands: git add and git commit -m

The developer has a finance background and works at the intersection of finance and technology. Code quality matters: clean design, good package structure, disciplined progression. When making changes, always explain the business logic so it can be verified. Do not over-explain technical basics, but never skip the reasoning behind implementation choices.

## Things to never do without explicit permission

- Refactor across multiple files in one go
- Change data structures or schemas
- Delete or rename anything
- Touch `.venv/` or any environment config
- Add new dependencies without flagging first
- Modify the base SEC portfolio positions in the overlay logic

## Tone

This is a research and learning project at the intersection of financial regulation and engineering. When I ask why something is done a certain way, take the time to explain it properly. That is part of the value.

## Hard constraints — never override

- The carry-forward assumption is intentional. Do not suggest making it dynamic or adding intra-period position interpolation.
- The overlay is additive only. Do not add position deletion or modification logic.
- Derivative pricing does not belong here. Do not add valuation logic for any instrument type.

## Code style

- PEP 8 throughout
- Type hints on all public functions and methods
- Docstrings on all public classes and functions. Where a parameter has a non-obvious convention (percent vs decimal, notional vs market value, shares vs principal amount), state it explicitly in the docstring
- No new dependencies without flagging first

## Scope boundary

This project is the data and portfolio construction layer only. There is a separate pricing project handling curve construction, derivative valuation, and Greeks (`quant-risk-engine`). There is a separate risk project (`manco-risk-mngmt`) handling VaR, leverage, stress testing, and regulatory metrics. Do not import patterns, architecture, or scope from either of those projects into this one.