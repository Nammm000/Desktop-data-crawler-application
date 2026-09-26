# Verification Rules

Before finishing any change to auth, users, or tokens, verify end-to-end against
the running server (MongoDB up, uvicorn on :8000):

```bash
BASE=http://localhost:8000/api/v1
curl -s $BASE/../health                                  # {"status":"ok","database":"up"}

# happy path
curl -s -X POST $BASE/auth/signup -H 'Content-Type: application/json' -d '{"username":"smoketest","email":"smoketest@example.com","password":"S3curePass!"}'   # 201
RESP=$(curl -s -X POST $BASE/auth/login -H 'Content-Type: application/json' -d '{"email":"smoketest@example.com","password":"S3curePass!"}')
curl -s $BASE/users/me -H "Authorization: Bearer <accessToken from $RESP>"               # 200
curl -s -X POST $BASE/auth/refresh -H 'Content-Type: application/json' -d '{"refreshToken":"<from $RESP>"}'  # 200, both tokens rotate

# rotation security: replaying the ORIGINAL refresh token after refreshing
# -> 401 "Refresh token reuse detected; all sessions revoked", and the rotated
#    token must ALSO be dead (family revoked)

# logout
curl -i -X POST $BASE/auth/logout -H 'Content-Type: application/json' -d '{"refreshToken":"..."}'   # 204; refresh afterwards -> 401

# change password -> 200 fresh pair; old password login -> 401; old refresh tokens dead
```

Spot-check edge cases: duplicate signup `409`, short/multibyte-overflow password
`422`, garbage Bearer `401`, ban via
`db.users.updateOne({username:"smoketest"},{$set:{status:"banned"}})` → `/users/me`
`403` (then restore `status:"active"` or delete the smoketest user).

Clean up test users afterwards:
`db.users.deleteOne({username:"smoketest"})` (refresh tokens TTL out on their own).

## Source-pages agents (sourceType "source_pages")

```bash
# Run uvicorn with fast delays for manual testing:
SOURCE_PAGES_DELAY_MIN_SECONDS=0.5 SOURCE_PAGES_DELAY_MAX_SECONDS=2 \
  .venv/bin/uvicorn app.main:app --port 8000
# (requires the one-time: .venv/bin/playwright install chromium)

# 400 on create: both pagination buttons
curl -s -X POST $BASE/agents -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{
    "name":"sp-bad","format":"json","sourceType":"source_pages",
    "script":"{\"source_pages\":[\"https://example.com\"],\"post_link\":\"//a/@href\",
               \"next_page\":\"//a\",\"load_more\":\"//b\"}"}'
# -> 400 "script cannot contain both 'next_page' and 'load_more' - choose one pagination mode"

# 201 happy path, then run: SSE stream (start -> document events as pages
# are saved -> done with the Completed summary)
curl -s -X POST $BASE/agents -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{
    "name":"sp-ok","format":"json","sourceType":"source_pages",
    "script":"{\"source_pages\":[\"https://e.vnexpress.net/news/tech/tech-news\"],
               \"post_link\":\"//h2/a/@href\",\"max_next\":1,
               \"title\":[\"//meta[@property=\'og:title\']/@content\",\"//title\"]}"}'
curl -sN $BASE/agents/<agentId>/run -H "Authorization: Bearer $TOKEN"    # SSE
# PATCHing a generic agent to sourceType source_pages keeping a links-style
# script also 400s (merged-view structure check)
```
