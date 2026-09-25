---
paths:
  - "app/api/**"
  - "app/schemas/**"
---

# API Surface Rules

## Endpoint inventory

| Method | Path | Auth | Body | Success | Errors |
|---|---|---|---|---|---|
| POST | `/api/v1/auth/signup` | — | `{username, email, password}` | `201 UserOut` | `409` dup email/username, `422` |
| POST | `/api/v1/auth/login` | — | `{email, password}` | `200 TokenPair` | `401` generic, `403` not active |
| POST | `/api/v1/auth/refresh` | — | `{refreshToken}` | `200 TokenPair` (both rotated) | `401` invalid/expired/reuse |
| POST | `/api/v1/auth/logout` | — | `{refreshToken}` | `204` idempotent | `422` |
| POST | `/api/v1/auth/change-password` | Bearer | `{currentPassword, newPassword}` | `200 TokenPair` | `401`, `403`, `400` same pw, `422` |
| GET | `/api/v1/users/me` | Bearer | — | `200 UserOut` | `401`, `403` |
| GET | `/api/v1/users` | Bearer (admin) | query: `limit` (1–100, def 50), `skip` (≥0, def 0) | `200 UserList` `{users, total}` | `401`, `403` |
| PATCH | `/api/v1/users/{userId}/status` | Bearer (admin) | `{status}` active/inactive/banned | `200 UserOut` | `401`, `403`, `400` self-target, `404`, `422` |
| PATCH | `/api/v1/users/{userId}/role` | Bearer (admin) | `{role}` admin/user | `200 UserOut` | `401`, `403`, `400` self-target, `404`, `422` |
| DELETE | `/api/v1/users/{userId}` | Bearer (admin) | — | `204` | `401`, `403`, `400` self-target, `404` |
| DELETE | `/api/v1/users` | Bearer (admin) | `{userIds}` (list, ≥1) | `200 {deleted: n}` | `401`, `403`, `400` self in list, `422` |
| POST | `/api/v1/agents` | Bearer | `{name, format, script, type?, sourceType?, status?}` | `201 AgentOut` | `400` invalid JSON script (or invalid source_pages structure — both pagination buttons, missing `source_pages`/`post_link`, bad `max_next`; or invalid ecommerce structure — bad `links`, both `max_next` spellings, bad `max_next`/`max_products`, non-XPath field), `409` dup name, `422` |
| GET | `/api/v1/agents` | Bearer | query: `limit` (1–100, def 50), `skip` (≥0, def 0) | `200 AgentList` `{agents, total}` | `401` |
| GET | `/api/v1/agents/{agentId}` | Bearer | — | `200 AgentOut` (carries `lastRun` when the agent has run) | `401`, `404` |
| PATCH | `/api/v1/agents/{agentId}` | Bearer | any of `{name, script, format, type, sourceType, status}` | `200 AgentOut` | `400` invalid JSON script (merged view; source_pages structure checked on the merged sourceType+script pair), `409` dup name, `404`, `422` |
| DELETE | `/api/v1/agents/{agentId}` | Bearer | — | `204` (its data docs' `agentId` → `null`; stored credentials deleted) | `401`, `404` |
| GET | `/api/v1/agents/{agentId}/run` | Bearer | — | `202 AgentOut` (status `Running`; crawl continues in the background) | `401`, `404`, `409` "Agent is already running", `400` script not runnable (non-json format, bad structure, > `CRAWL_MAX_PAGES` links, non-http(s) or — for `sourceType: facebook` — non-facebook links; source_pages: non-http(s) source pages, > `CRAWL_MAX_PAGES` source pages, same structure rules as create; ecommerce: non-http(s) seeds, > `CRAWL_MAX_PAGES` seeds, `max_products` > `ECOMMERCE_MAX_PRODUCTS`, same structure rules as create), `503` facebook credentials undecryptable |
| POST | `/api/v1/agents/{agentId}/stop` | Bearer | — | `202 AgentOut` (still `Running` in the body; Stopped lands via the WS broadcast after in-flight requests settle) | `401`, `404`, `409` "Agent is not running" |
| PUT | `/api/v1/agents/{agentId}/credentials` | Bearer | `{cookieHeader?, proxyText?}` (raw pastes; at least one) | `200 AgentOut` (`hasCookies`/`hasProxies` flags updated; values stored Fernet-encrypted, NEVER returned) | `400` unparseable cookies / bad proxy URL / >20 proxies / both empty, `404`, `503` `CREDENTIALS_ENCRYPTION_KEY` unset/invalid |
| GET | `/api/v1/agents/{agentId}/credentials-metadata` | Bearer | — | `200 {cookieNames: [...], proxyCount, updatedAt}` (non-secret summary) | `401`, `404` |
| DELETE | `/api/v1/agents/{agentId}/credentials` | Bearer | — | `200 AgentOut` (flags cleared; idempotent) | `401`, `404` |
| GET | `/api/v1/agents/{agentId}/data` | Bearer | query: `limit` (1–100, def 50), `skip` (≥0, def 0) | `200 DataList` `{data: [DataOut], total}` (newest first) | `401`, `404` "Agent not found" |
| GET | `/api/v1/data/orphaned` | Bearer | query: `limit` (1–100, def 50), `skip` (≥0, def 0) | `200 DataList` `{data: [DataOut], total}` (newest first; docs whose agent was deleted — `agentId` is `null`) | `401`, `422` |
| DELETE | `/api/v1/data` | Bearer | `{ids}` (list, ≥1) | `200 {deleted: n}` (unknown ids don't count) | `401`, `422` |
| DELETE | `/api/v1/data/{dataId}` | Bearer | — | `204` | `401`, `404` "Data not found" |
| WS | `/api/v1/notifications/ws` | query: `token` (access JWT) | — | ack `{"type":"connected"}`, then `{"type":"notification","message","createdAt"}` every `NOTIFICATION_INTERVAL_SECONDS` (def 900) plus `{"type":"agentStatus",...}` frames on agent runs (terminal Completed/Stopped/Failed frames carry `runtimeSeconds` and a `lastRun` summary with per-link `failures`; Completed/Stopped also carry `count` + `data`) | handshake rejection (close 1008 → HTTP 403) |

When adding/removing/changing an endpoint, update this table and the README.

## Conventions

- Routers: `APIRouter(prefix="/...", tags=[...])`, mounted in `app/main.py` under
  `prefix="/api/v1"`. Route functions are `async`.
- Routes stay thin: validate → call a function in `app/services/` → map to a response
  schema. No collection access directly from route files.
- Every route declares `response_model=` pointing at a schema in `app/schemas/`.
- JSON is camelCase: request/response models use
  `ConfigDict(alias_generator=to_camel, populate_by_name=True)` (see `_CamelModel`
  in `app/schemas/auth.py`). Python fields stay snake_case.
- Errors always use FastAPI's `{"detail": "..."}` shape via `HTTPException`.
- Admin endpoints guard via `AdminUser` (`Depends(get_current_admin)`, `app/api/deps.py`).
  Setting a user's status to anything but `active` also revokes all of that user's
  refresh tokens; deleting a user hard-deletes their refresh tokens (a replayed token
  then reads as unknown → 401, no cascade); admins cannot target their own account
  (400 self-guard — status, role, and delete).
- Password constraints live in `PASSWORD_FIELD` and `_check_password_bytes`
  (`app/schemas/auth.py`) — reuse them; never redefine per-endpoint rules.
- Agent endpoints are open to every authenticated user (`CurrentUser`). When the
  effective `format` is `json`, the effective `script` must parse via `json.loads` —
  validated in `agent_service._validate_script` on create and on the merged
  (old ∪ new) PATCH view (`400` otherwise); `xml`/`md` scripts are stored as-is.
  Agent names are unique (`409` on create and on rename). `updatedBy` is
  server-derived from the authenticated user (creator on create, patcher on
  update) — never accepted from the request body.
- `GET /agents/{agentId}/run` (`crawler_service.start_agent_crawl`) runs the
  agent's script as a background Scrapy crawl: run-time script validation
  (`_parse_run_script`) → atomic Running claim (`find_one_and_update` with
  `status != Running` — the refresh-rotation idiom; race loser gets `409`) →
  broadcast a `Running` frame → `asyncio.create_task(execute_crawl)`. The task
  registry `_running_crawls` (`_RunningCrawl`: task + runner + stop_requested)
  double-guards re-runs (PATCH-proof) and backs `POST .../stop`. Every link
  carries an errback that classifies download failures (`broken_link` 404,
  `rate_limited` 429, `timeout`, `dns_error`, `connection_error`,
  `proxy_error`, `cancelled`, … — constants in `app/models/crawl.py`). On
  finish: one `data` doc per crawled page, a terminal status of `Completed` /
  `Stopped` / `Failed`, a `lastRun` summary written ATOMICALLY with the status
  flip (`{startedAt, finishedAt, outcome, totalLinks, successCount,
  failureCount, failures: [{url, reason, detail?}]}`, capped at 100 entries),
  and a broadcast frame carrying it. A GET with side effects by explicit user
  request — safe from prefetchers because it requires Bearer auth. Startup
  sweep (`reset_interrupted_crawls`) flips restart-orphaned `Running` agents
  to `Failed` (with a `cancelled` lastRun entry). If the agent is deleted
  mid-run, the finishing crawl nulls its just-inserted docs' `agentId` so they
  still land as orphans. Quirk: PATCHing an agent's status *to* `Running`
  without a crawl soft-locks runs (`409`) until it is patched back.
- `POST /agents/{agentId}/stop` (`crawler_service.stop_agent_crawl`): marks
  the in-flight run `stop_requested`, stamps `stoppedBy`, and calls
  `crawler.stop()` on the live runner — a GRACEFUL close (in-flight requests
  settle first, bounded by `DOWNLOAD_TIMEOUT`), then `execute_crawl`
  finalizes as `Stopped` with partial data and `cancelled` failure entries
  for never-fetched links. Re-running after `Stopped` is allowed (the claim
  only excludes `Running`).
- Agents have `sourceType: "generic" | "facebook" | "source_pages" |
  "ecommerce"` (default `generic`). Generic agents run `AgentScriptSpider`
  (XPath script against any allowed http(s) site). Facebook agents run
  `FacebookPostSpider` (`app/services/facebook_spider.py`): links must be
  https `*.facebook.com` (rewritten onto `FACEBOOK_HOST`, default mbasic),
  script keys are OPTIONAL per-field XPath overrides of the built-in post
  fields (author, text, timestamp, reactions, comments, mediaUrls,
  permalink), per-request cookies and round-robin proxies come from the
  stored (encrypted) credentials, and responses are classified before
  extraction (`login_redirect`, `cookie_missing`, `checkpoint`,
  `rate_limited`, `unexpected_html` when nothing matched). Facebook runs use
  a gentle settings overlay (browser UA, 1 concurrent request,
  `FACEBOOK_DOWNLOAD_DELAY` randomized, AutoThrottle).
  source_pages agents (`app/services/source_pages_discovery.py`) carry a
  different script contract — `source_pages` (listing URLs) + `post_link`
  (article-link XPaths, unioned/deduped) + optional `next_page` XOR
  `load_more` (button XPath to CLICK; both → 400 on create AND run) +
  optional `max_next` (int ≥ 1; `max_next_page` alias) — and every other key
  is a field XPath evaluated on each DISCOVERED article via the regular
  `AgentScriptSpider`. Structure is validated at write time
  (`agent_service._validate_script` branches on sourceType) and re-validated
  at run start (`_parse_source_pages_run_script`: http(s) source pages,
  `CRAWL_MAX_PAGES` cap). Runs execute in two phases inside `execute_crawl`:
  a Playwright headless-chromium discovery (randomized 3–120 s delays on
  listing navigations only, stop-aware, bounded by
  `SOURCE_PAGES_TIMEOUT_SECONDS`; discovered links capped at
  `CRAWL_MAX_PAGES`), then the generic crawl with the remaining budget.
  Discovery failures reuse the `lastRun.failures` vocabulary (a failing
  source page records one entry; other pages continue; a missing button is
  a NORMAL end of pagination, not a failure). Playwright is imported lazily
  — without `playwright install chromium` the app boots and a source_pages
  run finalizes as Failed with the install hint.
  ecommerce agents (`app/services/ecommerce_spider.py`, strategy doc
  `ECOMMERCE_CRAWLER_STRATEGY.md`) crawl a product catalog autonomously:
  seeds are listing/category URLs (`links`); the spider discovers product
  links (`product_link` XPath, books.toscrape built-in), follows the
  listing `next_page` link automatically (absent link = normal end), and
  extracts built-in product fields on each detail page (title, price,
  currency, availability, rating, category, imageUrl, description,
  productUrl — script keys override per field, transforms still apply).
  Caps: `max_next` (pagination per seed, `max_next_page` alias) and
  `max_products` (unique products; default/ceiling
  `ECOMMERCE_MAX_PRODUCTS`); reaching the product cap stops discovery and
  DRAINS the queued requests (exact cap, one `cancelled` entry). Discovered
  links resolve against the page URL (relative-href trap) and only on the
  seeds' exact hostnames. Structure is validated at write time and run
  start (`_parse_ecommerce_run_script`: http(s) seeds, `CRAWL_MAX_PAGES`
  seed cap, `max_products` ceiling). Runs use a politeness overlay (Chrome
  UA, `ECOMMERCE_DOWNLOAD_DELAY` randomized, 2 concurrent,
  AutoThrottle; no Playwright). `lastRun.totalLinks` counts discovered
  unique products; a page/time-budget truncation records one `cancelled`
  entry. Static-HTML shops only — JS-rendered catalogs need source_pages.
- Credentials (`agent_secret_service`): raw cookie-header / proxy-list pastes
  are parsed once, validated, and stored Fernet-encrypted
  (`CREDENTIALS_ENCRYPTION_KEY`, `app/core/encryption.py`) in the
  `agent_secrets` collection keyed by the agent `_id`. No route returns the
  values — `AgentOut` carries only `hasCookies`/`hasProxies`; the edit dialog
  gets names/counts via `GET .../credentials-metadata`. Deleting an agent
  cascades the secret doc.
- Signup roles: the FIRST signup (empty users collection) becomes `admin`;
  every later signup is a regular `user`.
- Data endpoints are open to every authenticated user (`CurrentUser`). Deleting
  an agent soft-orphans its crawl results (`agent_service.delete_agent` →
  `data_service.detach_from_agent`: `agentId` → `null` on every data doc,
  `agentName` kept for display), so the per-agent listing 404s once the agent
  is gone while `GET /data/orphaned` (`data_service.list_orphaned`) lists the
  survivors — `find({"agentId": None})` + `count_documents` like every other
  listing (the null equality is served by `idx_agent_crawled`, unlike the
  `$nin` anti-join it replaced). It never 404s (an empty page just means
  nothing is orphaned) and a recreated same-name agent never relinks orphans
  (fresh `_id`; `null` is never re-populated). A startup sweep
  (`data_service.detach_dangling_agents`) nulls any `agentId` matching no live
  agent. Declared before any
  `GET /data/{dataId}` route would be (path-literal before path-param).
- WS broadcast frames are flat camelCase dicts via the shared
  `connection_manager.manager` (registered by the notifications route):
  Running `{"type":"agentStatus","agentId","agentName","status":"Running","createdAt"}`;
  Completed adds `"runtimeSeconds"` (float, 1 decimal), `"count"` and `"data"`
  (DataOut-shaped); Failed adds `"runtimeSeconds"` and `"message"`. Broadcasts
  reach every connected client.
- WebSocket routes (`app/api/routes/notifications.py`): the access token rides
  in the `token` query param (QWebSocket cannot set handshake headers) and is
  validated at the handshake by `_handshake_user` (decode → `type == "access"`
  → `user_service.get_by_id` → active). Rejections must `websocket.close(1008)`
  BEFORE `accept()` — `HTTPException` does not work in WS routes. Frames are
  plain camelCase dicts via `send_json` (`response_model=` doesn't apply). The
  push loop is per-connection (`asyncio.sleep(interval)`); without a concurrent
  `receive()` a dead client is only reaped when the next send fails.

## Status-code semantics (keep consistent)

- `401`: missing/invalid/expired credential — include `WWW-Authenticate: Bearer` header
- `403`: authenticated but not allowed (wrong current password, banned account, non-admin)
- `409`: unique-constraint conflict (`DuplicateKeyError` → distinct email vs username message)
- `422`: request validation (let Pydantic produce these; don't hand-roll)
- `400`: semantically valid but rejected request (e.g. new password equals current,
  admin targeting their own account)
- `404`: path parameter matches no document (e.g. unknown `{userId}`)
