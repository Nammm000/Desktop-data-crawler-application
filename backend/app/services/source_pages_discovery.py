"""source_pages agents: listing-page link discovery via Playwright.

Phase 1 of a source_pages run — a plain-asyncio Playwright coroutine opens
each source page in headless chromium, collects article links through the
`post_link` XPaths, and walks pagination by CLICKING (next_page / load_more
buttons — JS interaction Scrapy cannot do), with randomized human-like
delays between listing-page navigations. Phase 2 (in crawler_service) feeds
the discovered links to the generic AgentScriptSpider unchanged.

Why plain playwright and not scrapy-playwright: the app runs Scrapy 2.19
with TWISTED_REACTOR_ENABLED=False (pure asyncio); scrapy-playwright's
handlers are built on the Twisted path and never engage there. Playwright is
imported lazily inside `discover_links` so the API still boots (and
generic/facebook agents still run) without it installed."""

from __future__ import annotations

import asyncio
import dataclasses
import random
import time
from urllib.parse import urldefrag, urljoin, urlparse

from fastapi import HTTPException, status

from app.core.config import Settings
from app.models.crawl import FailureReason

# The four config keys that shape discovery (max_next_page is a tolerated
# alias) — never data fields, even though their values are strings/lists too.
RESERVED_KEYS = (
    "source_pages",
    "post_link",
    "next_page",
    "load_more",
    "max_next",
    "max_next_page",
)

CHROME_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

# Runs document.evaluate in the page: attribute/text nodes yield their value
# (post_link XPaths commonly end in /@href), <a> elements yield their
# already-resolved absolute href, anything else yields its text content.
_EXTRACT_XPATH_JS = """
(xpath) => {
    const result = document.evaluate(
        xpath, document, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
    const values = [];
    for (let i = 0; i < result.snapshotLength; i++) {
        const node = result.snapshotItem(i);
        if (node.nodeType === Node.ATTRIBUTE_NODE ||
                node.nodeType === Node.TEXT_NODE) {
            values.push(node.nodeValue);
        } else if (node.nodeType === Node.ELEMENT_NODE &&
                   node.tagName === "A") {
            values.push(node.href);
        } else if (node.nodeType === Node.ELEMENT_NODE) {
            values.push(node.textContent);
        }
    }
    return values;
}
"""


@dataclasses.dataclass(frozen=True)
class SourcePagesPlan:
    """Normalized source_pages script (see parse_source_pages_script)."""

    source_pages: list[str]
    post_link_xpaths: list[str]
    mode: str  # "single" | "next_page" | "load_more"
    button_xpaths: list[str]  # [] in single mode
    max_next: int | None  # None = while the button exists
    field_xpaths: dict[str, list[str]]


@dataclasses.dataclass
class DiscoveryOutcome:
    """What discover_links found. `stopped` is True when a stop request cut
    the discovery short (the caller must not start the article crawl)."""

    links: list[str]  # unique, in discovery order
    failures: list[dict]  # lastRun-shaped entries
    stopped: bool


def _as_xpath_list(label: str, value) -> list[str]:
    """Normalize a script value to a list of non-empty XPath strings
    (`label` is quoted/positioned by the caller: "'post_link'" for the
    reserved keys, "field 'title'" for data fields)."""
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    if (
        isinstance(value, list)
        and value
        and all(isinstance(v, str) and v.strip() for v in value)
    ):
        return [v.strip() for v in value]
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"script {label} must be a non-empty XPath string or a "
        "non-empty list of XPath strings",
    )


