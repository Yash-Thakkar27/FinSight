# Deploying the app to Streamlit Community Cloud

The app is a read-only viewer over PostgreSQL. Hosting it needs three things: the code on GitHub
(already there), a PostgreSQL database the host can reach, and the data loaded into it. The
pipeline and the models keep running on your machine; only the app is hosted.

**Status: prepared and tested locally, not deployed.** A copy of the repository with no `.env`,
only `app/requirements.txt` installed and settings supplied as environment variables loads all
seven pages. The steps below need your accounts and have not been run.

## Before you start

- **Data redistribution.** A public app shows data from Yahoo Finance to anyone with the link.
  Yahoo's terms restrict redistribution. Keep the app private (Streamlit lets you restrict viewers
  to invited email addresses) or share the link only with the people reviewing your application.
- **The data is a snapshot.** The hosted app shows whatever was last loaded. It does not refresh
  itself; rerun step 3 when you want newer data.
- **Free tiers sleep.** A free database suspends when idle and the app sleeps after days without
  visits; the first page load afterwards takes longer.

## 1. Create a hosted PostgreSQL database

Any PostgreSQL 15+ works. With [Neon](https://neon.tech) (free tier, 0.5 GB; this project uses
about 55 MB):

1. Sign up and create a project. Name the database `finsight`.
2. Open **Connection details** and note the host, database, user and password. Use the direct
   (non-pooled) host for loading data.

## 2. Point your machine at it

Create `.env.cloud` in the project root (it is gitignored) with the hosted database's values:

```bash
POSTGRES_HOST=your-project.region.aws.neon.tech
POSTGRES_PORT=5432
POSTGRES_DB=finsight
POSTGRES_USER=your_user
POSTGRES_PASSWORD=your_password
POSTGRES_SSLMODE=require
```

## 3. Load the data (from your machine)

Open a **new terminal** for this, so the hosted settings do not linger in your normal one:

```bash
cd FinSight
source .venv/bin/activate
set -a; source .env.cloud; set +a        # these variables override .env for this terminal

python scripts/init_db.py                # create schemas, tables and views
python scripts/update_data.py --skip-fetch   # load from the raw snapshots on disk, no network fetch
python scripts/run_ml.py                 # Data Science Lab results
```

Close that terminal when it finishes.

- `--skip-fetch` loads exactly the data you already have locally. Leave it out to fetch fresh
  data first.
- **Do not run `pytest` in that terminal.** The database tests would create a test database on
  the hosted server.
- `run_ml.py` rewrites `docs/model_cards/`. With the same data the cards come out unchanged
  (they carry the data snapshot id, not a timestamp). If you fetched fresh data, commit the
  regenerated cards so the hosted app shows the ones that match its database.

## 4. Push the code

Commit and push the current state of `main` (the deployment files are `app/requirements.txt`,
`.streamlit/config.toml`, `.streamlit/secrets.toml.example` and the `POSTGRES_SSLMODE` setting).

## 5. Create the app

1. Go to [share.streamlit.io](https://share.streamlit.io) and sign in with GitHub.
2. **Create app → Deploy a public app from GitHub.**
3. Repository `Yash-Thakkar27/FinSight`, branch `main`, main file path **`app/Home.py`**.
4. **Advanced settings:**
   - Python version: **3.13**.
   - Secrets: paste the six lines from `.streamlit/secrets.toml.example` with your database's
     values. Root-level secrets reach the app as environment variables, which is how
     `config/settings.py` reads them.
5. **Deploy.** The first build takes a few minutes.

Streamlit installs `app/requirements.txt` (10 packages), because it sits next to the entry point;
the full `requirements.txt` at the repository root is for the pipeline, the models and the tests.

To restrict who can see it: **App settings → Sharing → Only specific people can view this app.**

## 6. Check it

- The Overview shows 25 companies and a latest price date.
- Company Analysis for HDFC Bank shows N/A with "not meaningful for banks".
- The Data Science Lab shows the model-versus-baseline table and the model cards.
- The footer on every page gives the data source and as-of date.

## If something goes wrong

| Symptom | Likely cause |
|---|---|
| "No data has been loaded yet" | Step 3 was run against a different database than the one in the app's secrets. |
| `connection refused` or a timeout in the app's logs | Wrong host or port in the secrets, or the database's IP allow-list blocks Streamlit (Neon allows all by default). |
| `SSL connection is required` | `POSTGRES_SSLMODE = "require"` is missing from the secrets. |
| `password authentication failed` | A typo in the secrets; special characters in the password are handled, so paste it as is. |
| "No Data Science Lab results are stored yet" | `run_ml.py` was not run against the hosted database. |
| Build fails on a package | Check the Python version is 3.13; the pins in `app/requirements.txt` were verified on 3.13. |

## Running locally after this change

`.streamlit/config.toml` no longer fixes the bind address, so that the same file works on a host.
To keep a local run off your network:

```bash
streamlit run app/Home.py --server.address localhost
```
