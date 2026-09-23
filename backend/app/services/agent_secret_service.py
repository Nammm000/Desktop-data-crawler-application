"""Storage for per-agent crawl credentials (Facebook cookies, proxy list).

Cookies and proxies arrive as raw text, are parsed and validated ONCE here,
and are stored Fernet-encrypted in a dedicated collection. The agent doc
only carries boolean flags (hasCookies / hasProxies) so listings and the API
surface can never leak a secret — the plaintext exists only inside
execute_crawl's stack frame after `decrypt_for_run`."""

from datetime import datetime, timezone

from fastapi import HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from app.core import encryption
from app.models.agent import AGENTS_COLLECTION, AGENT_SECRETS_COLLECTION

_MAX_COOKIE_NAMES = 30  # metadata shown back to the user (rest summarized)
_MAX_PROXIES = 20


def parse_cookie_header(raw: str) -> dict[str, str]:
    """Tolerant parser for a pasted Cookie header: accepts `a=1; b=2` and
    newline-separated pairs; strips whitespace; drops empties and duplicates
    (last wins, like a browser). Raises 400 when nothing usable remains."""
    cookies: dict[str, str] = {}
    for chunk in raw.replace("\n", ";").split(";"):
        name, sep, value = chunk.strip().partition("=")
        if not sep:
            continue
        name = name.strip()
        value = value.strip().strip('"')
        if name and value:
            cookies[name] = value
    if not cookies:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No valid name=value cookie pairs found",
        )
    return cookies


def parse_proxy_list(raw: str) -> list[str]:
    """One proxy URL per line; http/https with an optional host:port and
    optional user:pass. Raises 400 on any invalid line."""
    proxies: list[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        if not (line.startswith("http://") or line.startswith("https://")):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Proxy '{line}' must be an http:// or https:// URL "
                "(e.g. http://user:pass@host:port)",
            )
        proxies.append(line)
    if not proxies:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No proxy URLs found",
        )
    if len(proxies) > _MAX_PROXIES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"At most {_MAX_PROXIES} proxies can be stored",
        )
    return proxies


async def set_credentials(
    db: AsyncIOMotorDatabase,
    agent_id: str,
    acting_email: str,
    *,
    cookie_header: str | None,
    proxy_text: str | None,
) -> dict:
    """Upsert the agent's secret doc and sync the agent's boolean flags.
    Exactly one of the two payloads must be non-empty."""
    if not cookie_header and not proxy_text:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide cookies, proxies, or both",
        )

    cookies = parse_cookie_header(cookie_header) if cookie_header else None
    proxies = parse_proxy_list(proxy_text) if proxy_text else None

    cookie_names: list[str] = []
    if cookies is not None:
        cookie_names = list(cookies)[:_MAX_COOKIE_NAMES]

    try:
        cookies_enc = encryption.encrypt(cookie_header) if cookies else ""
        proxies_enc = encryption.encrypt(proxy_text) if proxies else ""
    except encryption.CredentialsKeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc

    now = datetime.now(timezone.utc)
    await db[AGENT_SECRETS_COLLECTION].update_one(
        {"_id": agent_id},
        {
            "$set": {
                "cookiesEnc": cookies_enc,
                "cookieNames": cookie_names,
                "proxiesEnc": proxies_enc,
                "proxyCount": len(proxies) if proxies else 0,
                "updatedAt": now,
                "updatedBy": acting_email,
            }
        },
        upsert=True,
    )
    flags = {
        "hasCookies": cookies is not None,
        "hasProxies": proxies is not None,
    }
    return await db[AGENTS_COLLECTION].find_one_and_update(
        {"_id": agent_id},
        {"$set": {**flags, "updatedAt": now, "updatedBy": acting_email}},
        return_document=ReturnDocument.AFTER,
    )


async def clear_credentials(
    db: AsyncIOMotorDatabase, agent_id: str, acting_email: str
) -> dict:
    """Delete the secret doc and drop the agent's flags (idempotent)."""
    await db[AGENT_SECRETS_COLLECTION].delete_one({"_id": agent_id})
    now = datetime.now(timezone.utc)
    return await db[AGENTS_COLLECTION].find_one_and_update(
        {"_id": agent_id},
        {
            "$set": {
                "hasCookies": False,
                "hasProxies": False,
                "updatedAt": now,
                "updatedBy": acting_email,
            }
        },
        return_document=ReturnDocument.AFTER,
    )


async def delete_for_agent(db: AsyncIOMotorDatabase, agent_id: str) -> None:
    """Cascade on agent deletion (no flag sync — the agent is gone)."""
    await db[AGENT_SECRETS_COLLECTION].delete_one({"_id": agent_id})


async def get_metadata(db: AsyncIOMotorDatabase, agent_id: str) -> dict | None:
    """Non-secret summary (cookie names, counts) for the edit dialog."""
    return await db[AGENT_SECRETS_COLLECTION].find_one(
        {"_id": agent_id},
        {"cookieNames": 1, "proxyCount": 1, "updatedAt": 1},
    )


async def decrypt_for_run(
    db: AsyncIOMotorDatabase, agent_id: str
) -> tuple[dict[str, str] | None, list[str] | None]:
    """Crawl-time read: decrypt and rebuild (cookies, proxies). A missing doc
    or an empty side yields None for that side. Raises 503 on key problems —
    the run endpoint turns that into a Failed run with the reason."""
    doc = await db[AGENT_SECRETS_COLLECTION].find_one({"_id": agent_id})
    if doc is None:
        return None, None
    cookies = (
        parse_cookie_header(encryption.decrypt(doc["cookiesEnc"]))
        if doc.get("cookiesEnc")
        else None
    )
    proxies = (
        parse_proxy_list(encryption.decrypt(doc["proxiesEnc"]))
        if doc.get("proxiesEnc")
        else None
    )
    return cookies, proxies
