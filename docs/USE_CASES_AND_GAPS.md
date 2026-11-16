# Use cases, local tests, and gaps

## Use cases

| ID | Actor | Use case | Expected result |
|----|-------|----------|-----------------|
| UC-1 | Subscriber | Sends `/start` to the bot | Chat is stored as active; confirmation sent |
| UC-2 | Subscriber | Sends `/stop` | Chat is deactivated; no further alerts; sending `/start` again resubscribes |
| UC-3 | System | New signal is found | Every active subscriber gets the alert; the admin copy has Approve/Reject buttons, other copies do not |
| UC-4 | System | A subscriber has blocked the bot (HTTP 403) | That subscriber is deactivated automatically; other failures (e.g. HTTP 500) keep the subscriber |
| UC-5 | Admin (you) | Taps Approve or Reject | Alert status becomes APPROVED or REJECTED; buttons are removed from the admin message |
| UC-6 | Subscriber | Taps a button on a copy they received | Status is unchanged; the tap is ignored |
| UC-7 | Anyone | Calls the webhook without the secret; commands in a group; taps on an unknown alert | Rejected (401), ignored, or harmless (200 with `ignored`) |

## Automated tests (run locally, no AWS, no real Telegram)

```bash
cd trading_brain
pip install -r requirements-dev.txt
python -m pytest -q
```

- `tests/test_e2e_local.py`: one test per use case above, using an in-process DynamoDB mock (moto) and a recorded Telegram HTTP layer. Nothing is sent to a real chat.
- `tests/test_subscribers_and_broadcast.py`: store and broadcast unit tests.
- `tests/test_api.py`, `tests/test_telegram_client.py`: webhook and client tests.

Current result: all tests pass.

## Manual test with your real bot (local, via tunnel)

These use the real Telegram bot and your own chat. They need a tunnel, so the bot can reach your laptop.

1. Start the fake DynamoDB: `python -m moto.server -p 5000`, then create the two tables (see `scripts/` or re-run the table setup).
2. Start the API with `AWS_ENDPOINT_URL_DYNAMODB=http://127.0.0.1:5000` and `uvicorn api.main:app --port 8000`.
3. Start a tunnel: `cloudflared tunnel --url http://127.0.0.1:8000`.
4. Point the bot at it: `setWebhook` with `url=<tunnel>/api/telegram/webhook`, `secret_token=<TELEGRAM_WEBHOOK_SECRET>`, `allowed_updates=["message","callback_query"]`.
5. Send an alert, tap Approve, and check the status in the fake table.
6. Clean up: `deleteWebhook`, stop cloudflared, uvicorn, and moto. Tunnel URLs change each time.

## Gaps for local testing

- **Market scan and LLM not exercised.** `lambda_function.py` needs the market-scan API, Groq, and Yahoo data. Nothing here calls them; the e2e tests start from an alert that already exists.
- **Poll Lambda not run end to end.** Only `broadcast_alert` and the database write are tested, not `lambda_handler`.
- **Dashboard not tested against the local API.** `scripts/dev_api.py` runs the read API with an in-memory store, but the frontend has not been run against it in this round.
- **Auth is off locally.** `COGNITO_USER_POOL_ID` is unset, so the read API is unauthenticated. Fine on your laptop, not fine anywhere reachable.
- **Telegram rate limits untested.** Broadcasts run one after another; a large subscriber list is not load tested.
- **Subscriber list uses a full scan.** Fine for tens to hundreds of users.
- **Webhook over a tunnel only.** The tunnel URL changes every restart, so the webhook must be reset each time.
- **Frontend does not know about subscriptions.** Nothing in the UI shows who is subscribed.

## What else is needed for production (not AWS)

Blockers first:

1. **Replace the AWS-only storage.** `DbClient` and `SubscriberStore` both call boto3 DynamoDB directly. Move them behind an interface and implement it for your chosen database (for example Postgres or SQLite). The e2e tests already show the behaviour to keep.
2. **Secure the read API.** Set `COGNITO_USER_POOL_ID` and `COGNITO_CLIENT_ID`, or replace Cognito with your own auth. Without this, anyone who finds the URL can read alerts and approve trades. Note: Telegram approvals are protected by the webhook secret, but dashboard decisions go through `/api/alerts/{id}/review`, which needs auth.
3. **Replace the scheduler.** EventBridge runs the poll every 15 minutes. Use cron, or a job runner on your host, to call `lambda_handler` (or a `main()` wrapper) on the same schedule.
4. **Stable HTTPS hosting for the webhook.** A VPS, Render, Railway, or Fly.io app with a fixed domain. Then register the webhook once and stop relying on tunnels.
5. **A Dockerfile or process manager.** None exists yet. The read API (`uvicorn api.main:app`) and the poll job need to run as services that restart on failure.
6. **Secrets handling.** Move the bot token, webhook secret, Groq key, and AInvest key out of `.env` files into your host's secret store. The `.env` file must never be copied into a container image.
7. **Dashboard host and CORS.** Set `CORS_ALLOWED_ORIGINS` to the real frontend origin and host the frontend build (it is static).
8. **Backups.** Set up scheduled database backups before real approvals are stored.

Important but can follow:

9. **Broadcast throttling and retries.** Respect Telegram's roughly 30 messages per second limit and retry transient 5xx errors.
10. **Monitoring and alerts.** Log to a file or a service, and get notified when the poll job fails or the webhook returns errors.
11. **Automated webhook registration.** A script that sets the webhook and `allowed_updates` on deploy.
12. **Subscriber management.** Admin command to list, remove, or ban subscribers. Right now it's only possible by editing the database.
13. **Disclaimers.** Alerts are sent to users who are not you. Show a risk disclaimer on `/start` and make sure alerts are described as information, not advice. Check this with someone qualified before sending alerts to other people.
14. **Frontend subscription view.** Optional: show subscriber counts on the dashboard.
15. **Process for the data pipeline.** The market-scan and LLM steps have external costs and rate limits. Set spending caps before opening it to more users.

## Requirements file

`requirements-dev.txt` (created with this document) pins the test-only dependencies: `pytest` and `moto[dynamodb]`.
