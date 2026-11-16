# Use cases, tests, and gaps

## Use cases

| ID | Actor | Use case | Expected result |
|----|-------|----------|-----------------|
| UC-1 | Subscriber | Sends `/start` to the bot | Chat is stored as active; confirmation sent |
| UC-2 | Subscriber | Sends `/stop` | Chat is deactivated; no further alerts; `/start` again resubscribes |
| UC-3 | System | New signal is found | Every active subscriber gets the alert; only the admin copy has Approve/Reject |
| UC-4 | System | A subscriber has blocked the bot (HTTP 403) | That subscriber is deactivated; other failures (e.g. HTTP 500) keep the subscriber |
| UC-5 | Admin | Taps Approve or Reject (in Telegram or the dashboard) | Status becomes APPROVED or REJECTED; Telegram buttons are removed |
| UC-6 | Subscriber | Taps a button on a copy they received | Status unchanged; tap ignored |
| UC-7 | Anyone | Webhook without the secret; commands in groups; tap on an unknown alert | Rejected (401), ignored, or harmless |
| UC-8 | System | The poll runs: scan, analysis, alert, storage | Only signals above the volume threshold are stored and sent |
| UC-9 | Admin | Views and decides in the dashboard approval queue | Queue shows pending alerts; Approve changes the status |

## Running the tests

```bash
cd trading_brain
pip install -r requirements-dev.txt
python -m pytest -q
```

No AWS account, no real Telegram chat, and no network calls are needed. Current result: **118 passed**.

- `tests/test_e2e_local.py`: UC-1 to UC-7, each run on **both** DynamoDB (in-process mock) and SQLite.
- `tests/test_poll_handler_local.py`: UC-8, with the market scan and LLM mocked.
- `tests/test_subscribers_and_broadcast.py`, `tests/test_api.py`, `tests/test_telegram_client.py`: unit and webhook tests.

## Running it locally (SQLite, no AWS)

```bash
# 1. API on a single SQLite file
STORAGE_BACKEND=sqlite SQLITE_PATH=./data/local.db uvicorn api.main:app --port 8000

# 2. Poll loop (or one run with --once)
STORAGE_BACKEND=sqlite SQLITE_PATH=./data/local.db python scripts/run_poll.py

# 3. Dashboard: the frontend .env.local already points at localhost:8000.
cd ../frontend && npm run dev
```

Sign in with the mock auth: enter any email, then any 6-digit code.

Verified in this round: the dashboard loads the alert queue from the local API, and Approve from the dashboard updates the SQLite row to APPROVED.

Telegram webhook over a tunnel was verified earlier with a real tap (see the local test notes).

## Docker (not yet built here)

`Dockerfile`, `docker-compose.yml`, and `.dockerignore` are written. Docker Desktop was not running in this environment, so the image has **not been built or started**. Start Docker Desktop, then:

```bash
docker compose up -d --build
docker compose logs -f poll
```

## Remaining gaps

**Local**
- The market scan, Groq analysis, and Yahoo data are not called live. The poll path is tested with mocks only.
- The dashboard's market feeds show UNAVAILABLE and DOWN in this sandbox. Live prices need network access and the market APIs.
- Auth is mock mode locally. Any 6-digit code works.
- Tunnel URLs change on restart, so the Telegram webhook must be reset each time.

**Production (not AWS)**

| # | Item | Status | Needs from you |
|---|------|--------|----------------|
| 1 | Storage adapter | Done: SQLite backend, same behaviour as DynamoDB (tested on both) | Postgres instead of SQLite? Say so and I'll add it |
| 2 | Read API auth | Not done | See decision A below |
| 3 | Poll scheduler | Done: `scripts/run_poll.py` loops or runs once (`--once`) for cron | Confirm the interval |
| 4 | Stable HTTPS for the webhook | Not done | See decision B below |
| 5 | Dockerfile and restart policy | Written, not built | Start Docker Desktop to build and test |
| 6 | Secrets out of `.env` | Not done | See decision C below |
| 7 | CORS for the real frontend origin | Not done | The domain the dashboard will be served from |
| 8 | Backups | Not done | Where backups should go |
| 9 | Telegram rate limiting and retries | Not done | None (I'll add it) |
| 10 | Disclaimer on `/start` | Not done | Wording, checked by someone qualified |

## Decisions I need from you

- **A. Dashboard login.** The read API has no login outside AWS. Options:
  1. Keep Cognito, which is AWS.
  2. Your own email OTP, with a session cookie, on the host.
  3. A single API key for you only, which is the quickest option but not suitable for other users.
- **B. Where the webhook and dashboard will run.** A VPS, Render, Railway, or Fly.io, and the domain name. The webhook needs a fixed HTTPS address.
- **C. Where secrets go.** The host's secret store, if it has one, or a file on the server with restricted permissions.
- **D. Postgres or SQLite** for production. SQLite works for one server; Postgres is needed for more than one server or heavy writes.
