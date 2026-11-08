# Realton Telegram Mini App

A mobile-first Telegram Mini App starter for a reward-points concept. The app includes a responsive dashboard, a small SQLite ledger, Telegram `initData` validation scaffold, cooldown-limited idempotent demo claims, and a TON-address withdrawal request queue.

## Safety and scope

This project is a prototype foundation, not a launched service. `REALTON_DEMO_MODE=true` by default and the local demo identity is enabled only when no Telegram bot token is configured. It records demo points only. It does not create, mint, issue, or connect a Jetton; it does not connect Tonkeeper, sign transactions, or send TON. Withdrawal requests reserve points and go to a pending queue, but there is no admin review UI or payout mechanism. The sample demo database is local and is not suitable for public multi-user hosting.

Do not use real deposits, promise earnings, or treat points as money. Before any public release, review security, privacy, anti-abuse, legal, and hosting requirements. Never expose a bot token in frontend code.

## Run locally

Requires Python 3.10+ and no third-party Python packages.

```sh
cd realton
python app.py
```

Open `http://127.0.0.1:8000`. The app runs in demo mode without needing a Telegram bot token. The default SQLite database is `realton/realton.sqlite3` and can be changed with `REALTON_DB`.

## Telegram authentication scaffold

To require Telegram authentication, configure `TELEGRAM_BOT_TOKEN` as a server-side environment variable and set `REALTON_DEMO_MODE=false`, then restart. The server validates Telegram Mini App `initData` HMAC and rejects missing, invalid, or stale sessions. Never paste the token into the source code or browser.

Telegram Mini Apps require a publicly hosted HTTPS URL and a bot configured to launch it. This project is not deployed or connected to a bot. Hosting, BotFather setup, and production database operations remain separate steps.

## API

- `GET /api/health` returns prototype mode flags.
- `GET /api/me` returns demo points, claim cooldown, and recent requests.
- `POST /api/claim` with a unique `request_key` credits one demo point, enforcing a 10-second cooldown.
- `POST /api/withdraw` with `points` and `wallet_address` validates the input, reserves points, and creates a pending request. It does not transfer anything.

All APIs are same-origin. With a configured token, send the Telegram Web App `initData` as `X-Telegram-Init-Data`.
