# FinSight

**Financial Markets & Comparable Companies Analytics Platform (Indian Equities)**

> Work in progress. Phases 1–6 of 9 are complete: environment, configuration and database;
> data-source inspection; ingestion with raw snapshots; cleaning, validation and load; the
> metrics engine (ratios, valuation, returns, risk) for a 25-company universe; comparable
> companies with peer statistics and generated interpretation; anomaly detection; correlation;
> and the Data Science Lab (volatility forecasting with walk-forward evaluation, peer
> clustering, statistical tests, regime detection). This README covers setup only and is
> completed in Phase 9.

FinSight is an analytics tool, not investment advice.

## Setup (macOS)

Requirements: Python 3.11+ (built and verified on 3.13), and either Docker Desktop or Homebrew.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # then set POSTGRES_PASSWORD
```

### PostgreSQL, option A: Docker (verified)

```bash
open -a Docker                # start Docker Desktop if it is not running
docker compose up -d          # PostgreSQL 16 on localhost:5433
```

The container listens on host port **5433** so it does not clash with a local PostgreSQL on 5432.

### PostgreSQL, option B: Homebrew (not yet verified on the development machine)

```bash
brew install postgresql@16
brew services start postgresql@16
createuser -s finsight
createdb -O finsight finsight
psql -d finsight -c "ALTER USER finsight WITH PASSWORD 'your_password';"
```

Then set `POSTGRES_PORT=5432` in `.env`.

### Windows notes

Use `py -m venv .venv` and `.venv\Scripts\activate`. Run PostgreSQL with Docker Desktop
(`docker compose up -d`) and `copy .env.example .env`.

## Commands

```bash
python scripts/init_db.py             # create schemas, tables, indexes; seed reference data
python scripts/init_db.py --reset     # drop everything first (deletes all data)
python scripts/update_data.py         # fetch, clean, validate, load, recompute metrics
python scripts/update_data.py --tickers TCS.NS INFY.NS    # limit the fetch
python scripts/update_data.py --skip-fetch                # rebuild from raw snapshots, offline
psql -h localhost -p 5433 -U finsight -d finsight -f sql/analytical_queries.sql
python scripts/run_ml.py              # Data Science Lab: models, tests, model cards (offline)
python scripts/inspect_source.py      # regenerate docs/data_source_inspection.md (needs network)
pytest                                # no network; DB tests use a separate finsight_test database
ruff check .
```

## Documentation

- `docs/data_source_inspection.md`: what the source actually returns (fields, periods, units)
- `docs/methodology.md`: cleaning conventions, validation checks, loading, metric formulas,
  how the USD reporter (Infosys) is handled, comps, anomalies and correlation
- `docs/model_cards/`: one card per model, generated from the stored results
- `notebooks/01_eda.ipynb`, `notebooks/07_anomalies_and_regimes.ipynb`: executed notebooks
- `docs/assumptions.md`: assumptions and open decisions
- `docs/limitations.md`: known limitations