def parse_source_pages_script(parsed: dict) -> SourcePagesPlan:
    """Pure structural validation/normalization of an already-parsed
    source_pages script. Raises 400 with the same message style as
    crawler_service._parse_run_script; safe to call from write-time
    validation (agent_service) and run-time validation alike."""
    source_pages = parsed.get("source_pages")
    if (
        not isinstance(source_pages, list)
        or not source_pages
        or not all(isinstance(u, str) and u.strip() for u in source_pages)
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="script 'source_pages' must be a non-empty list of URL "
            "strings",
        )
    post_link_xpaths = _as_xpath_list("'post_link'", parsed.get("post_link"))

    has_next = "next_page" in parsed
    has_more = "load_more" in parsed
    if has_next and has_more:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="script cannot contain both 'next_page' and 'load_more' "
            "- choose one pagination mode",
        )
    button_xpaths: list[str] = []
    mode = "single"
    if has_next:
        button_xpaths = _as_xpath_list("'next_page'", parsed["next_page"])
        mode = "next_page"
    elif has_more:
        button_xpaths = _as_xpath_list("'load_more'", parsed["load_more"])
        mode = "load_more"

    has_max = "max_next" in parsed
    has_max_alias = "max_next_page" in parsed
    if has_max and has_max_alias:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="script cannot contain both 'max_next' and 'max_next_page'",
        )
    max_next = None
    if has_max or has_max_alias:
        raw = parsed["max_next" if has_max else "max_next_page"]
        # bool is an int subclass — reject True/False explicitly.
        if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="script 'max_next' must be an integer of at least 1",
            )
        max_next = raw

    field_xpaths: dict[str, list[str]] = {}
    for key, value in parsed.items():
        if key in RESERVED_KEYS:
            continue
        field_xpaths[key] = _as_xpath_list(f"field '{key}'", value)
    return SourcePagesPlan(
        source_pages=[u.strip() for u in source_pages],
        post_link_xpaths=post_link_xpaths,
        mode=mode,
        button_xpaths=button_xpaths,
        max_next=max_next,
        field_xpaths=field_xpaths,
    )


