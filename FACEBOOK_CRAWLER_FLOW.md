# Facebook Data Collection — How It Works

This document describes the Facebook post-collection process end to end: how
to set it up and use it, what happens on each run, why links fail and how to
read the reasons, and what to maintain when Facebook changes its markup.

A **Facebook agent** is an agent with `sourceType: "facebook"` (chosen as
"Facebook posts" in the Add/Edit agent dialog). Unlike a *generic* agent —
where you write XPath expressions for any website — a Facebook agent has
built-in post extraction and adds login cookies and proxy support.

**Read this first — honest caveats:**

- Collection goes through `mbasic.facebook.com`, Facebook's lightweight
  server-rendered site. It changes over time and behaves differently by
  region; the built-in selectors occasionally need updating (see
  [Maintenance](#maintenance)).
- Scraping with logged-in cookies can trigger security checkpoints on the
  account regardless of pacing or proxies. **Use a dedicated account** whose
  restriction you can afford.
- This is best-effort tooling for personal/internal collection, not a
  guaranteed pipeline.

## What gets collected

One `data` document per successfully fetched post URL, with these built-in
fields (defined in `backend/app/services/facebook_spider.py`):

| Field | Content |
|---|---|
| `author` | post author name |
| `text` | post text (paragraphs joined with newlines) |
| `timestamp` | post time as shown on the page |
| `reactions` | reaction summary text, when present |
| `comments` | comment-count text, when present |
| `mediaUrls` | image URLs (newline-joined) |
| `permalink` | the final URL the post was fetched from |

Any of these can be overridden per-agent: a script key with the same name
(e.g. `"text": "//div[@id='my-post-box']//p/text()"`) replaces the built-in
XPath list for that field. Multi-match XPaths join their values with
newlines; the first XPath in a list that matches anything wins.

## Setup & usage

### 1. Get your cookies

1. Log in to `facebook.com` in a desktop browser (a dedicated account).
2. Open DevTools → Network → click any request to facebook.com → copy the
   entire `Cookie:` **request header** value (a long `name=value; name=value`
   string).
3. In the app's Add/Edit agent dialog, paste it into the **Cookies** field.
   The live preview tells you what it found, e.g.
   *"4 cookies detected — includes c_user and xs (logged-in session)"*.
   `c_user` + `xs` are the two cookies that make it a logged-in session.

Cookies are optional — public pages can sometimes be fetched without them —
but most post URLs need a session.

### 2. Create the agent

In the dialog:

- **Source**: Facebook posts
- **Script** (JSON): a `links` array with the post/page URLs to collect:

  ```json
  {
    "links": [
      "https://www.facebook.com/story.php?story_fbid=123&id=456",
      "https://www.facebook.com/NASA/photos"
    ]
  }
  ```
- **Proxies** (optional): one `http://` or `https://` URL per line, up to 20
  (`http://user:pass@host:port`). Requests rotate through them round-robin.
- Every other script key is an optional XPath override of a built-in field.

Links must be `https` URLs on `facebook.com` or a subdomain — anything else
is rejected when the agent runs (HTTP 400).

Credentials are parsed and validated once, then stored **encrypted**. They
are never displayed again — the edit dialog only shows a summary ("Saved: 4
cookies (c_user, xs, fr, datr) + 2 proxies — never shown again") and a
**Clear saved credentials** button.

### 3. Run and stop

- **Run**: the play button on the agent row. The row flips to *Running* and
  the crawl continues in the background (one request at a time, ~3 s apart —
  see [Pacing](#configuration--security-notes)).
- **Stop**: while Running, the play button becomes a red stop button. Stopping
  is *graceful*: in-flight requests settle first (this can take a few
  seconds), then the run ends as **Stopped** with all data collected so far
  kept. A stopped agent can be run again immediately.

### 4. Read the results

- Collected posts appear in the data table under the agents table (click the
  agent's name). The **Fields** cell opens a read-only viewer.
- When some links produced nothing, the data pane shows
  **"Last run (completed): N of M links failed"** with a **View failure
  reasons** button that lists every failed link with its reason and detail —
  see the table below.
- The bell icon also logs the run summary, e.g.
  *Agent "NASA posts" completed in 12.4s (3 records) · 2 links failed*.

## End-to-end pipeline

```mermaid
sequenceDiagram
    participant UI as Desktop app
    participant API as FastAPI
    participant SEC as agent_secrets (Mongo)
    participant SP as FacebookPostSpider
    participant FB as mbasic.facebook.com
    participant DB as data/agents (Mongo)

    UI->>API: POST /agents (name, script, sourceType: facebook)
    UI->>API: PUT /agents/{id}/credentials (cookie paste, proxies)
    API->>SEC: parse + validate + Fernet-encrypt, store ciphertext

    UI->>API: GET /agents/{id}/run
    API->>API: validate script (https *.facebook.com links only)
    API->>API: atomic claim: status → Running
    API->>SEC: decrypt for this run (memory only)
    API->>SP: start crawl (cookies, proxies, gentle settings)
    loop every link
        SP->>SP: rewrite host to FACEBOOK_HOST (mbasic)
        SP->>FB: GET (cookies + round-robin proxy, 1 concurrent, delayed)
        alt soft failure
            SP->>SP: classify (login wall / checkpoint / 404 / 429 / …)
            SP->>DB: record failure reason on the run
        else post fetched
            SP->>SP: extract built-in / overridden fields
            SP->>DB: insert one data doc
        end
    end
    SP-->>API: crawl finished (or POST /agents/{id}/stop → graceful close)
    API->>DB: status → Completed / Stopped / Failed + lastRun summary
    API-->>UI: WS agentStatus frame (count, failures, data)
```

Key implementation files: `backend/app/services/facebook_spider.py` (spider,
URL rewriting, classification, built-in fields),
`backend/app/services/crawler_service.py` (run orchestration, `lastRun`,
stop), `backend/app/services/agent_secret_service.py` +
`backend/app/core/encryption.py` (credential storage),
`backend/app/models/crawl.py` (failure-reason constants).

## Failure reasons

Every link that produces no data document is recorded in the run's `lastRun`
with one of these reasons (constants in `backend/app/models/crawl.py`):

| Reason | Shown as | Meaning | What to do |
|---|---|---|---|
| `broken_link` | Broken link (404) | post deleted or wrong URL | remove/fix the link |
| `rate_limited` | Rate limited (429) | Facebook throttled the requests | raise `FACEBOOK_DOWNLOAD_DELAY`, run less often |
| `http_error` | HTTP error | any other non-2xx status (detail shows the code) | see detail — mbasic's bot wall often shows as HTTP 400; try adding cookies or a proxy |
| `login_redirect` | Login required (redirected) | landed on the login page although cookies were sent | the stored cookies expired — re-enter them |
| `cookie_missing` | Login cookies missing | the page needs a login and the agent has no cookies | paste cookies (c_user + xs) |
| `checkpoint` | Bot check / checkpoint | security check / CAPTCHA shown to the session | slow down, rotate proxies, or log in to the account in a browser and clear the checkpoint, then re-paste cookies |
| `timeout` | Timed out | no response in time | usually transient; retry |
| `dns_error` | Host not found (DNS) | bad domain (or proxy DNS failure) | check the link / proxy |
| `connection_error` | Connection failed | couldn't connect | network/proxy reachability |
| `proxy_error` | Proxy failed | connection failure through a configured proxy | replace the dead proxy |
| `unexpected_html` | Page fetched but nothing matched | 200 OK but no field extracted | selectors stale or page type unsupported — see [Maintenance](#maintenance) |
| `cancelled` | Cancelled | link never fetched because the run was stopped (or the server restarted) | re-run if you want the rest |
| `request_error` | Request failed | anything unrecognized (detail names the error) | see detail |

## Configuration & security notes

Environment variables in `backend/.env`:

| Variable | Default | Effect |
|---|---|---|
| `FACEBOOK_DOWNLOAD_DELAY` | `3` | seconds between requests (randomized ±50%) |
| `FACEBOOK_HOST` | `mbasic.facebook.com` | host links are rewritten onto |
| `CREDENTIALS_ENCRYPTION_KEY` | — | Fernet key encrypting stored cookies/proxies; blank/placeholder disables credential storage (API answers 503) |
| `CRAWL_TIMEOUT_SECONDS` | `600` | hard ceiling per run |
| `CRAWL_MAX_PAGES` | `200` | max links per run |

Pacing is deliberately gentle: **one** concurrent request per domain, a
randomized delay between requests, AutoThrottle, 2 retries, and a
realistic Chrome user agent.

Security properties:

- Cookies/proxies are stored **Fernet-encrypted** in a separate
  `agent_secrets` collection; no API route ever returns the values — the
  agent carries only `hasCookies`/`hasProxies` flags, and the edit dialog
  gets names/counts only.
- Plaintext exists only in memory during a run.
- Losing or changing `CREDENTIALS_ENCRYPTION_KEY` makes stored credentials
  undecryptable — the run endpoint then answers 503 with a message to
  re-enter them.
- Deleting an agent deletes its stored credentials with it.

## Maintenance

Facebook changes `mbasic` markup occasionally and serves it differently by
region. The signal: a run where **every** link reports `unexpected_html`
means the built-in selectors matched nothing — the markup changed (or that
page type isn't covered).

Fixes, cheapest first:

1. Add per-field XPath overrides on the agent (script keys) — no code change.
2. Update `_BUILT_IN_FIELDS` in `backend/app/services/facebook_spider.py`
   (each field is an ordered fallback list; test against a saved
   mbasic page's HTML).
3. If mbasic redirects your region to `m.facebook.com`, set
   `FACEBOOK_HOST=m.facebook.com` in `backend/.env` and adjust the selectors
   for that markup.

Also note: crawls run **in-process** on the API server — do not run uvicorn
with multiple workers, or the run registry and crawl tasks break.
