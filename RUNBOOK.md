# Runbook

Operational procedures for sec-holdings.

---

## Routine operations

### Fetch a fund for the first time

```bash
# Hedge fund via 13F (Pershing Square)
python -m sec_holdings.main --cik 0001336528 --source 13f

# Mutual fund via N-PORT (Fairholme)
python -m sec_holdings.main --cik 0001096344 --source nport --years 1

# With a derivatives overlay
python -m sec_holdings.main --cik 0001336528 --source 13f --overlay overlays/example_pershing.yaml
```

### Refresh an existing fund

Same command as above. Both filings and prices are fetched incrementally:
- Filings already in the DB are skipped by accession number
- Prices are only downloaded from the last known date per ticker forward
- A same-day re-run fetches nothing if no new data exists

### Check what is in the DB

```bash
python -m sec_holdings.main --info --cik 0001336528 --source 13f
python -m sec_holdings.main --info --cik 0001096344 --source nport
```

### Query the DB directly

```bash
sqlite3 data/sec_holdings.db

-- All funds in the DB
SELECT DISTINCT cik, source, COUNT(*) as filings,
       MIN(period_of_report), MAX(period_of_report)
FROM filings GROUP BY cik;

-- Latest positions for a fund
SELECT name, ticker, pct_val, value_usd
FROM holdings h JOIN filings f ON h.filing_id = f.id
WHERE f.cik = '0001336528'
AND f.period_of_report = (
    SELECT MAX(period_of_report) FROM filings WHERE cik = '0001336528'
)
ORDER BY pct_val DESC;

-- Active derivative contracts today
SELECT instrument_id, type, underlying, notional, open_date, close_date
FROM derivative_contracts
WHERE open_date <= date('now') AND close_date > date('now');
```

### Export holdings to CSV

```bash
sqlite3 -csv -header data/sec_holdings.db \
  "SELECT name, ticker, cusip, pct_val, value_usd, asset_category, country
   FROM holdings h JOIN filings f ON h.filing_id = f.id
   WHERE f.cik = '0001336528'
   AND f.period_of_report = '2026-03-31'
   ORDER BY pct_val DESC;" > pershing_2026q1.csv
```

---

## Adding a new fund

1. Find the CIK at `https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany`
2. Check which form type it files — run the diagnostic:

```bash
python -c "
import edgar
edgar.set_identity('sec-holdings mrspatbile@gmail.com')
from edgar import Company
c = Company('YOUR_CIK')
print(c.get_filings().head(20))
"
```

3. If form type is `NPORT-P` use `--source nport`. If `13F-HR` use `--source 13f`.
4. Fetch:

```bash
python -m sec_holdings.main --cik YOUR_CIK --source nport
```

---

## Adding a new overlay scenario

1. Create a new YAML in `overlays/`:

```bash
cp overlays/example_pershing.yaml overlays/pershing_rates_stress.yaml
```

2. Edit the instruments in the new file.
3. Delete existing derivative contracts for that overlay if re-running:

```sql
DELETE FROM derivative_contracts WHERE instrument_id LIKE 'rates_stress_%';
```

4. Re-run with the new overlay:

```bash
python -m sec_holdings.main --cik 0001336528 --source 13f \
  --overlay overlays/pershing_rates_stress.yaml
```

---

## Troubleshooting

### IdentityNotSetException

```
edgar.httprequests.IdentityNotSetException: User-Agent identity is not set
```

The `edgar.set_identity()` call is missing or not reaching edgartools before the first request. Check that `edgar.set_identity(config.user_agent)` is in `Fetcher.__init__`. Also verify `SEC_HOLDINGS_USER_AGENT` is set in `.env` or passed as `--agent`.

---

### No holdings returned

```
WARNING: No holdings returned. Check CIK and source type.
```

Two likely causes:

1. Wrong source type — run the diagnostic above to confirm whether the fund files `NPORT-P` or `13F-HR`.
2. Wrong CIK — the CIK may belong to the registrant, not the specific fund series. Check EDGAR directly.

---

### N-PORT returns no investment data

```
WARNING: No investment_data in XXXX: ...
```

Some N-PORT filings are amendments or cover multiple series. edgartools may return an empty DataFrame for some accession numbers. These are skipped and logged — not fatal. The other filings for the same fund are still processed.

---

### Prices fetch only missing dates
Prices are fetched incrementally per ticker. Only dates after the last known price date are downloaded. A fresh run after a full fetch skips yfinance entirely for up-to-date tickers.

---

### DB is locked

```
sqlite3.OperationalError: database is locked
```

Two processes writing to the same DB simultaneously. Never run two instances of `main.py` against the same DB at the same time. SQLite is single-writer.

---

### Stale or wrong data for a period

Delete the filing and re-fetch:

```sql
-- Find the filing id
SELECT id, accession, period_of_report FROM filings
WHERE cik = '0001336528' AND period_of_report = '2024-03-31';

-- Delete holdings first
DELETE FROM holdings WHERE filing_id = <id>;

-- Delete the filing
DELETE FROM filings WHERE id = <id>;
```

Then re-run `main.py`. The filing will be re-fetched and re-parsed.

---

## Using sec-holdings as a package

Once the DB is populated, import directly from other projects:

```python
from pathlib import Path
from sec_holdings.config import Config
from sec_holdings.database import Database
from sec_holdings.derivatives import DerivativesLoader

config = Config(
    cik="0001336528",
    source="13f",
    years=5,
    db_path=Path("../sec-holdings/data/sec_holdings.db"),
    overlay_path=Path("../sec-holdings/overlays/example_pershing.yaml"),
    user_agent="sec-holdings mrspatbile@gmail.com",
)

# Read holdings
with Database(config.db_path) as db:
    holdings = db.get_holdings_for_period_and_cik("2026-03-31", config.cik)
    prices   = db.get_prices_for_ticker("AMZN")

# Load active derivative contracts for a date
loader    = DerivativesLoader(config)
all_legs  = loader.load()
active    = loader.contracts_active_on(date(2026, 3, 31), all_legs)
```

---

## Known fund CIKs

| Fund | Type | CIK | Typical positions |
|---|---|---|---|
| Pershing Square | 13F | 0001336528 | 8-12 |
| Fairholme Fund (FAIRX) | N-PORT | 0001096344 | 10-20 |
| Sequoia Fund (SEQUX) | N-PORT | 0000089043 | ~25 |
| Longleaf Partners (LLPFX) | N-PORT | 0000806636 | ~20 |

---

## Performance reference

| Operation | Time |
|---|---|
| 13F fetch, 5 years (~22 filings) | ~3 seconds |
| N-PORT fetch, 1 year (~8 filings) | ~3 seconds |
| Price download, 22 tickers, 5 years | ~2 seconds |
| DB query, any period | < 10ms |
| Full re-run (all cached) | ~5 seconds |