def normalize_discovered_href(base_url: str, value) -> str | None:
    """Resolve one raw discovered value against the page URL and keep it
    only when it is a usable http(s) link: relative hrefs are joined,
    fragments dropped (dedup: /post#comments IS /post), and javascript:/
    mailto:/blank values discarded. None means "not a crawlable link"."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or value.startswith("#"):
        return None
    joined = urljoin(base_url, value)
    parsed = urlparse(joined)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    return urldefrag(joined).url


def _classify_navigation_error(
    exc: BaseException, http_status: int | None = None
) -> tuple[str, str | None]:
    """Map a Playwright navigation/load failure to (FailureReason, human
    detail) — the discovery twin of crawler_service._classify_download_error
    (string-classified for the same stability reasons)."""
    if http_status is not None:
        if http_status == 404:
            return FailureReason.BROKEN_LINK, None
        if http_status == 429:
            return FailureReason.RATE_LIMITED, None
        return FailureReason.HTTP_ERROR, f"HTTP {http_status}"
    name = type(exc).__name__
    message = str(exc)
    if "Timeout" in name or "Timeout" in message:
        return FailureReason.TIMEOUT, None
    if "ERR_NAME_NOT_RESOLVED" in message or "ERR_INTERNET_DISCONNECTED" in message:
        return FailureReason.DNS_ERROR, None
    if any(
        token in message
        for token in (
            "ERR_CONNECTION_REFUSED",
            "ERR_CONNECTION_RESET",
            "ERR_CONNECTION_CLOSED",
            "ERR_ADDRESS_UNREACHABLE",
            "ERR_NETWORK_CHANGED",
            "ERR_NETWORK_ACCESS_REVOKED",
        )
    ):
        return FailureReason.CONNECTION_ERROR, name
    return FailureReason.REQUEST_ERROR, f"{name}: {message}" if message else name


def source_pages_settings(
    base: dict, *, download_delay: float, timeout_seconds: float
) -> dict:
    """Overlay for the article-crawl phase of a source_pages run: same
    gentleness ideas as facebook_settings, plus the REMAINING overall-run
    budget as CLOSESPIDER_TIMEOUT (discovery already spent part of it)."""
    overlay = dict(base)
    overlay.update(
        {
            # The listing pages were fetched by chromium under this UA — the
            # article requests must not switch identity mid-site.
            "USER_AGENT": CHROME_USER_AGENT,
            "CONCURRENT_REQUESTS_PER_DOMAIN": 1,
            "DOWNLOAD_DELAY": download_delay,
            "RANDOMIZE_DOWNLOAD_DELAY": True,  # 0.5–1.5x the delay
            "CLOSESPIDER_TIMEOUT": timeout_seconds,
        }
    )
    return overlay


class _StopDiscovery(Exception):
    """Internal: raised to unwind everything when a stop is requested."""


async def discover_links(
    plan: SourcePagesPlan,
    *,
    settings: Settings,
    should_stop,  # callable[[], bool] — wired to the _RunningCrawl entry
    deadline: float,  # time.monotonic() instant the whole run must end by
) -> DiscoveryOutcome:
    """Drive headless chromium through the plan's source pages and collect
    unique article links. Never raises for per-page problems (they land in
    `failures`); a stop request ends discovery early with stopped=True. Only
    a Playwright/chromium launch failure propagates (the run finalizes as
    Failed with the install hint)."""
    from playwright.async_api import async_playwright  # lazy — see module doc

    links: list[str] = []
    seen: set[str] = set()
    failures: list[dict] = []
    bad_xpaths: set[str] = set()  # record a broken XPath once, not per page

    def _check_stop() -> None:
        if should_stop():
            raise _StopDiscovery()

    async def _polite_sleep() -> None:
        """Randomized listing-page delay, sliced into 1s checks so a stop
        request lands within ~1s instead of after the full 2 minutes."""
        remaining = random.uniform(
            settings.source_pages_delay_min_seconds,
            settings.source_pages_delay_max_seconds,
        )
        while remaining > 0:
            _check_stop()
            chunk = min(1.0, remaining)
            await asyncio.sleep(chunk)
            remaining -= chunk

    async def _collect(page) -> int:
        """Extract through every post_link XPath; return how many NEW unique
        links were added. A JS-throwing XPath is recorded once and skipped."""
        page_url = page.url
        added = 0
        for xp in plan.post_link_xpaths:
            try:
                values = await page.evaluate(_EXTRACT_XPATH_JS, xp)
            except Exception:
                if xp not in bad_xpaths:
                    bad_xpaths.add(xp)
                    failures.append(
                        {
                            "url": page_url,
                            "reason": FailureReason.REQUEST_ERROR,
                            "detail": f"post_link XPath failed to evaluate: {xp}",
                        }
                    )
                continue
            for raw in values:
                href = normalize_discovered_href(page_url, raw)
                if href is not None and href not in seen:
                    seen.add(href)
                    links.append(href)
                    added += 1
        return added

    async def _find_button(page):
        """First button XPath that matches an element (attribute-selecting
        XPaths simply never match — buttons are elements)."""
        for xp in plan.button_xpaths:
            try:
                locator = page.locator(f"xpath={xp}")
                if await locator.count() > 0:
                    return locator.first
            except Exception:
                continue  # locator-invalid XPath — try the next one
        return None

    capped = False
    timed_out = False
    try:
        async with async_playwright() as pw:
            try:
                browser = await pw.chromium.launch(headless=True)
            except Exception as exc:
                raise RuntimeError(
                    "Playwright chromium unavailable - run "
                    "'.venv/bin/playwright install chromium'"
                ) from exc
            try:
                for source_url in plan.source_pages:
                    _check_stop()
                    if time.monotonic() >= deadline:
                        timed_out = True
                        break
                    # Fresh context per source page: no cookie/state bleed
                    # between unrelated sites.
                    context = await browser.new_context(
                        user_agent=CHROME_USER_AGENT,
                        viewport={"width": 1366, "height": 768},
                    )
                    try:
                        page = await context.new_page()
                        try:
                            response = await page.goto(
                                source_url,
                                wait_until="domcontentloaded",
                                timeout=30_000,
                            )
                        except Exception as exc:
                            reason, detail = _classify_navigation_error(exc)
                            failures.append(
                                {
                                    "url": source_url,
                                    "reason": reason,
                                    "detail": "source page failed to load: "
                                    f"{detail or exc}",
                                }
                            )
                            continue
                        http_status = response.status if response else None
                        if http_status is not None and http_status >= 400:
                            reason, detail = _classify_navigation_error(
                                Exception(), http_status
                            )
                            failures.append(
                                {
                                    "url": source_url,
                                    "reason": reason,
                                    "detail": "source page failed to load: "
                                    f"{detail}",
                                }
                            )
                            continue
                        try:  # best-effort settle for late-loading listings
                            await page.wait_for_load_state(
                                "networkidle", timeout=10_000
                            )
                        except Exception:
                            pass
                        # Human-like think time after the initial load too —
                        # delays apply to EVERY listing-page navigation.
                        await _polite_sleep()

                        if plan.mode == "next_page":
                            visited: set[str] = {urldefrag(page.url).url}
                            navigations = 0
                            while True:
                                await _collect(page)
                                if len(links) >= settings.crawl_max_pages:
                                    capped = True
                                    break
                                if (
                                    plan.max_next is not None
                                    and navigations >= plan.max_next
                                ):
                                    break
                                if time.monotonic() >= deadline:
                                    timed_out = True
                                    break
                                _check_stop()
                                button = await _find_button(page)
                                if button is None:
                                    break  # last page — normal end
                                try:
                                    await button.click(timeout=5_000)
                                except Exception as exc:
                                    reason, detail = _classify_navigation_error(
                                        exc
                                    )
                                    failures.append(
                                        {
                                            "url": page.url,
                                            "reason": reason,
                                            "detail": "next_page button "
                                            f"click failed: {detail or exc}",
                                        }
                                    )
                                    break
                                try:  # SPA next-buttons may not navigate
                                    await page.wait_for_load_state(
                                        "domcontentloaded", timeout=30_000
                                    )
                                except Exception:
                                    pass
                                await _polite_sleep()
                                listing_url = urldefrag(page.url).url
                                if listing_url in visited:
                                    # Circular pagination guard.
                                    break
                                visited.add(listing_url)
                                navigations += 1
                        else:  # single | load_more
                            await _collect(page)
                            clicks = 0
                            while plan.mode == "load_more":
                                if len(links) >= settings.crawl_max_pages:
                                    capped = True
                                    break
                                if (
                                    plan.max_next is not None
                                    and clicks >= plan.max_next
                                ):
                                    break
                                if time.monotonic() >= deadline:
                                    timed_out = True
                                    break
                                _check_stop()
                                button = await _find_button(page)
                                if button is None:
                                    break
                                try:
                                    await button.click(timeout=5_000)
                                except Exception as exc:
                                    reason, detail = _classify_navigation_error(
                                        exc
                                    )
                                    failures.append(
                                        {
                                            "url": page.url,
                                            "reason": reason,
                                            "detail": "load_more button "
                                            f"click failed: {detail or exc}",
                                        }
                                    )
                                    break
                                try:  # let the newly loaded posts arrive
                                    await page.wait_for_load_state(
                                        "networkidle", timeout=10_000
                                    )
                                except Exception:
                                    pass
                                await _polite_sleep()
                                added = await _collect(page)
                                clicks += 1
                                if plan.max_next is None and added == 0:
                                    # No max_next and the click surfaced
                                    # nothing new — the list is exhausted.
                                    break
                        if capped or timed_out:
                            break
                    finally:
                        await context.close()
            finally:
                await browser.close()
    except _StopDiscovery:
        return DiscoveryOutcome(links=links, failures=failures, stopped=True)

    if capped:
        failures.append(
            {
                "url": None,
                "reason": FailureReason.CANCELLED,
                "detail": f"discovery stopped at {settings.crawl_max_pages} "
                "unique links (CRAWL_MAX_PAGES)",
            }
        )
    if timed_out:
        failures.append(
            {
                "url": None,
                "reason": FailureReason.TIMEOUT,
                "detail": "run deadline reached during link discovery; "
                f"crawling {len(links)} discovered links",
            }
        )
    return DiscoveryOutcome(links=links, failures=failures, stopped=False)
