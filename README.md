# ATLAS — final deployment package for TRACKER-BOT

Ye complete application hai: Telegram bot, automatic scanner, 136-metric research,
RSI/MFI/OBV/EMA/MACD dashboard, separate OI context, exchange screening and web UI.
Purane ZIPs ya alag code snippets combine karne ki zaroorat nahi hai.

## 1. GitHub par exactly kya upload karein

`ATLAS-FINAL-DEPLOY.zip` extract karein. Extracted folder ke **andar ke saare
files aur bot/ folder** existing TRACKER-BOT repository ke root par upload karein.
ZIP file khud upload nahi karni. Extra parent folder ke andar app mat rakhein.
Existing repository ka commit/download backup rakh lein.

Replace these existing files:
- `bot.py`
- `requirements.txt`
- `.python-version`

Add the included files/folders together in the same commit:
- `bot/` — entire folder including `data/` and `web/`; mandatory
- `render.yaml`
- `.env.example` (empty credential template only)
- `.gitignore`
- `README.md` (these instructions)

Repository root par `bot.py`, `bot/`, `requirements.txt` aur `render.yaml`
saath dikhne chahiye. `bot.py` launcher hai; sirf usko copy karna sufficient nahi.
Tests, development tools, generated reports, original PDFs and credentials are
not included in this deployment-only package.

## 2. Existing Render service settings

Use the existing **Python Web Service** connected to this repository:

| Setting | Exact value |
| --- | --- |
| Root directory | Empty (repository root) |
| Build command | `pip install -r requirements.txt` |
| Start command | `python bot.py` |
| Health check path | `/healthz` |
| `PYTHON_VERSION` environment variable | `3.12.14` |
| Processes / instances | One |

Old `PYTHON_VERSION` environment value overrides `.python-version`, so update
it if present. The launcher serves the dashboard on `0.0.0.0:$PORT` and runs
the scanner and Telegram polling in the same process.

If the existing service is a Background Worker, create a Python **Web Service**
for the same repo to obtain a dashboard URL; choose Free if using the free tier.
Stop the old polling process before starting another with the same Telegram token.
For an existing service, uploading `render.yaml` alone does not replace its manually
configured settings. Do not create a second Blueprint just to update that service.

## 3. Environment variables

Your existing bot credentials can stay exactly as they are:

| Variable | Value |
| --- | --- |
| `TELEGRAM_TOKEN` | Existing BotFather token; keep private |
| `USER_CHAT_ID` | Existing numeric destination chat ID |
| `DASHBOARD_TOKEN` | Add your own long random dashboard password |
| `PYTHON_VERSION` | `3.12.14` |
| `DEMO_MODE` | `false` |
| `SCAN_SECONDS` | `300` |

The modern names `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` are also supported.
Use one complete pair. If a modern value is nonempty, both modern values are
required and take precedence; credentials from the two pairs never mix.
For a new Blueprint, its prompts use the modern names and generate DASHBOARD_TOKEN.
For this existing-service update, add DASHBOARD_TOKEN yourself in Environment.
Never put real tokens/passwords into GitHub or `.env.example`.

Private chat: owner inferred from USER_CHAT_ID. Group chat: also set
`TELEGRAM_OWNER_ID` to your positive Telegram user ID; `/whoami` displays it.
Normally no PUBLIC_BASE_URL is needed: Render supplies the dashboard URL.

## 4. Deploy and use

Deploy the latest commit after saving the settings above. Open the service's
`.onrender.com` URL when Live:
- Dashboard username: `atlas`
- Password: your `DASHBOARD_TOKEN`
- Public health endpoint: `/healthz` (liveness, not proof of fresh market data)

Telegram commands after the first scan:
```text
/panel
/status
/technical SOL 5m
/access
/report
```

`/panel` opens the new controls. Add/remove coins and change the timer there.
`/website` gives the dashboard button. `/zip off` keeps automatic digests while
turning off automatic large ZIP attachments; `/report` still requests the full ZIP.

## What carries over, and limits

- Existing repo, Telegram bot credentials and `python bot.py` start command can
  be reused. This runs the new Atlas research application, replacing the old
  Gate.io/MEXC sniper-score engine; its old signal rules are not preserved.
- The supplied old ZIP has no `quant_sniper.db`. Old watchlists and signal history
  are not imported. Add your selected coins again via `/panel` (1–20 research coins).
- All supported exchange listings are screened separately. This does not mean
  all 136 metrics are available for every listed coin. Missing data stays labelled.
- Optional paid providers still require their own API keys/plans. No exchange
  restriction bypass, accuracy guarantee or trading execution is included.
- Free Render services can sleep and have usage limits. Local preferences can be
  lost on restart/redeploy: save `/backup`, or configure CONTROL_REDIS_URL and
  CONTROL_REDIS_TOKEN for durable preferences. An uptime monitor is not a 24/7 SLA.

Official hosting references:
- https://render.com/docs/deploy-fastapi
- https://render.com/docs/python-version
- https://render.com/docs/web-services
- https://render.com/docs/free
