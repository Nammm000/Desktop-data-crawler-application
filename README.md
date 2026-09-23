# Data Crawler

A desktop data-crawler: a FastAPI + MongoDB backend that runs crawl **agents**
(embedded [Scrapy](https://scrapy.org) — generic XPath spiders and Facebook
post collection), and a PySide6 desktop client.

## Quick start

```bash
make setup   # both virtualenvs + pinned deps + backend/.env with fresh secrets
make up      # MongoDB (docker) -> API on :8000 -> desktop app
```

The first account you sign up becomes the **admin**; later signups are regular
users. `make up` runs everything in one terminal — Ctrl+C stops the app and the
API; `make down` also stops MongoDB (data is kept). See `make help` for all
targets (dev mode, logs, secret generation).

## What it does

- **Agents** are crawl definitions. A *generic* agent's script is JSON:
  `{"links": [url, ...], "<field>": "<xpath or [xpath, ...]>"}`. A *Facebook*
  agent points at facebook.com post URLs (auto-rewritten to `mbasic.facebook.com`)
  and extracts built-in post fields (author, text, timestamp, reactions,
  comments, permalink, media) with optional XPath overrides.
- **Credentials** (Facebook cookies, proxy list) are stored Fernet-encrypted
  in a separate collection and are **never returned by the API**.
- **Run results**: one `data` document per successfully crawled page; the
  agent carries a `lastRun` summary with per-link failure reasons
  (`broken_link`, `login_redirect`, `checkpoint` = bot check, `rate_limited`,
  `timeout`, `dns_error`, ...). Runs can be stopped mid-crawl.
- Users, JWT auth (rotating refresh tokens), live status over WebSocket.

## Facebook crawling — read this first

- Use a **dedicated account** whose checkpoint/ban you can afford; scraping
  with your cookies can trigger security checks regardless of pacing.
- Pacing is deliberately gentle (one request at a time, 3 s randomized delay —
  `FACEBOOK_DOWNLOAD_DELAY` in `backend/.env`); proxies are optional,
  one URL per line (`http://user:pass@host:port`).
- `mbasic.facebook.com` markup changes over time and differs by region — if
  runs suddenly report `unexpected_html` on every link, the built-in selectors
  need updating (or add per-field XPath overrides on the agent).

## Layout

```
backend/    FastAPI app (see backend/README.md for the full API docs)
frontend/   PySide6 desktop client
scripts/    setup helpers; Makefile / run.sh orchestrate everything
```

## Notes

- Losing `CREDENTIALS_ENCRYPTION_KEY` invalidates all stored cookies/proxies
  (re-paste them). Keep `backend/.env` backed up.
- The API is single-process by design (crawls share the event loop); don't run
  `uvicorn --workers N`.
