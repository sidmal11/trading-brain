# Deploy to a single VPS (no AWS)

Target: one small Linux VPS running Docker Compose, with Caddy providing HTTPS. The poll runs every 5 minutes. The webhook and dashboard share one HTTPS domain.

## Choosing a host

| Option | Cost | Notes |
|--------|------|-------|
| Hetzner Cloud (small x86 VM) | roughly EUR 4 to 5 a month | Simple, reliable, good for a 24/7 poll. Recommended. |
| Oracle Cloud Always Free | free | Good price, but sign-up and capacity can be difficult. |
| Render, Railway, Fly.io | paid for always-on | Free tiers sleep or are time-limited, which breaks the poll and webhook. |

Check current pricing before you sign up.

## Domain

Use your own domain, or a free subdomain from DuckDNS. Either way, create an **A record** that points at the VPS's public IP. Caddy gets its certificate from Let's Encrypt once DNS resolves.

## Steps

1. **Create the VPS** (Ubuntu 24.04), add your SSH key, and note its IP.
2. **Point DNS** at the IP (A record).
3. **Open only the needed ports:**
   ```bash
   sudo ufw allow 22 && sudo ufw allow 80 && sudo ufw allow 443 && sudo ufw enable
   ```
4. **Install Docker:**
   ```bash
   curl -fsSL https://get.docker.com | sudo sh
   ```
5. **Copy the project** to `/opt/trading_brain` (for example with `scp` or `git clone`).
6. **Build the dashboard** on your own machine, then copy the output to the server as `frontend-dist/`. The build must use the same origin as the API:
   ```bash
   cd frontend && VITE_USE_MOCK_DATA=false VITE_TRADING_BRAIN_API_URL= npm run build
   ```
   Copy `frontend/dist` to `/opt/trading_brain/frontend-dist`.
7. **Create the secrets file** `/opt/trading_brain/.env` and make it readable only by you:
   ```bash
   cd /opt/trading_brain && touch .env && chmod 600 .env
   ```
   Put these values in it:
   ```bash
   DOMAIN=your.domain.example
   AUTH_MODE=email_otp
   ALLOWED_EMAILS=you@example.com
   SESSION_SECRET=<openssl rand -hex 32>
   COOKIE_SECURE=true
   SMTP_HOST=<your SMTP host>
   SMTP_PORT=587
   SMTP_USER=<smtp user>
   SMTP_PASSWORD=<smtp password>
   SMTP_FROM=Trading Brain <no-reply@your.domain.example>
   TELEGRAM_BOT_TOKEN=<bot token>
   TELEGRAM_CHAT_ID=<your chat id>
   ADMIN_CHAT_IDS=<your chat id>
   TELEGRAM_WEBHOOK_SECRET=<openssl rand -hex 24>
   GROQ_API_KEY=<key>
   POLLING_INTERVAL_MINUTES=5
   ```
   Keep `.env` out of git and out of any backup that leaves the server unencrypted.
8. **Start the stack:**
   ```bash
   cd /opt/trading_brain && docker compose up -d --build
   docker compose logs -f
   ```
9. **Register the Telegram webhook** against the fixed HTTPS address (use the secret from `.env`):
   ```bash
   curl "https://api.telegram.org/bot<TOKEN>/setWebhook" \
     -d "url=https://your.domain.example/api/telegram/webhook" \
     -d "secret_token=<TELEGRAM_WEBHOOK_SECRET>" \
     -d 'allowed_updates=["message","callback_query"]'
   ```
10. **Check it:** `curl https://your.domain.example/health` should return `{"status":"ok","authEnforced":true}`.

## Backups

SQLite holds the alerts and subscribers. Back it up daily with SQLite's own backup command, then copy the result off the server:

```bash
sudo apt install -y sqlite3
# crontab -e   (daily at 03:15)
15 3 * * * sqlite3 /opt/trading_brain/data/trading_brain.db ".backup '/opt/backups/tb-$(date +\%F).db'"
```

Keep at least 14 days, and copy them to a second place (another machine or cloud storage).

## Known gaps before this is live

- **The dashboard sign-in screen still uses Cognito.** The backend's email-code login is ready (`/api/auth/otp/request` and `/api/auth/otp/verify`), but the frontend has not been changed to call it. Until it is, the dashboard will not sign in on this server.
- **No rate limit on the login endpoint.** Codes lock after five wrong tries and requests have a 60-second cooldown, but an IP-level limit is not in place.
- **Single server.** SQLite and the poll share one host. Moving to more than one server would need Postgres.
- **Not yet run on a server.** The Compose files and Caddyfile have not been built or started on a real host.
